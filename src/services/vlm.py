import asyncio
import inspect
import json
import orjson
from collections.abc import AsyncGenerator
from dataclasses import dataclass
import time
from datetime import datetime
from typing import Annotated, Awaitable, Dict, Any, Optional, Union, Tuple, cast

import aiohttp
from aiohttp import ClientTimeout
from fastapi import Depends, Form, Request

from src.api_models.chat_app.req_model import ChatLogAddReq
from src.base.constants.const import (
    LEGACY_KEY_NAMESPACE_MODULE,
    USER_AI_MODEL_SET_PREFIX,
    build_governed_key_prefix,
)
from src.base.exceptions import HttpException
from src.base.logging import logger
from src.base.utils import context_util
from src.base.utils.uuid_util import UUIDUtil
from src.config import config
from src.dao.redis import RedisManager
from src.dao.redis.managers.chat_log import ChatLogRedisManager
from urllib.parse import urljoin
from src.api_models.api_key.resp_model import CodePlanApiKeyPermResp
from src.api_models.vlm.protocol import TranscriptionRequest



from src.services.api_key import ApiKeyService
from src.services.concurrency_limiter import ConcurrencyLimiterService
from src.utils.http_client import AsyncHttpClient, RequestWrapper, HttpErrorWithContent, RequestResult, CancelBehavior, RequestStatus
from src.utils.sse_proxy_client import SseProxyClient


@dataclass(frozen=True)
class LimiterResolutionPolicy:
    subject_key: str
    subject_source: str
    user_total_limit: Optional[int]
    user_model_limit: Optional[int]
    limit_source: str


class VlmService:
    """
    VLM代理且记录调用日志服务
    """


    _proxy_client: SseProxyClient | None = None
    _limiter_service: ConcurrencyLimiterService | None = None

    @classmethod
    async def startup(cls):
        """初始化共享的SSE代理客户端"""
        if cls._proxy_client is None:
            cls._proxy_client = SseProxyClient(result_retention_seconds=60)
            logger.info("Initialized shared SseProxyClient for VLMService")

    @classmethod
    async def shutdown(cls):
        """关闭共享的SSE代理客户端"""
        if cls._proxy_client is not None:
            await cls._proxy_client.close()
            cls._proxy_client = None
            logger.info("Closed shared SseProxyClient for VLMService")
        if cls._limiter_service is not None:
            await cls._limiter_service.close()
            cls._limiter_service = None
            logger.info("Closed shared ConcurrencyLimiterService for VlmService")

    @classmethod
    async def get_sse_proxy_client(cls) -> SseProxyClient:
        """获取共享的SSE代理客户端"""
        if cls._proxy_client is None:
            raise HttpException("SseProxyClient is not initialized","500")
        return cls._proxy_client

    @classmethod
    def _get_concurrency_limiter_service(cls) -> ConcurrencyLimiterService:
        if cls._limiter_service is None:
            cls._limiter_service = ConcurrencyLimiterService()
        return cls._limiter_service

    @classmethod
    def _get_limiter_lease_ttl_ms(cls) -> int:
        ttl_ms = cls._normalize_limit_value(config.LIMITER_LEASE_TTL_MS)
        if ttl_ms is not None:
            return ttl_ms
        return 900_000

    @classmethod
    async def _acquire_pre_submit_limiter(
        cls,
        *,
        limiter_policy: LimiterResolutionPolicy,
        model_name: str,
    ) -> Dict[str, Any]:
        req_id = context_util.REQUEST_ID.get() or "-"
        trace_id = context_util.TRACE_ID.get() or "-"
        limiter_request_id = UUIDUtil.generate_uuid_v4()
        limiter_context: Dict[str, Any] = {
            "subject_key": limiter_policy.subject_key,
            "subject_source": limiter_policy.subject_source,
            "model_name": model_name,
            "request_id": limiter_request_id,
            "limit_source": limiter_policy.limit_source,
            "release_required": False,
        }

        logger.debug(
            "Limiter acquire.start, "
            f"req_id={req_id}, trace_id={trace_id}, subject={limiter_policy.subject_key or '-'}, "
            f"model={model_name or '-'}, policy={limiter_policy.limit_source}, mode={config.LIMITER_MODE}, "
            f"request_id={limiter_request_id}"
        )

        user_total_limit = cls._normalize_limit_value(limiter_policy.user_total_limit)
        user_model_limit = cls._normalize_limit_value(limiter_policy.user_model_limit)
        if not limiter_policy.subject_key or user_total_limit is None or user_model_limit is None:
            limiter_context.update(
                {
                    "status": "skipped",
                    "reason": "incomplete_policy",
                    "allowed": True,
                }
            )
            logger.debug(
                "Limiter acquire.result, "
                f"req_id={req_id}, trace_id={trace_id}, subject={limiter_policy.subject_key or '-'}, "
                f"model={model_name or '-'}, policy={limiter_policy.limit_source}, status=skipped, "
                "reason=incomplete_policy, release=skip"
            )
            return limiter_context

        limiter_context.update(
            {
                "user_total_limit": user_total_limit,
                "user_model_limit": user_model_limit,
                "ttl_ms": cls._get_limiter_lease_ttl_ms(),
            }
        )

        acquire_result = await cls._get_concurrency_limiter_service().acquire_with_mode(
            user_id=limiter_policy.subject_key,
            model_name=model_name,
            request_id=limiter_request_id,
            ttl_ms=limiter_context["ttl_ms"],
            user_total_limit=user_total_limit,
            user_model_limit=user_model_limit,
            mode=config.LIMITER_MODE,
            fail_policy=config.LIMITER_FAIL_POLICY,
            rollout_percent=config.LIMITER_ROLLOUT_PERCENT,
        )
        limiter_context.update(
            {
                "status": "acquired",
                "allowed": acquire_result.allowed,
                "blocked": acquire_result.blocked,
                "would_block": acquire_result.would_block,
                "bypass": acquire_result.bypass,
                "reason": acquire_result.reason,
                "mode": acquire_result.mode,
                "fail_policy": acquire_result.fail_policy,
                "error_policy_action": acquire_result.error_policy_action,
            }
        )
        if acquire_result.error:
            limiter_context["error"] = acquire_result.error
        if acquire_result.acquire_result:
            limiter_context["user_total_count"] = acquire_result.acquire_result.user_total_count
            limiter_context["user_model_count"] = acquire_result.acquire_result.user_model_count
            limiter_context["release_required"] = bool(
                acquire_result.acquire_result.granted and acquire_result.mode == "enforce"
            )

        logger.debug(
            "Limiter acquire.result, "
            f"req_id={req_id}, trace_id={trace_id}, subject={limiter_policy.subject_key}, model={model_name}, "
            f"policy={limiter_policy.limit_source}, ttl_ms={limiter_context.get('ttl_ms')}, "
            f"request_id={limiter_request_id}, mode={acquire_result.mode}, status={limiter_context.get('status')}, "
            f"allowed={acquire_result.allowed}, blocked={acquire_result.blocked}, "
            f"would_block={acquire_result.would_block}, reason={acquire_result.reason}"
        )
        logger.debug(
            "Limiter acquire.detail, "
            f"req_id={req_id}, trace_id={trace_id}, subject={limiter_policy.subject_key}, model={model_name}, "
            f"policy={limiter_policy.limit_source}, mode={acquire_result.mode}, fail_policy={acquire_result.fail_policy}, "
            f"bypass={acquire_result.bypass}, error_policy_action={acquire_result.error_policy_action}, "
            f"user_total_count={limiter_context.get('user_total_count')}, "
            f"user_model_count={limiter_context.get('user_model_count')}, release_required={limiter_context.get('release_required')}, "
            f"error={acquire_result.error}"
        )

        if acquire_result.would_block and not acquire_result.blocked:
            logger.debug(
                "Limiter acquire.would_block, "
                f"req_id={req_id}, trace_id={trace_id}, subject={limiter_policy.subject_key}, model={model_name}, "
                f"policy={limiter_policy.limit_source}, mode={acquire_result.mode}, reason={acquire_result.reason}, "
                f"request_id={limiter_request_id}"
            )

        if acquire_result.blocked:
            logger.warning(
                "Limiter acquire.blocked, "
                f"req_id={req_id}, trace_id={trace_id}, subject={limiter_policy.subject_key}, model={model_name}, "
                f"policy={limiter_policy.limit_source}, mode={acquire_result.mode}, reason={acquire_result.reason}, "
                f"request_id={limiter_request_id}"
            )
            raise HttpException(
                f"请求并发受限: reason={acquire_result.reason}, subject={limiter_policy.subject_key}, model={model_name}",
                "429",
            )

        return limiter_context

    @classmethod
    async def _release_limiter_context_once(cls, limiter_context: Optional[Dict[str, Any]]) -> None:
        req_id = context_util.REQUEST_ID.get() or "-"
        trace_id = context_util.TRACE_ID.get() or "-"
        if not limiter_context:
            logger.debug(f"Limiter release.skip, req_id={req_id}, trace_id={trace_id}, reason=empty_context")
            return
        if limiter_context.get("release_state") in {"released", "skipped", "error"}:
            logger.debug(
                "Limiter release.skip, "
                f"req_id={req_id}, trace_id={trace_id}, subject={limiter_context.get('subject_key') or '-'}, "
                f"model={limiter_context.get('model_name') or '-'}, request_id={limiter_context.get('request_id') or '-'}, "
                f"state={limiter_context.get('release_state')}, reason=already_terminal"
            )
            return
        if not limiter_context.get("release_required"):
            limiter_context["release_state"] = "skipped"
            logger.debug(
                "Limiter release.skip, "
                f"req_id={req_id}, trace_id={trace_id}, subject={limiter_context.get('subject_key') or '-'}, "
                f"model={limiter_context.get('model_name') or '-'}, request_id={limiter_context.get('request_id') or '-'}, "
                f"mode={limiter_context.get('mode') or '-'}, reason=release_not_required"
            )
            return

        subject_key = limiter_context.get("subject_key")
        model_name = limiter_context.get("model_name")
        limiter_request_id = limiter_context.get("request_id")
        ttl_ms = limiter_context.get("ttl_ms")
        if not subject_key or not model_name or not limiter_request_id or ttl_ms is None:
            limiter_context["release_state"] = "error"
            limiter_context["release_error"] = "incomplete_release_context"
            logger.warning(
                "Limiter release skipped due to incomplete context, "
                f"req_id={req_id}, trace_id={trace_id}, request_id={limiter_request_id}, "
                f"model={model_name}, subject={subject_key}, ttl_ms={ttl_ms}"
            )
            return

        limiter_context["release_state"] = "releasing"
        try:
            release_result = await cls._get_concurrency_limiter_service().release(
                user_id=subject_key,
                model_name=model_name,
                request_id=limiter_request_id,
                ttl_ms=int(ttl_ms),
            )
            limiter_context["released"] = bool(release_result.removed)
            limiter_context["release_state"] = "released"
            logger.debug(
                "Limiter release.result, "
                f"req_id={req_id}, trace_id={trace_id}, subject={subject_key}, model={model_name}, "
                f"request_id={limiter_request_id}, ttl_ms={ttl_ms}, status=released, removed={release_result.removed}"
            )
        except Exception as release_error:
            limiter_context["release_state"] = "error"
            limiter_context["release_error"] = str(release_error)
            logger.warning(
                "Limiter release failed, "
                f"req_id={req_id}, trace_id={trace_id}, request_id={limiter_request_id}, "
                f"model={model_name}, subject={subject_key}, error={release_error}"
            )

    @classmethod
    async def _finalize_limiter_and_image_slot_once(cls, result: RequestResult, data: Any) -> None:
        limiter_context = data.get("limiter_context") if isinstance(data, dict) else None
        req_id = context_util.REQUEST_ID.get() or "-"
        trace_id = context_util.TRACE_ID.get() or "-"
        terminal_status = result.status.value if hasattr(result.status, "value") else str(result.status)
        terminal_reason = "ok" if result.error is None else str(result.error)
        logger.debug(
            "Limiter request.end, "
            f"req_id={req_id}, trace_id={trace_id}, status={terminal_status}, reason={terminal_reason}, "
            f"subject={(limiter_context or {}).get('subject_key') or '-'}, "
            f"model={(limiter_context or {}).get('model_name') or '-'}, "
            f"mode={(limiter_context or {}).get('mode') or '-'}, policy={(limiter_context or {}).get('limit_source') or '-'}, "
            f"request_id={(limiter_context or {}).get('request_id') or '-'}, "
            f"release_required={bool((limiter_context or {}).get('release_required'))}"
        )
        await cls._release_limiter_context_once(limiter_context)
        if limiter_context:
            logger.debug(
                "Limiter request.end.release, "
                f"req_id={req_id}, trace_id={trace_id}, request_id={limiter_context.get('request_id') or '-'}, "
                f"release_state={limiter_context.get('release_state') or '-'}, released={limiter_context.get('released')}"
            )

        if not isinstance(data, dict) or not data.get("image_stream_handoff"):
            return
        request_id = result.request_id
        cls._image_generation_limiter_contexts.pop(request_id, None)
        if request_id in cls._image_generation_inflight:
            cls._image_generation_inflight.remove(request_id)
            cls.image_generation_semaphore.release()

    @classmethod
    async def audio_transcriptions_precheck_before_read(
        cls,
        *,
        api_key_digest: Optional[str],
        requested_model: Optional[str],
    ) -> Dict[str, Any]:
        precheck_model_name = "audio_router_precheck"
        limiter_request_id = UUIDUtil.generate_uuid_v4()
        precheck_context: Dict[str, Any] = {
            "subject_source": "api_key_digest",
            "subject_key": api_key_digest or "",
            "request_id": limiter_request_id,
            "model_name": requested_model or "",
            "precheck_model_name": precheck_model_name,
            "stage": "audio_router_precheck",
        }

        limiter_policy = cls._build_limiter_resolution_policy(
            api_key_digest=api_key_digest,
            auth_perm=None,
            model_default_total_limit=None,
            model_default_model_limit=None,
            local_fallback_total_limit=config.LIMITER_USER_TOTAL_CONCURRENCY_LIMIT,
            local_fallback_model_limit=config.LIMITER_USER_TOTAL_CONCURRENCY_LIMIT,
        )
        user_total_limit = cls._normalize_limit_value(limiter_policy.user_total_limit)

        precheck_context.update(
            {
                "subject_key": limiter_policy.subject_key,
                "subject_source": limiter_policy.subject_source,
                "limit_source": limiter_policy.limit_source,
                "user_total_limit": user_total_limit,
            }
        )
        if not limiter_policy.subject_key or user_total_limit is None:
            precheck_context.update(
                {
                    "status": "skipped",
                    "reason": "incomplete_policy",
                    "allowed": True,
                }
            )
            return precheck_context

        ttl_ms = cls._get_limiter_lease_ttl_ms()
        precheck_context["ttl_ms"] = ttl_ms
        acquire_result = await cls._get_concurrency_limiter_service().acquire_with_mode(
            user_id=limiter_policy.subject_key,
            model_name=precheck_model_name,
            request_id=limiter_request_id,
            ttl_ms=ttl_ms,
            user_total_limit=user_total_limit,
            user_model_limit=user_total_limit,
            mode=config.LIMITER_MODE,
            fail_policy=config.LIMITER_FAIL_POLICY,
            rollout_percent=config.LIMITER_ROLLOUT_PERCENT,
        )
        precheck_context.update(
            {
                "status": "acquired",
                "allowed": acquire_result.allowed,
                "blocked": acquire_result.blocked,
                "would_block": acquire_result.would_block,
                "bypass": acquire_result.bypass,
                "reason": acquire_result.reason,
                "mode": acquire_result.mode,
                "fail_policy": acquire_result.fail_policy,
                "error_policy_action": acquire_result.error_policy_action,
            }
        )
        if acquire_result.error:
            precheck_context["error"] = acquire_result.error
        if acquire_result.acquire_result:
            precheck_context["user_total_count"] = acquire_result.acquire_result.user_total_count

        if acquire_result.blocked:
            raise HttpException(
                (
                    "音频读取前并发预检受限: "
                    f"reason={acquire_result.reason}, subject={limiter_policy.subject_key}, model={requested_model or ''}"
                ),
                "429",
            )

        if acquire_result.acquire_result and acquire_result.acquire_result.granted and acquire_result.mode == "enforce":
            try:
                release_result = await cls._get_concurrency_limiter_service().release(
                    user_id=limiter_policy.subject_key,
                    model_name=precheck_model_name,
                    request_id=limiter_request_id,
                    ttl_ms=ttl_ms,
                )
                precheck_context["released"] = release_result.removed
            except Exception as release_error:
                logger.warning(
                    f"Audio router precheck release failed, request_id={limiter_request_id}, error={release_error}"
                )
                precheck_context["release_error"] = str(release_error)

        return precheck_context

    @staticmethod
    def _flag_enabled(flag_value: Any, default: bool = False) -> bool:
        if flag_value is None:
            return default
        if isinstance(flag_value, bool):
            return flag_value
        if isinstance(flag_value, (int, float)):
            return bool(flag_value)
        if isinstance(flag_value, str):
            return flag_value.strip().lower() in {"1", "true", "yes", "on"}
        return default

    @staticmethod
    def _normalize_limit_value(limit_value: Any) -> Optional[int]:
        if limit_value is None:
            return None
        try:
            parsed = int(limit_value)
        except (TypeError, ValueError):
            return None
        if parsed <= 0:
            return None
        return parsed

    @classmethod
    def _legacy_model_hash_key(cls) -> str:
        return build_governed_key_prefix(
            prefix=USER_AI_MODEL_SET_PREFIX,
            env=config.LEGACY_REDIS_ENV,
            service=config.LEGACY_REDIS_SERVICE,
            module=LEGACY_KEY_NAMESPACE_MODULE,
        )

    @classmethod
    async def _hget_legacy_model(cls, model_name: str) -> Any:
        governed_key = cls._legacy_model_hash_key()
        model_info = await cast(Awaitable[Any], RedisManager.client.hget(governed_key, model_name))
        if model_info is None and governed_key != USER_AI_MODEL_SET_PREFIX:
            model_info = await cast(Awaitable[Any], RedisManager.client.hget(USER_AI_MODEL_SET_PREFIX, model_name))
        return model_info

    @classmethod
    async def _hgetall_legacy_models(cls) -> Dict[str, Any]:
        governed_key = cls._legacy_model_hash_key()
        models = await cast(Awaitable[Dict[str, Any]], RedisManager.client.hgetall(governed_key))
        if not models and governed_key != USER_AI_MODEL_SET_PREFIX:
            models = await cast(Awaitable[Dict[str, Any]], RedisManager.client.hgetall(USER_AI_MODEL_SET_PREFIX))
        return models

    @classmethod
    def _resolve_limiter_subject(
        cls,
        api_key_digest: Optional[str],
        auth_perm: Optional[CodePlanApiKeyPermResp] = None,
    ) -> tuple[str, str]:
        use_user_id_subject = cls._flag_enabled(config.LIMITER_SUBJECT_USE_USER_ID, default=False)
        if use_user_id_subject and auth_perm is not None:
            raw_user_id = auth_perm.user_id
            if raw_user_id is not None:
                normalized_user_id = cast(str, str(raw_user_id).strip())
                if normalized_user_id != "":
                    return normalized_user_id, "user_id"
        return api_key_digest or "", "api_key_digest"

    @classmethod
    def _resolve_limiter_limits(
        cls,
        *,
        model_default_total_limit: Optional[int],
        model_default_model_limit: Optional[int],
        local_fallback_total_limit: Optional[int],
        local_fallback_model_limit: Optional[int],
        auth_perm: Optional[CodePlanApiKeyPermResp] = None,
    ) -> tuple[Optional[int], Optional[int], str]:
        effective_total = cls._normalize_limit_value(model_default_total_limit)
        if effective_total is None:
            effective_total = cls._normalize_limit_value(local_fallback_total_limit)

        effective_model = cls._normalize_limit_value(model_default_model_limit)
        if effective_model is None:
            effective_model = cls._normalize_limit_value(local_fallback_model_limit)

        use_dynamic_limit = cls._flag_enabled(config.LIMITER_ENABLE_DYNAMIC_LIMITS, default=False)
        if not use_dynamic_limit or auth_perm is None:
            return effective_total, effective_model, "model_or_local_default"

        dynamic_total = cls._normalize_limit_value(auth_perm.user_total_concurrency_limit)
        dynamic_model = cls._normalize_limit_value(auth_perm.user_model_concurrency_limit)
        if dynamic_total is None and dynamic_model is None:
            return effective_total, effective_model, "model_or_local_default"

        return dynamic_total or effective_total, dynamic_model or effective_model, "dynamic_reserved"

    @classmethod
    def _build_limiter_resolution_policy(
        cls,
        *,
        api_key_digest: Optional[str],
        auth_perm: Optional[CodePlanApiKeyPermResp] = None,
        model_default_total_limit: Optional[int] = None,
        model_default_model_limit: Optional[int] = None,
        local_fallback_total_limit: Optional[int] = None,
        local_fallback_model_limit: Optional[int] = None,
    ) -> LimiterResolutionPolicy:
        subject_key, subject_source = cls._resolve_limiter_subject(api_key_digest=api_key_digest, auth_perm=auth_perm)
        user_total_limit, user_model_limit, limit_source = cls._resolve_limiter_limits(
            model_default_total_limit=model_default_total_limit,
            model_default_model_limit=model_default_model_limit,
            local_fallback_total_limit=local_fallback_total_limit,
            local_fallback_model_limit=local_fallback_model_limit,
            auth_perm=auth_perm,
        )
        return LimiterResolutionPolicy(
            subject_key=subject_key,
            subject_source=subject_source,
            user_total_limit=user_total_limit,
            user_model_limit=user_model_limit,
            limit_source=limit_source,
        )

    @classmethod
    def _count_tools(cls, req: Dict[str, Any]) -> int:
        """计算请求中可用的 tools 数量
        Args:
            req: 请求字典
        Returns:
            int: tools 的数量
        """
        tools = req.get("tools")
        if tools is None:
            return 0
        if isinstance(tools, list):
            return len(tools)
        return 0

    @classmethod
    def _get_client_ip(cls, raw_request: Request | None) -> str:
        """从 FastAPI Request 获取客户端 IP（兼容反向代理头）。"""
        if raw_request is None:
            return ""

        try:
            x_forwarded_for = raw_request.headers.get("x-forwarded-for")
            if x_forwarded_for:
                # 可能是逗号分隔的多个IP，取第一个
                first = x_forwarded_for.split(",")[0].strip()
                if first:
                    return first

            x_real_ip = raw_request.headers.get("x-real-ip")
            if x_real_ip:
                x_real_ip = x_real_ip.strip()
                if x_real_ip:
                    return x_real_ip

            if raw_request.client and raw_request.client.host:
                return raw_request.client.host
        except Exception:
            return ""

        return ""
    

    @classmethod
    def _build_form_data_from_dict(cls, data_dict: Dict[str, Any], file_data: Dict[str, Any] | None = None) -> aiohttp.FormData:
        """
        从字典动态构建 FormData
        Args:
            data_dict: 包含表单数据的字典
            file_data: 文件数据字典，格式为 {'field_name': {'content': bytes, 'filename': str, 'content_type': str}}
        Returns:
            aiohttp.FormData: 构建好的表单数据
        """
        form_data = aiohttp.FormData()
        
        # 添加文件字段
        if file_data:
            for field_name, file_info in file_data.items():
                form_data.add_field(
                    field_name, 
                    file_info['content'],
                    filename=file_info.get('filename'),
                    content_type=file_info.get('content_type')
                )
        
        # 添加其他字段
        for field_name, field_value in data_dict.items():
            if field_value is not None:
                if isinstance(field_value, bool):
                    form_data.add_field(field_name, str(field_value).lower())
                elif isinstance(field_value, (list, tuple)):
                    # 处理数组类型参数
                    for item in field_value:
                        if item:
                            form_data.add_field(f'{field_name}[]', str(item))
                elif isinstance(field_value, (int, float)):
                    form_data.add_field(field_name, str(field_value))
                elif isinstance(field_value, str) and field_value.strip():
                    form_data.add_field(field_name, field_value)
                    
        return form_data

    
    
    @classmethod
    def _json_dumps(cls, obj: Any) -> str:
        """
        使用 orjson 序列化对象为 JSON 字符串
        Args:
            obj: 需要序列化的对象
        Returns:
            str: 序列化后的 JSON 字符串
        """
        return orjson.dumps(obj).decode('utf-8')
    
    @classmethod
    def _filter_anthropic_beta_header(cls, beta_header: str | None) -> str | None:
        """过滤 Anthropic-Beta header 中的黑名单项

        Args:
            beta_header: 原始的 Anthropic-Beta header 值，逗号分隔的列表

        Returns:
            过滤后的 header 值，如果过滤后为空则返回 None
        """
        if not beta_header:
            return None

        # 解析逗号分隔的列表
        beta_items = [item.strip() for item in beta_header.split(',')]

        # 过滤掉黑名单中的项
        blacklist = config.ANTHROPIC_BETA_HEADER_BLACKLIST
        filtered_items = [item for item in beta_items if item and item not in blacklist]

        if not filtered_items:
            return None

        return ','.join(filtered_items)

    
    @classmethod
    async def _check_responses_model_need_transform(cls, model: Union[str, None]) -> bool:
        """
        检查模型是否需要通过transformer转换
        Args:
            model: 模型名称
        Returns:
            bool: True表示需要transformer转换，False表示不需要
        Raises:
            HttpException: 当模型不存在或配置无效时
        """
        try:
            if not model:
                raise HttpException("模型名称不能为空", "400")
            model_info_str = await cls._hget_legacy_model(model)
            if not model_info_str:
                raise HttpException(f"模型 {model} 无效", "404")
            
            # 处理Redis返回的可能是bytes或str的情况
            model_info_str = model_info_str.decode('utf-8') if isinstance(model_info_str, bytes) else model_info_str
            model_info = orjson.loads(model_info_str)
            upstream_type_list = model_info.get("upstream_type", "openai")
            upstream_types = upstream_type_list.split(',')
            return "responses" not in upstream_types
        except orjson.JSONDecodeError:
            raise HttpException(f"模型 {model} 配置格式错误，请联系客服处理～", "500")
        except HttpException as e:
            raise e
        except Exception as e:
            logger.error(f"检查模型配置失败: {e}")
            raise HttpException(f"检查模型 {model} 配置失败，请联系客服处理～", "500")

    @classmethod
    async def _get_model_config(
        cls,
        model: Union[str, None],
        api_key_digest: Union[str, None],
        include_limiter_policy: bool = False,
    ) -> tuple[Any, ...]:
        """
        从Redis获取模型配置信息
        Args:
            model: 模型名称
        Returns:
            tuple: (api_key, base_url)
        Raises:
            HttpException: 当模型不存在或配置无效时
        """
        try:
            if not model:
                raise HttpException("模型名称不能为空","400")
            model_info_str = await cls._hget_legacy_model(model)
            if not model_info_str:
                raise HttpException(f"模型 {model} 无效","404")

            # 处理Redis返回的可能是bytes或str的情况
            model_info_str = model_info_str.decode('utf-8') if isinstance(model_info_str, bytes) else model_info_str
            model_info = orjson.loads(model_info_str)
            api_key = model_info.get('api_key')
            base_url = model_info.get('base_url')
            real_ai_model = model_info.get('real_ai_model')
            original_ai_model = model_info.get('ai_model', model)
            active_code_plan_level = model_info.get('active_code_plan_level', None)
            model_perm_groups = model_info.get('model_perm_groups', [])
            if model_perm_groups == []:
                raise HttpException(f"模型[{model}]尚未开放调用，请切换其它模型～","403")

            resolved_api_key_digest: str = api_key_digest or ""
            auth_perm = await ApiKeyService().check_code_plan_api_key_perm(
                api_key_digest=resolved_api_key_digest,
                model_name=original_ai_model,
            )
            model_default_total_limit = (
                model_info.get("model_default_user_total_concurrency_limit")
                or model_info.get("user_total_concurrency_limit")
            )
            model_default_model_limit = (
                model_info.get("model_default_user_model_concurrency_limit")
                or model_info.get("user_model_concurrency_limit")
            )
            limiter_policy = cls._build_limiter_resolution_policy(
                api_key_digest=resolved_api_key_digest,
                auth_perm=auth_perm,
                model_default_total_limit=model_default_total_limit,
                model_default_model_limit=model_default_model_limit,
                local_fallback_total_limit=config.LIMITER_USER_TOTAL_CONCURRENCY_LIMIT,
                local_fallback_model_limit=config.LIMITER_USER_MODEL_CONCURRENCY_LIMIT,
            )
            logger.debug(
                f"Limiter policy scaffold resolved: "
                f"subject_source={limiter_policy.subject_source}, "
                f"limit_source={limiter_policy.limit_source}"
            )
            # if bool(set(model_perm_groups) & set(context_util.current_user_api_key_perms())) is False:
            #     raise HttpException(f"用户暂无模型[{model}]调用权限，请前往官网订阅/升级codeplan套餐或资源包～","403")
            # if active_code_plan_level is not None:
            #     user_code_plan_level = context_util.current_user_code_plan_level()
            #     if user_code_plan_level < active_code_plan_level:
            #         raise HttpException(f"模型 {model} 不在当前等级CodePlan支持名单内，请尝试升级您的CodePlan等级或切换其它模型～","403")
            
            if not api_key or not base_url:
                raise HttpException(f"模型 {model} 配置缺失，请联系客服处理～","500")

            if include_limiter_policy:
                return api_key, base_url, real_ai_model, original_ai_model, limiter_policy
            return api_key, base_url, real_ai_model, original_ai_model
        except orjson.JSONDecodeError:
            raise HttpException(f"模型 {model} 配置格式错误，请联系客服处理～","500")
        except HttpException as e:
            raise e
        except Exception as e:
            logger.error(f"获取模型配置失败: {e}")
            raise HttpException(f"获取模型 {model} 配置失败，请联系客服处理～","500")
        
    @classmethod
    async def _build_request_url_and_headers(
        cls,
        model_name: str,
        original_path: str,
        original_base_url: str,
        original_api_key: str,
        original_api_type: str = "openai"
    ) -> tuple[str, str, dict]:
        """
        根据模型配置构建最终请求URL和额外headers

        当need_transform=1时：
        - URL: {transform_base_url}/{primary_type}/{target_type}{original_path}
        - Headers: X-Upstream-BaseURL 和 X-Upstream-Apikey

        Args:
            model_name: 模型名称
            original_path: 原始路径（含query参数），如 "/v1/messages?beta=true"
            original_base_url: 原始base_url（用于header）
            original_api_key: 原始api_key（用于header）
            original_api_type: 原始API类型（如openai），用于构建URL

        Returns:
            tuple[str, dict]: (最终URL, 额外headers字典)
        """
        # 从Redis获取完整模型配置
        model_info_str = await cls._hget_legacy_model(model_name)
        if isinstance(model_info_str, bytes):
            model_info_str = model_info_str.decode('utf-8')
        model_info = orjson.loads(model_info_str)

        upstream_type_list = model_info.get("upstream_type", "openai")
        upstream_types = upstream_type_list.split(',')

        need_transform = original_api_type not in upstream_types

        # 不需要转换，返回原始URL
        if need_transform != 1:
            return original_base_url.rstrip('/') + original_path, original_api_key, {}

        # 需要转换
        primary_type = original_api_type

        # 2. 获取目标类型（从模型的transform_type）
        transform_type = model_info.get("transform_type", ['openai'])
        if not isinstance(transform_type, list):
            transform_type = [transform_type] if transform_type else ['openai']
        target_type = transform_type[0] if transform_type else 'openai'

        # 3. 获取transform_base_url
        transform_base_url = model_info.get('transform_base_url')
        if not transform_base_url:
            raise HttpException(f"模型 {model_name} 的transform_base_url配置缺失", "500")

        # 4. 构建最终URL
        url = f"{transform_base_url.rstrip('/')}/{primary_type}/{target_type}{original_path}"

        transform_api_key = model_info.get('transform_api_key')
        if not transform_api_key:
            raise HttpException(f"模型 {model_name} 的transform_api_key配置缺失", "500")
        # 5. 构建额外headers
        headers = {
            "X-Upstream-BaseURL": original_base_url,
            "X-Upstream-Apikey": original_api_key
        }

        return url, transform_api_key, headers

        
    @classmethod
    async def _request_error_callback(cls,result: RequestResult, data: Any) -> None:
        """请求错误回调函数"""
        try:
            logger.error(f"Caught Exception: {str(result.error)}")
        finally:
            await cls._finalize_limiter_and_image_slot_once(result, data)


    @classmethod
    async def _request_success_callback(cls, result: RequestResult, data: Dict[str, Any]) -> None:
        """请求成功回调函数"""
        try:
            call_time: datetime = data.get("call_time", datetime.now())
            start_time = time.mktime(call_time.timetuple()) + call_time.microsecond / 1_000_000
            use_time_ms = int((time.time() - start_time) * 1000)
            api_key: str = data.get("api_key", "")
            client_ip: str = data.get("client_ip", "")
            chat_log_type: int = data.get("chat_log_type", 1)
            model_name: str = data.get("model_name", "")
            real_model_name: str = data.get("real_model_name", "")
            usage:Dict = result.json_body.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            if prompt_tokens == 0:
                prompt_tokens = usage.get("input_tokens", 0)
                cache_creation_input_tokens = usage.get("cache_creation_input_tokens", 0)
                cache_read_input_tokens = usage.get("cache_read_input_tokens", 0)
                prompt_tokens = prompt_tokens + cache_creation_input_tokens + cache_read_input_tokens
            if completion_tokens == 0:
                completion_tokens = usage.get("output_tokens", 0)
            total_tokens = usage.get("total_tokens", 0)
            input_tokens, output_tokens = prompt_tokens, completion_tokens

            if prompt_tokens > 0 or completion_tokens > 0 or total_tokens > 0:
                if prompt_tokens == 0 and completion_tokens == 0:
                    input_tokens = prompt_tokens
                    output_tokens = total_tokens
                else:
                    input_tokens = prompt_tokens
                    output_tokens = completion_tokens
            if prompt_tokens > 0 or completion_tokens > 0:
                tools_count = data.get("tools_count", 0)
                log = ChatLogAddReq(
                    order_no=f"vlm-{UUIDUtil.generate_random_string(20)}",
                    call_time=call_time,
                    api_key=api_key,
                    ip_address=client_ip,
                    model_name=model_name,
                    real_model_name=real_model_name,
                    use_time=use_time_ms,
                    status=1,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    tools_count=tools_count,
                )
                asyncio.create_task(cls._save_log_task(log_entry=log))
        finally:
            await cls._finalize_limiter_and_image_slot_once(result, data)

    @classmethod
    async def _request_success_callback_stream(cls, result: RequestResult, data: Dict[str, Any]) -> None:
        """请求成功回调函数，流式响应专用"""
        try:
            call_time: datetime = data.get("call_time", datetime.now())
            start_time = time.mktime(call_time.timetuple()) + call_time.microsecond / 1_000_000
            api_key: str = data.get("api_key", "")
            client_ip: str = data.get("client_ip", "")
            chat_log_type: int = data.get("chat_log_type", 1)
            model_name: str = data.get("model_name", "")
            real_model_name: str = data.get("real_model_name", "")
            chat_endpoint_type: str = data.get("chat_endpoint_type", "openai")
            if type(result.content) == bytes:
                buffer = result.content
            elif type(result.content) == str:
                buffer = result.content.encode("utf-8")
            else:
                buffer = b""
            use_time_ms = int((time.time() - start_time) * 1000)
            status = 1 if result.status == RequestStatus.COMPLETED else 0
            tools_count = data.get("tools_count", 0)
            log = ChatLogAddReq(
                order_no=f"vlm-{UUIDUtil.generate_random_string(20)}",
                call_time=call_time,
                api_key=api_key,
                ip_address=client_ip,
                model_name=model_name,
                real_model_name=real_model_name or model_name,
                use_time=use_time_ms,
                status=status,
                input_tokens=0,
                output_tokens=0,
                tools_count=tools_count,
            )
            if chat_endpoint_type == "openai":
                asyncio.create_task(cls._save_log_task_stream(log_entry=log, buffer=buffer))
            elif chat_endpoint_type == "responses":
                asyncio.create_task(cls._responses_save_log_task_stream(log_entry=log, buffer=buffer))
            elif chat_endpoint_type == "anthropic":
                asyncio.create_task(cls._anthropic_save_log_task_stream(log_entry=log, buffer=buffer))
            else:
                # 兼容默认走openai日志记录逻辑
                asyncio.create_task(cls._save_log_task_stream(log_entry=log, buffer=buffer))
        finally:
            await cls._finalize_limiter_and_image_slot_once(result, data)

    @classmethod
    def _ensure_bytes(cls, content: Any) -> bytes:
        if isinstance(content, bytes):
            return content
        if isinstance(content, str):
            return content.encode("utf-8")
        return b""

    @classmethod
    def _extract_sse_json_payload(cls, line: bytes, *, skip_event_lines: bool = False) -> bytes | None:
        stripped_line = line.strip()
        if not stripped_line:
            return None
        if skip_event_lines and stripped_line.startswith(b"event:"):
            return None
        if stripped_line.startswith(b"data:"):
            stripped_line = stripped_line.replace(b"data:", b"", 1).strip()
        if stripped_line == b"[DONE]":
            return None
        return stripped_line

    @classmethod
    def _extract_openai_stream_usage(cls, buffer: bytes) -> Optional[Tuple[int, int]]:
        lines = buffer.split(b"\n")
        candidate_lines = lines[-6:] if len(lines) >= 6 else lines
        for line in reversed(candidate_lines):
            payload = cls._extract_sse_json_payload(line)
            if payload is None:
                continue
            try:
                json_data = orjson.loads(payload)
            except orjson.JSONDecodeError:
                continue
            usage = json_data.get("usage")
            if not isinstance(usage, dict):
                continue
            input_tokens = int(usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0) or 0)
            output_tokens = int(usage.get("completion_tokens", 0) or usage.get("output_tokens", 0) or 0)
            return input_tokens, output_tokens
        return None

    @classmethod
    def _extract_responses_stream_usage(cls, buffer: bytes) -> Optional[Tuple[int, int]]:
        lines = buffer.split(b"\n")
        for line in reversed(lines):
            payload = cls._extract_sse_json_payload(line, skip_event_lines=True)
            if payload is None:
                continue
            try:
                json_data = orjson.loads(payload)
            except orjson.JSONDecodeError:
                continue
            usage = json_data.get("usage")
            if not isinstance(usage, dict):
                response = json_data.get("response")
                if isinstance(response, dict):
                    usage = response.get("usage")
            if not isinstance(usage, dict):
                continue
            return int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)
        return None

    @classmethod
    def _extract_anthropic_stream_usage(cls, buffer: bytes) -> Optional[Tuple[int, int, int, int]]:
        lines = buffer.split(b"\n")
        selected_lines = lines[:6] + lines[-10:] if len(lines) >= 16 else lines
        max_input_tokens = 0
        max_output_tokens = 0
        max_cache_creation = 0
        max_cache_read = 0
        found_usage = False

        for line in selected_lines:
            payload = cls._extract_sse_json_payload(line, skip_event_lines=True)
            if payload is None:
                continue
            try:
                json_data = orjson.loads(payload)
            except orjson.JSONDecodeError:
                continue

            usage = json_data.get("usage")
            if not isinstance(usage, dict):
                message = json_data.get("message")
                if isinstance(message, dict):
                    usage = message.get("usage")
            if not isinstance(usage, dict):
                continue

            found_usage = True
            max_input_tokens = max(max_input_tokens, int(usage.get("input_tokens", 0) or 0))
            max_output_tokens = max(max_output_tokens, int(usage.get("output_tokens", 0) or 0))
            max_cache_creation = max(max_cache_creation, int(usage.get("cache_creation_input_tokens", 0) or 0))
            max_cache_read = max(max_cache_read, int(usage.get("cache_read_input_tokens", 0) or 0))

        if not found_usage:
            return None
        return max_input_tokens, max_output_tokens, max_cache_creation, max_cache_read

    @classmethod
    async def _request_stream_finish_callback(cls, result: RequestResult, data: Dict[str, Any]) -> None:
        """流式请求结束回调：用于音频/图片等流式接口写调用日志。"""
        try:
            call_time: datetime = data.get("call_time", datetime.now())
            start_time = time.mktime(call_time.timetuple()) + call_time.microsecond / 1_000_000
            use_time_ms = int((time.time() - start_time) * 1000)

            api_key: str = data.get("api_key", "")
            client_ip: str = data.get("client_ip", "")
            chat_log_type: int = data.get("chat_log_type", 1)
            model_name: str = data.get("model_name", "")
            real_model_name: str = data.get("real_model_name", "")

            status = 1 if result.status == RequestStatus.COMPLETED else 0

            buffer = cls._ensure_bytes(result.content)
            tools_count = data.get("tools_count", 0)
            log = ChatLogAddReq(
                order_no=f"vlm-{UUIDUtil.generate_random_string(20)}",
                call_time=call_time,
                api_key=api_key,
                ip_address=client_ip,
                model_name=model_name,
                real_model_name=real_model_name or model_name,
                use_time=use_time_ms,
                status=status,
                input_tokens=0,
                output_tokens=0,
                tools_count=tools_count,
            )
            asyncio.create_task(cls._save_log_task_stream(log_entry=log, buffer=buffer))
        finally:
            await cls._finalize_limiter_and_image_slot_once(result, data)

    @classmethod
    async def _image_generation_success_callback(cls, result: RequestResult, data: Dict[str, Any]) -> None:
        """图片生成非流式成功回调：usage 字段为 input/output/total_tokens。"""
        try:
            call_time: datetime = data.get("call_time", datetime.now())
            start_time = time.mktime(call_time.timetuple()) + call_time.microsecond / 1_000_000
            use_time_ms = int((time.time() - start_time) * 1000)

            api_key: str = data.get("api_key", "")
            client_ip: str = data.get("client_ip", "")
            chat_log_type: int = data.get("chat_log_type", 1)
            model_name: str = data.get("model_name", "")
            real_model_name: str = data.get("real_model_name", "")

            body = result.json_body or {}
            usage: Dict[str, Any] = body.get("usage", {}) if isinstance(body, dict) else {}
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            total_tokens = usage.get("total_tokens", 0)

            if input_tokens == 0 and output_tokens == 0 and total_tokens == 0:
                return

            tools_count = data.get("tools_count", 0)
            log = ChatLogAddReq(
                order_no=f"vlm-{UUIDUtil.generate_random_string(20)}",
                call_time=call_time,
                api_key=api_key,
                ip_address=client_ip,
                model_name=model_name,
                real_model_name=real_model_name or model_name,
                use_time=use_time_ms,
                status=1,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                tools_count=tools_count,
            )
            asyncio.create_task(cls._save_log_task(log_entry=log))
        finally:
            await cls._finalize_limiter_and_image_slot_once(result, data)

    @classmethod
    async def _tts_finish_callback(cls, result: RequestResult, data: Dict[str, Any]) -> None:
        """TTS 请求结束回调：按文本长度计 prompt_tokens。"""
        try:
            call_time: datetime = data.get("call_time", datetime.now())
            start_time = time.mktime(call_time.timetuple()) + call_time.microsecond / 1_000_000
            use_time_ms = int((time.time() - start_time) * 1000)

            api_key: str = data.get("api_key", "")
            client_ip: str = data.get("client_ip", "")
            chat_log_type: int = data.get("chat_log_type", 1)
            model_name: str = data.get("model_name", "")
            real_model_name: str = data.get("real_model_name", "")
            input_tokens: int = int(data.get("input_tokens", 0) or 0)
            output_tokens: int = int(data.get("output_tokens", 0) or 0)

            status = 1 if result.status == RequestStatus.COMPLETED else 0

            tools_count = data.get("tools_count", 0)
            log = ChatLogAddReq(
                order_no=f"vlm-{UUIDUtil.generate_random_string(20)}",
                call_time=call_time,
                api_key=api_key,
                ip_address=client_ip,
                model_name=model_name,
                real_model_name=real_model_name or model_name,
                use_time=use_time_ms,
                status=status,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                tools_count=tools_count,
            )
            asyncio.create_task(cls._save_log_task(log_entry=log))
        finally:
            await cls._finalize_limiter_and_image_slot_once(result, data)

    @classmethod
    async def _audio_transcription_success_callback(cls, result: RequestResult, data: Dict[str, Any]) -> None:
        """音频转录非流式成功回调：特殊Token计算逻辑"""
        try:
            call_time: datetime = data.get("call_time", datetime.now())
            start_time = time.mktime(call_time.timetuple()) + call_time.microsecond / 1_000_000
            use_time_ms = int((time.time() - start_time) * 1000)

            api_key: str = data.get("api_key", "")
            client_ip: str = data.get("client_ip", "")
            chat_log_type: int = data.get("chat_log_type", 1)
            model_name: str = data.get("model_name", "")
            real_model_name: str = data.get("real_model_name", "")

            body = result.json_body or {}
            usage = body.get("usage", {})
            text = body.get("text", "")

            # 特殊计算逻辑
            seconds = usage.get("seconds", 0)
            input_tokens = int(seconds * 100)
            output_tokens = len(text)

            tools_count = data.get("tools_count", 0)
            log = ChatLogAddReq(
                order_no=f"vlm-{UUIDUtil.generate_random_string(20)}",
                call_time=call_time,
                api_key=api_key,
                ip_address=client_ip,
                model_name=model_name,
                real_model_name=real_model_name or model_name,
                use_time=use_time_ms,
                status=1,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                tools_count=tools_count,
            )
            asyncio.create_task(cls._save_log_task(log_entry=log))
        finally:
            await cls._finalize_limiter_and_image_slot_once(result, data)

    @classmethod
    async def non_stream_chat(cls,  req: Dict[str, Any], api_key: str,path:str, raw_request: Request)-> Union[Dict[str, Any], Any]:
        """
        处理对VLM的代理请求。
        Args:
            req (Dict[str, Any]): 包含所有模型参数的请求字典。
        Returns:
            Union[JSONResponse, StreamingResponse]: 直接从VLM客户端返回的响应。
        """
        chat_log_type = req.pop("chat_log_type", 1)
        model_name = req.get("model")

        # 从Redis获取模型配置
        model_api_key, base_url, real_model_name, model_name, limiter_policy = await cls._get_model_config(
            model_name,
            api_key,
            include_limiter_policy=True,
        )
        req["model"] = real_model_name  # 使用真实模型名称
        # 保存原始base_url和api_key用于可能的transform headers
        original_base_url = base_url
        original_api_key = model_api_key

        # 构建URL和额外headers
        url, model_api_key, transform_headers = await cls._build_request_url_and_headers(
            model_name,
            '/v1/chat/completions',
            original_base_url,
            original_api_key,
            "openai"
        )
        if transform_headers is None or len(transform_headers) == 0:
            logger.debug(f"Model {model_name} does not require header transformation.")
        else:
            logger.debug(f"Model {model_name} requires header transformation., transformer_url: {url}")

        headers = {
            "Authorization": f"Bearer {model_api_key}",
            "Content-Type": "application/json"
        }
        headers.update(transform_headers)

        # 透传user-agent
        if raw_request:
            user_agent = raw_request.headers.get("user-agent")
            if user_agent:
                headers["User-Agent"] = user_agent

        start_time = time.time()
        call_time = datetime.fromtimestamp(start_time)

        # 计算 tools 数量
        tools_count = cls._count_tools(req)
        limiter_context = await cls._acquire_pre_submit_limiter(
            limiter_policy=limiter_policy,
            model_name=model_name,
        )

        user_data = {
            "call_time": call_time,
            "api_key": api_key,
            "client_ip": cls._get_client_ip(raw_request),
            "real_model_name": real_model_name,
            "model_name": model_name,
            "chat_log_type": chat_log_type,
            "endpoint_path": "/v1/chat/completions",
            "tools_count": tools_count,
            "limiter_context": limiter_context,
        }
        try:
            proxy_client = await cls.get_sse_proxy_client()
            req_wrapper = RequestWrapper(
                url=url,
                method="POST",
                headers=headers,
                json=req,
                timeout=config.VLM_PROXY_TOTAL_REQUEST_TIMEOUT,
                on_success=cls._request_success_callback,
                on_failure=cls._request_error_callback,
                user_data=user_data,
                max_retries=config.VLM_PROXY_RETRIES,
                retry_interval=config.VLM_PROXY_RETRY_INTERVAL
            )
            req_id = proxy_client.submit(req_wrapper)
            logger.debug(f"Forwarding request {req_id} (Stream: False) to VLM backend")
            await proxy_client.wait_for_upstream_status(req_id)
            return await proxy_client.json(req_id)
        except HttpException as e:
            raise e
        except HttpErrorWithContent as e:
            raise e
        except Exception as e:
            raise HttpException(f"聊天请求失败: {str(e)}", "500")

    @classmethod
    async def stream_chat_do_request(cls, req: Dict[str, Any], api_key: str,path:str, raw_request: Request)-> Tuple[str,float]:
        """
        处理对VLM的代理请求，返回aiohttp响应对象。
        Args:
            req (Dict[str, Any]): 包含所有模型参数的请求字典。
        Returns:
            Tuple[str, float]: 直接从VLM客户端返回的响应对象和调用时间。
        """
        chat_log_type = req.pop("chat_log_type", 1)
        model_name = req.get("model")
        # 从Redis获取模型配置
        model_api_key, base_url, real_model_name, model_name, limiter_policy = await cls._get_model_config(
            model_name,
            api_key,
            include_limiter_policy=True,
        )
        req["model"] = real_model_name  # 使用真实模型名称
        # 保存原始base_url和api_key用于可能的transform headers
        original_base_url = base_url
        original_api_key = model_api_key

        # 构建URL和额外headers
        url, model_api_key, transform_headers = await cls._build_request_url_and_headers(
            model_name,
            '/v1/chat/completions',
            original_base_url,
            original_api_key,
            "openai"
        )
        if transform_headers is None or len(transform_headers) == 0:
            logger.debug(f"Model {model_name} does not require header transformation.")
        else:
            logger.debug(f"Model {model_name} requires header transformation., transformer_url: {url}")

        headers = {
            "Authorization": f"Bearer {model_api_key}",
            "Content-Type": "application/json"
        }
        headers.update(transform_headers)

        # 透传user-agent
        if raw_request:
            user_agent = raw_request.headers.get("user-agent")
            if user_agent:
                headers["User-Agent"] = user_agent

        start_time = time.time()
        call_time = datetime.fromtimestamp(start_time)

        req["stream"] = True
        req["stream_options"] = {"include_usage": True}

        tools_count = cls._count_tools(req)
        limiter_context = await cls._acquire_pre_submit_limiter(
            limiter_policy=limiter_policy,
            model_name=model_name,
        )

        cancel_behavior = CancelBehavior.TRIGGER_SUCCESS
        user_data = {
            "call_time": call_time,
            "api_key": api_key,
            "client_ip": cls._get_client_ip(raw_request),
            "real_model_name": real_model_name,
            "model_name": model_name,
            "chat_log_type": chat_log_type,
            "tools_count": tools_count,
            "chat_endpoint_type": "openai",
            "limiter_context": limiter_context,
        }

        try:
            proxy_client = await cls.get_sse_proxy_client()
            req_wrapper = RequestWrapper(
                url=url,
                method="POST",
                headers=headers,
                json=req,
                is_stream=True,
                keep_content_in_memory=True,
                timeout=config.VLM_PROXY_TOTAL_REQUEST_TIMEOUT,
                on_failure=cls._request_error_callback,
                on_success=cls._request_success_callback_stream,
                user_data=user_data,
                cancel_behavior=cancel_behavior,
                retry_on_stream_error=False,
                max_retries=config.VLM_PROXY_RETRIES,
                retry_interval=config.VLM_PROXY_RETRY_INTERVAL
            )
            req_id = proxy_client.submit(req_wrapper)
            logger.debug(f"Forwarding request {req_id} (Stream: False) to VLM backend")
            await proxy_client.wait_for_upstream_status(req_id)
            return req_id, start_time
        except HttpException as e:
            raise e
        except HttpErrorWithContent as e:
            raise e
        except Exception as e:
            raise HttpException(f"聊天请求失败: {str(e)}", "500")

    @classmethod
    async def _check_client_disconnected(cls, raw_request: Request, request_id: str) -> None:
        """检查客户端是否断开连接"""
        try:
            # 监测客户端断开连接
            proxy_client = await cls.get_sse_proxy_client()
            while proxy_client.is_alive(request_id):
                if await raw_request.is_disconnected():
                    logger.warning(f"Client disconnected, cancelling upstream request {request_id} if active.")
                    await proxy_client.cancel_request(
                        request_id,
                    )
                    return
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            return
        
    @classmethod
    async def stream_chat_get_response(
        cls,
        req: Dict[str, Any],
        api_key: str,
        path:str,
        client_request_id: str,
        start_time: float,
        raw_request: Request
        ) -> AsyncGenerator[Any, None]:
        """
        处理对VLM的代理请求，返回流式响应生成器。"""
        logger.debug("Starting VLM stream_chat_get_response")
        try:
            # asyncio.create_task(cls._check_client_disconnected(raw_request, client_request_id))
            
            proxy_client = await cls.get_sse_proxy_client()
            async for chunk in proxy_client.stream_generator_with_heartbeat(client_request_id):
                yield chunk
        except Exception as e:
            raise HttpException(f"聊天流式响应失败: {str(e)}", "500")
    @classmethod
    async def stream_chat(cls, req: Dict[str, Any], api_key: str,path:str,raw_request: Request)-> AsyncGenerator[Any, None]:
        """
        处理对VLM的代理请求。
        Args:
            req (Dict[str, Any]): 包含所有模型参数的请求字典。
        Returns:
            Union[JSONResponse, StreamingResponse]: 直接从VLM客户端返回的响应。
        """
        request_id, start_time = await cls.stream_chat_do_request(req, api_key,path, raw_request)
        async for chunk in cls.stream_chat_get_response(req, api_key,path, request_id, start_time, raw_request):
            yield chunk

    @classmethod
    def _responses_validate_and_normalize_req(cls, req: Dict[str, Any]):
        """
        校验请求体字段，并将 input 规范化为上游支持的 'message' 格式
        """
        # 1. 拦截不支持的字段，直接抛出 400
        if "conversation" in req or "previous_response_id" in req:
            logger.warning("Request blocked: contains 'conversation' or 'previous_response_id'")
            raise HttpException("请求体包含不支持的字段: conversation 或 previous_response_id", "400")

        user_input = req.get("input")

        # 定义包装函数：将纯文本转换为上游要求的 'message' 结构
        def wrap_as_message(text_content: str) -> Dict[str, Any]:
            return {
                "type": "message",
                "role": "user",
                "content": text_content
            }

        # 2. 如果 input 是字符串，转换为包含单个 message 对象的列表
        if isinstance(user_input, str):
            req["input"] = [wrap_as_message(user_input)]
            logger.debug("Normalized string input to message object list")

        # 3. 如果 input 是列表，检查其中的元素
        elif isinstance(user_input, list):
            normalized_input = []
            for item in user_input:
                if isinstance(item, str):
                    # 处理 ["hello"] 这种情况
                    normalized_input.append(wrap_as_message(item))
                else:
                    # 如果已经是对象 {"type": "message", ...} 则保持原样
                    normalized_input.append(item)
            req["input"] = normalized_input

    @classmethod
    async def non_stream_responses(cls,  req: Dict[str, Any], api_key: str,path:str, raw_request: Request)-> Union[Dict[str, Any], Any]:
        """
        处理对VLM的代理请求。
        Args:
            req (Dict[str, Any]): 包含所有模型参数的请求字典。
        Returns:
            Union[JSONResponse, StreamingResponse]: 直接从VLM客户端返回的响应。
        """
        cls._responses_validate_and_normalize_req(req)
        chat_log_type = req.pop("chat_log_type", 1)
        model_name = req.get("model")

        service_tier = req.get("service_tier", "flex")
        if service_tier == "priority":
            raise HttpException("暂时不支持priority服务层级的模型调用，请切换到flex服务层级～", "400")

        # 1. 获取基础配置
        model_api_key, base_url, real_model_name, model_name, limiter_policy = await cls._get_model_config(
            model_name,
            api_key,
            include_limiter_policy=True,
        )
        req["model"] = real_model_name

        # 保存原始base_url和api_key用于可能的transform headers
        original_base_url = base_url
        original_api_key = model_api_key

        # 构建URL和额外headers
        url, model_api_key, transform_headers = await cls._build_request_url_and_headers(
            model_name,
            path,
            original_base_url,
            original_api_key,
            "responses"
        )
        if transform_headers is None or len(transform_headers) == 0:
            logger.debug(f"Model {model_name} does not require header transformation.")
        else:
            logger.debug(f"Model {model_name} requires header transformation., transformer_url: {url}")


        headers = {
            "Authorization": f"Bearer {model_api_key}",
            "Content-Type": "application/json",
            "OpenAI-Beta": "assistants=v2"
        }
        # 添加transform headers
        headers.update(transform_headers)
        
        # 透传user-agent
        if raw_request:
            user_agent = raw_request.headers.get("user-agent")
            if user_agent:
                headers["User-Agent"] = user_agent

        status = 1  # 调用状态：0失败 1成功
        prompt_tokens, completion_tokens, total_tokens = 0, 0, 0
        start_time = time.time()
        call_time = datetime.fromtimestamp(start_time)
        # 计算 tools 数量
        tools_count = cls._count_tools(req)
        limiter_context = await cls._acquire_pre_submit_limiter(
            limiter_policy=limiter_policy,
            model_name=model_name,
        )

        user_data = {
            "call_time": call_time,
            "api_key": api_key,
            "client_ip": cls._get_client_ip(raw_request),
            "real_model_name": real_model_name,
            "model_name": model_name,
            "chat_log_type": chat_log_type,
            "endpoint_path": "/v1/messages",
            "tools_count": tools_count,
            "limiter_context": limiter_context,
        }

        try:
            # -----------------非流式响应--------------------
            proxy_client = await cls.get_sse_proxy_client()
            req_wrapper = RequestWrapper(
                url=url,
                method="POST",
                headers=headers,
                json=req,
                timeout=config.VLM_PROXY_TOTAL_REQUEST_TIMEOUT,
                on_success=cls._request_success_callback,
                on_failure=cls._request_error_callback,
                user_data=user_data,
                max_retries=config.VLM_PROXY_RETRIES,
                retry_interval=config.VLM_PROXY_RETRY_INTERVAL
            )
            req_id = proxy_client.submit(req_wrapper)
            logger.debug(f"Forwarding request {req_id} (Stream: False) to VLM backend")
            await proxy_client.wait_for_upstream_status(req_id)
            # 直接返回JSON响应
            return await proxy_client.json(req_id)
        except HttpException as e:
            raise e
        except HttpErrorWithContent as e:
            raise e
        except Exception as e:
            raise HttpException(f"聊天请求失败: {str(e)}", "500")

    @classmethod
    async def stream_responses_do_request(cls, req: Dict[str, Any], api_key: str,path:str, raw_request: Request)-> Tuple[str,float]:
        """
        处理对VLM的代理请求，返回aiohttp响应对象。
        Args:
            req (Dict[str, Any]): 包含所有模型参数的请求字典。
        Returns:
            Tuple[str, float]: 直接从VLM客户端返回的响应对象和调用时间。
        """
        cls._responses_validate_and_normalize_req(req)
        chat_log_type = req.pop("chat_log_type", 1)
        model_name = req.get("model")

        service_tier = req.get("service_tier", "flex")
        if service_tier == "priority":
            raise HttpException("暂时不支持priority服务层级的模型调用，请切换到flex服务层级～", "400")

        # 从Redis获取模型配置
        model_api_key, base_url, real_model_name, model_name, limiter_policy = await cls._get_model_config(
            model_name,
            api_key,
            include_limiter_policy=True,
        )
        req["model"] = real_model_name  # 使用真实模型名称
        
        # 保存原始base_url和api_key用于可能的transform headers
        original_base_url = base_url
        original_api_key = model_api_key

        # 构建URL和额外headers
        url, model_api_key, transform_headers = await cls._build_request_url_and_headers(
            model_name,
            path,
            original_base_url,
            original_api_key,
            "responses"
        )
        if transform_headers is None or len(transform_headers) == 0:
            logger.debug(f"Model {model_name} does not require header transformation.")
        else:
            logger.debug(f"Model {model_name} requires header transformation., transformer_url: {url}")

        # bypass headers
        headers = {}
        headers.update({
            "Authorization": f"Bearer {model_api_key}",
            "Content-Type": "application/json",
            "OpenAI-Beta": "assistants=v2"
        })

        # 添加transform headers
        headers.update(transform_headers)

        if raw_request:
            user_agent = raw_request.headers.get("user-agent")
            if user_agent:
                headers["User-Agent"] = user_agent

        status = 1  # 调用状态：0失败 1成功
        prompt_tokens, completion_tokens = 0, 0
        start_time = time.time()
        call_time = datetime.fromtimestamp(start_time)

        # 确保每个chunk包含token信息
        req["stream"] = True
        tools_count = cls._count_tools(req)
        limiter_context = await cls._acquire_pre_submit_limiter(
            limiter_policy=limiter_policy,
            model_name=model_name,
        )
        cancel_behavior = CancelBehavior.TRIGGER_SUCCESS
        user_data = {
            "call_time": call_time,
            "api_key": api_key,
            "client_ip": cls._get_client_ip(raw_request),
            "real_model_name": real_model_name,
            "model_name": model_name,
            "chat_log_type": chat_log_type,
            "tools_count": tools_count,
            "chat_endpoint_type": "responses",
            "limiter_context": limiter_context,
        }

        try:
            proxy_client = await cls.get_sse_proxy_client()
            req_wrapper = RequestWrapper(
                url=url,
                method="POST",
                headers=headers,
                json=req,
                is_stream=True,
                keep_content_in_memory=True,
                timeout=config.VLM_PROXY_TOTAL_REQUEST_TIMEOUT,
                on_failure=cls._request_error_callback,
                on_success=cls._request_success_callback_stream,
                user_data=user_data,
                cancel_behavior=cancel_behavior,
                retry_on_stream_error=False,
                max_retries=config.VLM_PROXY_RETRIES,
                retry_interval=config.VLM_PROXY_RETRY_INTERVAL
            )
            req_id = proxy_client.submit(req_wrapper)
            logger.debug(f"Forwarding request {req_id} (Stream: True) to VLM backend")
            await proxy_client.wait_for_upstream_status(req_id)
            return req_id, start_time
        except HttpException as e:
            raise e
        except HttpErrorWithContent as e:
            raise e
        except Exception as e:
            raise HttpException(f"聊天请求失败: {str(e)}", "500")

    @classmethod
    async def stream_responses_get_response(
        cls,
        req: Dict[str, Any],
        api_key: str,
        path:str,
        client_request_id: str,
        start_time: float,
        raw_request: Request
        ) -> AsyncGenerator[Any, None]:
        """
        处理对VLM的代理请求，返回流式响应生成器。"""
        logger.debug("Starting VLM stream_chat_get_response")
        try:
            # asyncio.create_task(cls._check_client_disconnected(raw_request, client_request_id))
            
            proxy_client = await cls.get_sse_proxy_client()
            async for chunk in proxy_client.stream_generator_with_heartbeat(client_request_id):
                yield chunk
        except Exception as e:
            raise HttpException(f"聊天流式响应失败: {str(e)}", "500")
    @classmethod
    async def stream_responses(cls, req: Dict[str, Any], api_key: str,path:str,raw_request: Request)-> AsyncGenerator[Any, None]:
        """
        处理对VLM的代理请求。
        Args:
            req (Dict[str, Any]): 包含所有模型参数的请求字典。
        Returns:
            Union[JSONResponse, StreamingResponse]: 直接从VLM客户端返回的响应。
        """
        request_id, start_time = await cls.stream_responses_do_request(req, api_key,path, raw_request)
        async for chunk in cls.stream_responses_get_response(req, api_key,path, request_id, start_time, raw_request):
            yield chunk
    @classmethod
    async def non_stream_anthropic_messages(cls,  req: Dict[str, Any], api_key: str,path:str, raw_request: Request)-> Union[Dict[str, Any], Any]:
        """
        处理对VLM的代理请求。
        Args:
            req (Dict[str, Any]): 包含所有模型参数的请求字典。
        Returns:
            Union[JSONResponse, StreamingResponse]: 直接从VLM客户端返回的响应。
        """
        chat_log_type = req.pop("chat_log_type", 1)
        model_name = req.get("model")

        # 从Redis获取模型配置
        model_api_key, base_url, real_model_name, model_name, limiter_policy = await cls._get_model_config(
            model_name,
            api_key,
            include_limiter_policy=True,
        )
        req["model"] = real_model_name  # 使用真实模型名称

        # 保存原始base_url和api_key用于可能的transform headers
        original_base_url = base_url
        original_api_key = model_api_key

        # 构建URL和额外headers
        url, model_api_key, transform_headers = await cls._build_request_url_and_headers(
            model_name,
            '/v1/messages?beta=true',
            original_base_url,
            original_api_key,
            "anthropic"
        )
        if transform_headers is None or len(transform_headers) == 0:
            logger.debug(f"Model {model_name} does not require header transformation.")
        else:
            logger.debug(f"Model {model_name} requires header transformation., transformer_url: {url}")

        # bypass headers
        headers = {
            "Authorization": f"Bearer {model_api_key}",
            "Content-Type": "application/json"
        }
        headers.update(transform_headers)

        # 透传user-agent
        if raw_request:
            user_agent = raw_request.headers.get("user-agent")
            if user_agent:
                headers["User-Agent"] = user_agent
        # 透传 anthropic 专有 header
        if raw_request:
            anthropic_version = raw_request.headers.get("anthropic-version")
            if anthropic_version:
                headers["Anthropic-Version"] = anthropic_version
        if raw_request and raw_request.headers.get("anthropic-beta"):
            filtered_beta = cls._filter_anthropic_beta_header(raw_request.headers.get("anthropic-beta"))
            if filtered_beta:
                headers["Anthropic-Beta"] = filtered_beta
        if raw_request:
            x_app = raw_request.headers.get("x-app")
            if x_app:
                headers["X-App"] = x_app

        status = 1  # 调用状态：0失败 1成功
        prompt_tokens, completion_tokens, total_tokens = 0, 0, 0
        start_time = time.time()
        call_time = datetime.fromtimestamp(start_time)

        # 计算 tools 数量
        tools_count = cls._count_tools(req)
        limiter_context = await cls._acquire_pre_submit_limiter(
            limiter_policy=limiter_policy,
            model_name=model_name,
        )

        user_data = {
            "call_time": call_time,
            "api_key": api_key,
            "client_ip": cls._get_client_ip(raw_request),
            "real_model_name": real_model_name,
            "model_name": model_name,
            "chat_log_type": chat_log_type,
            "endpoint_path": "/v1/messages",
            "tools_count": tools_count,
            "limiter_context": limiter_context,
        }
        try:
            # -----------------非流式响应--------------------
            proxy_client = await cls.get_sse_proxy_client()
            req_wrapper = RequestWrapper(
                url=url,
                method="POST",
                headers=headers,
                json=req,
                timeout=config.VLM_PROXY_TOTAL_REQUEST_TIMEOUT,
                on_success=cls._request_success_callback,
                on_failure=cls._request_error_callback,
                user_data=user_data,
                max_retries=config.VLM_PROXY_RETRIES,
                retry_interval=config.VLM_PROXY_RETRY_INTERVAL
            )
            req_id = proxy_client.submit(req_wrapper)
            logger.debug(f"Forwarding request {req_id} (Stream: False) to VLM backend")
            await proxy_client.wait_for_upstream_status(req_id)
            # 直接返回JSON响应
            return await proxy_client.json(req_id)
        except HttpException as e:
            raise e
        except HttpErrorWithContent as e:
            raise e
        except Exception as e:
            raise HttpException(f"聊天请求失败: {str(e)}", "500")
    
    @classmethod
    async def anthropic_messages_stream_do_request(cls, req: Dict[str, Any], api_key: str,path:str, raw_request: Request)-> Tuple[str,float]:
        """
        处理对VLM的代理请求，返回aiohttp响应对象。
        Args:
            req (Dict[str, Any]): 包含所有模型参数的请求字典。
        Returns:
            Tuple[str, float]: 直接从VLM客户端返回的响应对象和调用时间。
        """
        chat_log_type = req.pop("chat_log_type", 1)
        model_name = req.get("model")
        # 从Redis获取模型配置
        model_api_key, base_url, real_model_name, model_name, limiter_policy = await cls._get_model_config(
            model_name,
            api_key,
            include_limiter_policy=True,
        )
        req["model"] = real_model_name  # 使用真实模型名称

        # 保存原始base_url和api_key用于可能的transform headers
        original_base_url = base_url
        original_api_key = model_api_key

        # 构建URL和额外headers
        url, model_api_key, transform_headers = await cls._build_request_url_and_headers(
            model_name,
            '/v1/messages?beta=true',
            original_base_url,
            original_api_key,
            "anthropic"
        )
        if transform_headers is None or len(transform_headers) == 0:
            logger.debug(f"Model {model_name} does not require header transformation.")
        else:
            logger.debug(f"Model {model_name} requires header transformation., transformer_url: {url}")
        

        # bypass headers
        headers = {
            "Authorization": f"Bearer {model_api_key}",
            "Content-Type": "application/json"
        }
        headers.update(transform_headers)

        # 透传user-agent
        if raw_request:
            user_agent = raw_request.headers.get("user-agent")
            if user_agent:
                headers["User-Agent"] = user_agent
        # 透传 anthropic 专有 header
        if raw_request:
            anthropic_version = raw_request.headers.get("anthropic-version")
            if anthropic_version:
                headers["Anthropic-Version"] = anthropic_version
        if raw_request and raw_request.headers.get("anthropic-beta"):
            filtered_beta = cls._filter_anthropic_beta_header(raw_request.headers.get("anthropic-beta"))
            if filtered_beta:
                headers["Anthropic-Beta"] = filtered_beta
        if raw_request:
            x_app = raw_request.headers.get("x-app")
            if x_app:
                headers["X-App"] = x_app

        status = 1  # 调用状态：0失败 1成功
        prompt_tokens, completion_tokens = 0, 0
        start_time = time.time()
        call_time = datetime.fromtimestamp(start_time)

        # 确保每个chunk包含token信息
        req["stream"] = True
        tools_count = cls._count_tools(req)
        limiter_context = await cls._acquire_pre_submit_limiter(
            limiter_policy=limiter_policy,
            model_name=model_name,
        )
        cancel_behavior = CancelBehavior.TRIGGER_SUCCESS
        user_data = {
            "call_time": call_time,
            "api_key": api_key,
            "client_ip": cls._get_client_ip(raw_request),
            "real_model_name": real_model_name,
            "model_name": model_name,
            "chat_log_type": chat_log_type,
            "tools_count": tools_count,
            "chat_endpoint_type": "anthropic",
            "limiter_context": limiter_context,
        }

        try:
            proxy_client = await cls.get_sse_proxy_client()
            req_wrapper = RequestWrapper(
                url=url,
                method="POST",
                headers=headers,
                json=req,
                is_stream=True,
                keep_content_in_memory=True,
                timeout=config.VLM_PROXY_TOTAL_REQUEST_TIMEOUT,
                on_failure=cls._request_error_callback,
                on_success=cls._request_success_callback_stream,
                user_data=user_data,
                cancel_behavior=cancel_behavior,
                retry_on_stream_error=False,
                max_retries=config.VLM_PROXY_RETRIES,
                retry_interval=config.VLM_PROXY_RETRY_INTERVAL
            )
            req_id = proxy_client.submit(req_wrapper)
            logger.debug(f"Forwarding request {req_id} (Stream: True) to VLM backend")
            await proxy_client.wait_for_upstream_status(req_id)
            return req_id, start_time
        except HttpException as e:
            raise e
        except HttpErrorWithContent as e:
            raise e
        except Exception as e:
            raise HttpException(f"聊天请求失败: {str(e)}", "500")
        
    @classmethod
    async def anthropic_messages_stream_get_response(
        cls,
        req: Dict[str, Any],
        api_key: str,
        path:str,
        client_request_id: str,
        start_time: float,
        raw_request: Request
        ) -> AsyncGenerator[Any, None]:
        """
        处理对VLM的代理请求，返回流式响应生成器。"""
        logger.debug("Starting VLM anthropic_messages_stream_get_response")
        try:
            # asyncio.create_task(cls._check_client_disconnected(raw_request, client_request_id))
            
            proxy_client = await cls.get_sse_proxy_client()
            async for chunk in proxy_client.stream_generator_with_heartbeat(client_request_id):
                yield chunk
        except Exception as e:
            raise HttpException(f"聊天流式响应失败: {str(e)}", "500")
        
    @classmethod
    async def stream_anthropic_messages(cls, req: Dict[str, Any], api_key: str,path:str,raw_request: Request)-> AsyncGenerator[Any, None]:
        """
        处理对VLM的代理请求。
        Args:
            req (Dict[str, Any]): 包含所有模型参数的请求字典。
        Returns:
            Union[JSONResponse, StreamingResponse]: 直接从VLM客户端返回的响应。
        """
        request_id, start_time = await cls.anthropic_messages_stream_do_request(req, api_key,path, raw_request)
        async for chunk in cls.anthropic_messages_stream_get_response(req, api_key,path, request_id, start_time, raw_request):
            yield chunk

    @classmethod
    async def anthropic_count_tokens(cls,  req: Dict[str, Any], api_key: str, api_url: str, raw_request: Request)-> Union[str, Any]:
        """
        处理对VLM的非流式代理请求。
        Args:
            req (Dict[str, Any]): 包含所有模型参数的请求字典。
        Returns:
            Union[JSONResponse, StreamingResponse]: 直接从VLM客户端返回的响应。
        """
        chat_log_type = req.pop("chat_log_type", 1)
        model_name = req.get("model")
        
        # 从Redis获取模型配置
        model_api_key, base_url, real_model_name, model_name = await cls._get_model_config(model_name,api_key)
        req["model"] = real_model_name  # 使用真实模型名称

        # 保存原始base_url和api_key用于可能的transform headers
        original_base_url = base_url
        original_api_key = model_api_key

        # 构建URL和额外headers
        url, model_api_key, transform_headers = await cls._build_request_url_and_headers(
            model_name,
            api_url,
            original_base_url,
            original_api_key,
            "anthropic"
        )

        if transform_headers is None or len(transform_headers) == 0:
            logger.debug(f"Model {model_name} does not require header transformation.")
        else:
            logger.debug(f"Model {model_name} requires header transformation., transformer_url: {url}")

        headers = {
            "Authorization": f"Bearer {model_api_key}",
            "Content-Type": "application/json"
        }
        headers.update(transform_headers)

        # 透传user-agent
        if raw_request:
            user_agent = raw_request.headers.get("user-agent")
            if user_agent:
                headers["User-Agent"] = user_agent
        # 透传 anthropic 专有 header
        if raw_request:
            anthropic_version = raw_request.headers.get("anthropic-version")
            if anthropic_version:
                headers["Anthropic-Version"] = anthropic_version
        if raw_request and raw_request.headers.get("anthropic-beta"):
            filtered_beta = cls._filter_anthropic_beta_header(raw_request.headers.get("anthropic-beta"))
            if filtered_beta:
                headers["Anthropic-Beta"] = filtered_beta
        if raw_request:
            x_app = raw_request.headers.get("x-app")
            if x_app:
                headers["X-App"] = x_app

        status = 1  # 调用状态：0失败 1成功
        prompt_tokens, completion_tokens, total_tokens = 0, 0, 0
        start_time = time.time()
        call_time = datetime.fromtimestamp(start_time)

        # 计算 tools 数量（embeddings 等请求通常不使用 tools）
        tools_count = cls._count_tools(req)
        user_data = {
            "call_time": call_time,
            "api_key": api_key,
            "client_ip": cls._get_client_ip(raw_request),
            "real_model_name": real_model_name,
            "model_name": model_name,
            "chat_log_type": chat_log_type,
            "endpoint_path": "/v1" + api_url,
            "tools_count": tools_count,
        }

        try:
            # -----------------非流式响应--------------------
            proxy_client = await cls.get_sse_proxy_client()
            req_wrapper = RequestWrapper(
                url=url,
                method="POST",
                headers=headers,
                json=req,
                timeout=config.VLM_PROXY_TOTAL_REQUEST_TIMEOUT,
                user_data=user_data,
                max_retries=config.VLM_PROXY_RETRIES,
                retry_interval=config.VLM_PROXY_RETRY_INTERVAL
            )
            req_id = proxy_client.submit(req_wrapper)
            logger.debug(f"Forwarding request {req_id} (Stream: False) to VLM backend")
            await proxy_client.wait_for_upstream_status(req_id)
            # 直接返回JSON响应
            return await proxy_client.json(req_id)
        except HttpException as e:
            raise e
        except HttpErrorWithContent as e:
            raise e
        except Exception as e:
            raise HttpException(f"聊天请求失败: {str(e)}", "500")
    @classmethod
    async def proxy_request_non_stream(cls,  req: Dict[str, Any], api_key: str, api_url: str, raw_request: Request)-> Union[str, Any]:
        """
        处理对VLM的非流式代理请求。
        Args:
            req (Dict[str, Any]): 包含所有模型参数的请求字典。
        Returns:
            Union[JSONResponse, StreamingResponse]: 直接从VLM客户端返回的响应。
        """
        chat_log_type = req.pop("chat_log_type", 1)
        model_name = req.get("model")
        
        # 从Redis获取模型配置
        model_api_key, base_url, real_model_name, model_name, limiter_policy = await cls._get_model_config(
            model_name,
            api_key,
            include_limiter_policy=True,
        )
        req["model"] = real_model_name  # 使用真实模型名称

        # 保存原始base_url和api_key用于可能的transform headers
        original_base_url = base_url
        original_api_key = model_api_key

        # 构建URL和额外headers
        url, model_api_key, transform_headers = await cls._build_request_url_and_headers(
            model_name,
            api_url,
            original_base_url,
            original_api_key
        )

        if transform_headers is None or len(transform_headers) == 0:
            logger.debug(f"Model {model_name} does not require header transformation.")
        else:
            logger.debug(f"Model {model_name} requires header transformation., transformer_url: {url}")

        
        headers = {
            "Authorization": f"Bearer {model_api_key}",
            "Content-Type": "application/json"
        }

        headers.update(transform_headers)
        
        # 透传user-agent
        if raw_request:
            user_agent = raw_request.headers.get("user-agent")
            if user_agent:
                headers["User-Agent"] = user_agent

        status = 1  # 调用状态：0失败 1成功
        prompt_tokens, completion_tokens, total_tokens = 0, 0, 0
        start_time = time.time()
        call_time = datetime.fromtimestamp(start_time)
        
        # 计算 tools 数量（embeddings 等请求通常不使用 tools）
        tools_count = cls._count_tools(req)
        limiter_context = await cls._acquire_pre_submit_limiter(
            limiter_policy=limiter_policy,
            model_name=model_name,
        )
        
        user_data = {
            "call_time": call_time,
            "api_key": api_key,
            "client_ip": cls._get_client_ip(raw_request),
            "real_model_name": real_model_name,
            "model_name": model_name,
            "chat_log_type": chat_log_type,
            "endpoint_path": "/v1"+api_url,
            "tools_count": tools_count,
            "limiter_context": limiter_context,
        }
        try:
            # -----------------非流式响应--------------------
            proxy_client = await cls.get_sse_proxy_client()
            req_wrapper = RequestWrapper(
                url=url,
                method="POST",
                headers=headers,
                json=req,
                timeout=config.VLM_PROXY_TOTAL_REQUEST_TIMEOUT,
                on_failure=cls._request_error_callback,
                on_success=cls._request_success_callback,
                user_data=user_data,
                max_retries=config.VLM_PROXY_RETRIES,
                retry_interval=config.VLM_PROXY_RETRY_INTERVAL
            )
            req_id = proxy_client.submit(req_wrapper)
            logger.debug(f"Forwarding request {req_id} (Stream: False) to VLM backend")
            await proxy_client.wait_for_upstream_status(req_id)
            # 直接返回JSON响应
            return await proxy_client.json(req_id)
        except HttpException as e:
            raise e
        except HttpErrorWithContent as e:
            raise e
        except Exception as e:
            raise HttpException(f"聊天请求失败: {str(e)}", "500")


    @classmethod
    async def proxy_tts(cls, req: Dict[str, Any], api_key: str, raw_request: Request) -> Any:
        """
        处理对VLM的TTS代理请求。
        Args:
            req (Dict[str, Any]): 包含所有模型参数的请求字典。
        Returns:
            Any: 直接从VLM客户端返回的响应。
        """
        chat_log_type = req.pop("chat_log_type", 1)
        model_name = req.get("model", "OpenAudio-S1-mini")

        # 从Redis获取模型配置
        model_api_key, base_url, real_model_name, model_name, limiter_policy = await cls._get_model_config(
            model_name,
            api_key,
            include_limiter_policy=True,
        )
        req["model"] = real_model_name  # 使用真实模型名称


        url = urljoin(base_url.rstrip('/') + '/','/' + "v1/tts".lstrip('/'))
        
        headers = {
            "Authorization": f"Bearer {model_api_key}",
            "Content-Type": "application/json"
        }
        
        # 透传user-agent
        if raw_request:
            user_agent = raw_request.headers.get("user-agent")
            if user_agent:
                headers["User-Agent"] = user_agent

        status = 1  # 调用状态：0失败 1成功
        prompt_tokens, completion_tokens, total_tokens = 0, 0, 0
        text = req.get("text", "")
        # set prompt_tokens as utf-8 text length
        if text:
            prompt_tokens = len(text.encode('utf-8'))
        else:
            raise HttpException("TTS请求缺少'text'参数")
        start_time = time.time()
        call_time = datetime.fromtimestamp(start_time)

        filename = f"tts_{UUIDUtil.generate_random_string(8)}"
        file_ext = req.get("format", "wav")
        
        # TTS 请求不使用 tools
        tools_count = 0
        limiter_context = await cls._acquire_pre_submit_limiter(
            limiter_policy=limiter_policy,
            model_name=model_name,
        )
        
        user_data = {
            "call_time": call_time,
            "api_key": api_key,
            "client_ip": cls._get_client_ip(raw_request),
            "chat_log_type": chat_log_type,
            "model_name": model_name,
            "real_model_name": real_model_name,
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens,
            "tools_count": tools_count,
            "limiter_context": limiter_context,
        }

        try:
            proxy_client = await cls.get_sse_proxy_client()
            req_wrapper = RequestWrapper(
                url=url,
                method="POST",
                headers=headers,
                json=req,
                timeout=config.VLM_PROXY_TOTAL_REQUEST_TIMEOUT,
                keep_content_in_memory=True,
                on_success=cls._tts_finish_callback,
                on_failure=cls._tts_finish_callback,
                user_data=user_data,
                max_retries=config.VLM_PROXY_RETRIES,
                retry_interval=config.VLM_PROXY_RETRY_INTERVAL
            )
            req_id = proxy_client.submit(req_wrapper)
            logger.debug(f"Forwarding request {req_id} (Stream: False) to VLM backend")
            await proxy_client.wait_for_upstream_status(req_id)
            content = await proxy_client.content(req_id)
            return content, filename, file_ext
        except HttpException as e:
            raise e
        except HttpErrorWithContent as e:
            raise e
        except Exception as e:
            raise HttpException(f"TTS请求失败: {str(e)}", "500")

    @classmethod
    async def close_resp(cls, resp):
        """
        aiohttp自动处理连接关闭，此方法保留以维持接口兼容性
        """
        logger.debug("Connection closed automatically by aiohttp")

    @classmethod
    async def _save_log_task(cls, log_entry:ChatLogAddReq):
        """异步保存日志到Redis"""
        # 检查配置开关，如果未启用则直接返回
        if not config.CHAT_LOG_ENABLE:
            logger.debug("Chat log is disabled, skipping log save.")
            return
            
        try:
            await ChatLogRedisManager.add_chat_log(log_entry)
            logger.debug(f"Chat log saved for model {log_entry.model_name}.")
        except Exception as e:
            logger.error(f"Failed to save chat log: {e}")

    @classmethod
    async def _save_log_task_stream(cls, log_entry:ChatLogAddReq,buffer: bytes):
        """异步保存日志到Redis"""
        if not config.CHAT_LOG_ENABLE:
            logger.debug("Chat log is disabled, skipping log save.")
            return

        try:
            usage = await asyncio.to_thread(cls._extract_openai_stream_usage, buffer)
            if usage is None:
                return
            log_entry.input_tokens, log_entry.output_tokens = usage
            await ChatLogRedisManager.add_chat_log(log_entry)
        except Exception as e:
            logger.error(f"Failed to save chat log: {e}")
    
    @classmethod
    async def _responses_save_log_task_stream(cls, log_entry: ChatLogAddReq, buffer: bytes):
        if not config.CHAT_LOG_ENABLE:
            return

        try:
            usage = await asyncio.to_thread(cls._extract_responses_stream_usage, buffer)
            if usage is None:
                return
            input_tokens, output_tokens = usage
            log_entry.input_tokens = input_tokens
            log_entry.output_tokens = output_tokens
            await ChatLogRedisManager.add_chat_log(log_entry)
            logger.debug(f"Stream Log Saved: {input_tokens} in, {output_tokens} out")
        except Exception as e:
            logger.error(f"Failed to save chat log: {e}")

    @classmethod
    async def _anthropic_save_log_task_stream(cls, log_entry:ChatLogAddReq,buffer: bytes):
        """异步保存日志到Redis"""
        if not config.CHAT_LOG_ENABLE:
            logger.debug("Chat log is disabled, skipping log save.")
            return

        try:
            usage = await asyncio.to_thread(cls._extract_anthropic_stream_usage, buffer)
            if usage is None:
                logger.warning(f"No usage data found in stream for logging., req_id: {log_entry.order_no}")
                return

            max_input_tokens, max_output_tokens, max_cache_creation, max_cache_read = usage
            logger.debug(
                "Extracted usage from stream: "
                f"input_tokens={max_input_tokens}, output_tokens={max_output_tokens}, "
                f"cache_creation_input_tokens={max_cache_creation}, cache_read_input_tokens={max_cache_read}"
            )
            log_entry.input_tokens = max_input_tokens + max_cache_creation + max_cache_read
            log_entry.output_tokens = max_output_tokens
            await ChatLogRedisManager.add_chat_log(log_entry)
        except Exception as e:
            logger.error(f"Failed to save chat log: {e}")

    @classmethod
    async def models(cls) -> Dict[str, Any]:
        """
        获取模型列表
        Returns:
            Dict[str, Any]: 模型列表
        """
        try:
            # 批量获取所有模型信息
            models_dict = await cls._hgetall_legacy_models()
            models_data = []
            
            if not models_dict:
                logger.warning("No models found in Redis")
                return {
                    "object": "list",
                    "data": [],
                    "first_id": None,
                    "last_id": None,
                    "has_more": False
                }
            
            for model_alias, model_info_str in models_dict.items():
                try:
                    # 处理Redis返回的可能是bytes或str的情况
                    model_alias = model_alias.decode('utf-8') if isinstance(model_alias, bytes) else model_alias
                    model_info_str = model_info_str.decode('utf-8') if isinstance(model_info_str, bytes) else model_info_str
                    
                    # 解析模型配置
                    model_info = orjson.loads(model_info_str)
                    # 只显示标记为在worker中显示的模型
                    # if model_info.get("is_display_in_worker", 0) == 1 and context_util.current_user_code_plan_level() >= model_info.get("active_code_plan_level", 0):
                    is_display_in_worker = model_info.get("is_display_in_worker", 0)
                    model_perm_groups = model_info.get('model_perm_groups', [])
                    if is_display_in_worker and bool(set(model_perm_groups) & set(context_util.current_user_perms_group())):
                        # 兼容 OpenAI 和 anthropic 的模型列表格式，使用别名作为ID
                        model_data = {
                            "id": model_alias,  # 使用别名作为ID
                            "object": "model",
                            "type": "model",
                            "display_name": model_alias,
                            "created": 1754385070,
                            "created_at": 1754385070,
                            "owned_by": "system"
                        }
                        models_data.append(model_data)
                        
                except orjson.JSONDecodeError as je:
                    logger.warning(f"Failed to parse model info for alias '{model_alias}': {je}")
                    continue
                except Exception as e:
                    logger.warning(f"Error processing model '{model_alias}': {e}")
                    continue
            
            logger.info(f"Successfully loaded {len(models_data)} models")
            return {
                "object": "list",
                "data": models_data
            }
            
        except Exception as e:
            logger.error(f"Failed to fetch models from Redis: {e}")
            raise HttpException("获取模型列表失败","500")

    @classmethod
    async def get_model(cls, model_id: str) -> Dict[str, Any]:
        """
        获取特定模型的详细信息
        Args:
            model_id: 模型ID（别名）
        Returns:
            Dict[str, Any]: 模型详细信息，如果模型不存在或无效则返回空数据
        """
        try:
            # 验证输入参数
            if not model_id or not isinstance(model_id, str):
                logger.warning("Model ID is empty or invalid")
                raise HttpException("模型ID不能为空或无效","400")
            
            model_id = model_id.strip()
            if not model_id:
                logger.warning("Model ID is empty after strip")
                raise HttpException("模型ID不能为空或无效","400")
            
            # 获取模型的详细信息
            model_info_str = await cls._hget_legacy_model(model_id)
            if not model_info_str:
                logger.warning(f"Model '{model_id}' not found in Redis")
                raise HttpException(f"找不到 {model_id} 模型","404")
            
            # 处理Redis返回的可能是bytes或str的情况
            model_info_str = model_info_str.decode('utf-8') if isinstance(model_info_str, bytes) else model_info_str
            
            try:
                model_info = orjson.loads(model_info_str)
                model_perm_groups = model_info.get('model_perm_groups', [])
                if bool(set(model_perm_groups) & set(context_util.current_user_perms_group())) is False:
                    raise HttpException(f"用户暂无模型[{model_id}]调用权限，请前往官网订阅/升级codeplan套餐或资源包～","403")
                # if not model_info or context_util.current_user_code_plan_level() < model_info.get("active_code_plan_level", 0):
                #     raise HttpException(f"模型 {model_id} 不在当前等级CodePlan支持名单内，请尝试升级您的CodePlan等级或切换其它模型～","403")
            except orjson.JSONDecodeError as je:
                logger.warning(f"Failed to parse model info for ID '{model_id}': {je}")
                raise HttpException("模型信息解析失败","500")
            
            # 检查模型是否在worker中显示
            if model_info.get("is_display_in_worker", 0) != 1:
                logger.warning(f"Model '{model_id}' is not available for display in worker")
                raise HttpException(f"模型 {model_id} 不可用","404")
            
            # 返回模型详细信息
            result = {
                "id": model_id,
                "object": "model",
                "type": "model",
                "display_name": model_id,
                "created": 1754385070,
                "created_at": 1754385070,
                "owned_by": "system"
            }
            
            logger.debug(f"Successfully retrieved model info for '{model_id}'")
            return result
            
        except Exception as e:
            logger.error(f"Failed to fetch model '{model_id}': {e}")
            raise HttpException("获取模型信息失败","500")

    @classmethod
    async def audio_transcriptions_non_stream(cls, request: Annotated[TranscriptionRequest, Form()], api_key: str,raw_request: Request) -> Union[str, Any]:
        """
        处理音频转录的非流式代理请求
        Args:
            file: 音频文件
            req_params: 请求参数
            api_key: API密钥
            path: 请求路径
        Returns:
            响应数据
        """
        chat_log_type = 1
        model_name = request.model

        # 从Redis获取模型配置
        model_api_key, base_url, real_model_name, model_name, limiter_policy = await cls._get_model_config(
            model_name,
            api_key,
            include_limiter_policy=True,
        )
        request.model = real_model_name  # 使用真实模型名称
        url = urljoin(base_url.rstrip('/') + '/', "/v1/audio/transcriptions")

        headers = {
            "Authorization": f"Bearer {model_api_key}",
        }
        
        # 透传user-agent
        if raw_request:
            user_agent = raw_request.headers.get("user-agent")
            if user_agent:
                headers["User-Agent"] = user_agent

        start_time = time.time()
        call_time = datetime.fromtimestamp(start_time)

        # 音频转录请求不使用 tools
        tools_count = 0
        limiter_context = await cls._acquire_pre_submit_limiter(
            limiter_policy=limiter_policy,
            model_name=model_name,
        )

        user_data = {
            "call_time": call_time,
            "api_key": api_key,
            "client_ip": cls._get_client_ip(raw_request),
            "chat_log_type": chat_log_type,
            "model_name": model_name,
            "real_model_name": real_model_name,
            "tools_count": tools_count,
            "limiter_context": limiter_context,
        }

        try:
            filename = request.file.filename
            content_type = request.file.content_type
            file_content = await request.file.read()

            file_data = {
                'file': {
                    'content': file_content,
                    'filename': filename,
                    'content_type': content_type
                }
            }

            form_fields = {
                'model': request.model,
                'language': request.language,
                'prompt': request.prompt,
                'response_format': request.response_format,
                'temperature': request.temperature,
                'timestamp_granularities': getattr(request, 'timestamp_granularities', None),
                'top_p': getattr(request, 'top_p', None),
                'top_k': getattr(request, 'top_k', None),
                'min_p': getattr(request, 'min_p', None),
                'seed': getattr(request, 'seed', None),
                'frequency_penalty': getattr(request, 'frequency_penalty', None),
                'repetition_penalty': getattr(request, 'repetition_penalty', None),
                'presence_penalty': getattr(request, 'presence_penalty', None),
                'to_language': getattr(request, 'to_language', None),
                'stream': getattr(request, 'stream', False),
                'stream_include_usage': getattr(request, 'stream_include_usage', False),
                'stream_continuous_usage_stats': getattr(request, 'stream_continuous_usage_stats', False)
            }

            form_data = cls._build_form_data_from_dict(form_fields, file_data)

            proxy_client = await cls.get_sse_proxy_client()
            req_wrapper = RequestWrapper(
                url=url,
                method="POST",
                headers=headers,
                data=form_data,
                timeout=config.VLM_PROXY_TOTAL_REQUEST_TIMEOUT,
                keep_content_in_memory=True,
                on_success=cls._audio_transcription_success_callback,
                on_failure=cls._request_error_callback,
                user_data=user_data,
                max_retries=config.VLM_PROXY_RETRIES,
                retry_interval=config.VLM_PROXY_RETRY_INTERVAL
            )
            req_id = proxy_client.submit(req_wrapper)
            logger.debug(f"Forwarding request {req_id} (Stream: False) to VLM backend")
            await proxy_client.wait_for_upstream_status(req_id)
            return await proxy_client.json(req_id)

        except HttpException as e:
            raise e
        except HttpErrorWithContent as e:
            raise e
        except Exception as e:
            logger.error(f"Failed audio_transcriptions_non_stream async, err_msg: {e}")
            raise HttpException(f"音频转录请求失败: {str(e)}", "500")


    @classmethod
    async def audio_transcriptions_do_request(
        cls,
        request: Annotated[TranscriptionRequest, Form()],
        api_key: str,
        filename: str | None,
        content_type: str | None,
        file_content: bytes,
        raw_request: Request,
    ) -> Tuple[str, float]:
        """音频转录流式请求：只负责发起上游请求并返回 request_id。"""
        chat_log_type = 1
        model_name = request.model

        model_api_key, base_url, real_model_name, model_name, limiter_policy = await cls._get_model_config(
            model_name,
            api_key,
            include_limiter_policy=True,
        )
        request.model = real_model_name

        url = urljoin(base_url.rstrip('/') + '/', "/v1/audio/transcriptions")
        headers = {
            "Authorization": f"Bearer {model_api_key}",
        }
        
        # 透传user-agent
        if raw_request:
            user_agent = raw_request.headers.get("user-agent")
            if user_agent:
                headers["User-Agent"] = user_agent

        start_time = time.time()
        call_time = datetime.fromtimestamp(start_time)

        request.stream = True
        request.stream_include_usage = True
        request.stream_continuous_usage_stats = True

        safe_filename = filename or "audio"
        safe_content_type = content_type or "application/octet-stream"

        file_data = {
            'file': {
                'content': file_content,
                'filename': safe_filename,
                'content_type': safe_content_type
            }
        }

        form_fields = {
            'model': request.model,
            'language': request.language,
            'prompt': request.prompt,
            'response_format': request.response_format,
            'temperature': request.temperature,
            'timestamp_granularities': getattr(request, 'timestamp_granularities', None),
            'top_p': getattr(request, 'top_p', None),
            'top_k': getattr(request, 'top_k', None),
            'min_p': getattr(request, 'min_p', None),
            'seed': getattr(request, 'seed', None),
            'frequency_penalty': getattr(request, 'frequency_penalty', None),
            'repetition_penalty': getattr(request, 'repetition_penalty', None),
            'presence_penalty': getattr(request, 'presence_penalty', None),
            'to_language': getattr(request, 'to_language', None),
            'stream': getattr(request, 'stream', False),
            'stream_include_usage': getattr(request, 'stream_include_usage', True),
            'stream_continuous_usage_stats': getattr(request, 'stream_continuous_usage_stats', True)
        }

        form_data = cls._build_form_data_from_dict(form_fields, file_data)

        # 音频转录请求不使用 tools
        tools_count = 0
        limiter_context = await cls._acquire_pre_submit_limiter(
            limiter_policy=limiter_policy,
            model_name=model_name,
        )

        user_data = {
            "call_time": call_time,
            "api_key": api_key,
            "client_ip": cls._get_client_ip(raw_request),
            "chat_log_type": chat_log_type,
            "model_name": model_name,
            "real_model_name": real_model_name,
            "tools_count": tools_count,
            "limiter_context": limiter_context,
        }

        try:
            proxy_client = await cls.get_sse_proxy_client()
            req_wrapper = RequestWrapper(
                url=url,
                method="POST",
                headers=headers,
                data=form_data,
                is_stream=True,
                keep_content_in_memory=True,
                timeout=config.VLM_PROXY_TOTAL_REQUEST_TIMEOUT,
                on_success=cls._request_stream_finish_callback,
                on_failure=cls._request_stream_finish_callback,
                user_data=user_data,
                cancel_behavior=CancelBehavior.TRIGGER_SUCCESS,
                retry_on_stream_error=False,
                max_retries=config.VLM_PROXY_RETRIES,
                retry_interval=config.VLM_PROXY_RETRY_INTERVAL
            )
            req_id = proxy_client.submit(req_wrapper)
            logger.debug(f"Forwarding request {req_id} (Stream: True) to VLM backend")
            await proxy_client.wait_for_upstream_status(req_id)
            return req_id, start_time
        except HttpException as e:
            raise e
        except HttpErrorWithContent as e:
            raise e
        except Exception as e:
            raise HttpException(f"音频转录请求失败: {str(e)}", "500")


    @classmethod
    async def audio_transcriptions_get_response(
        cls,
        client_request_id: str,
        start_time: float,
        raw_request: Request,
    ) -> AsyncGenerator[Any, None]:
        """音频转录流式响应：只负责消费上游流并向下游转发。"""
        try:
            # asyncio.create_task(cls._check_client_disconnected(raw_request, client_request_id))
            proxy_client = await cls.get_sse_proxy_client()
            async for chunk in proxy_client.stream_generator_with_heartbeat(client_request_id):
                yield chunk
        except HttpException as e:
            raise e
        except HttpErrorWithContent as e:
            raise e
        except Exception as e:
            raise HttpException(f"音频转录流式响应失败: {str(e)}", "500")

    @classmethod
    async def audio_transcriptions_stream(cls, request: Annotated[TranscriptionRequest, Form()], api_key: str, filename: str, content_type: str, file_content: bytes, raw_request: Request) -> AsyncGenerator[Any, None]:
        """
        处理音频转录的流式代理请求
        Args:
            file: 音频文件
            req_params: 请求参数
            api_key: API密钥
            path: 请求路径
        Returns:
            响应数据
        """
        req_id, start_time = await cls.audio_transcriptions_do_request(
            request=request,
            api_key=api_key,
            filename=filename,
            content_type=content_type,
            file_content=file_content,
            raw_request=raw_request,
        )
        async for chunk in cls.audio_transcriptions_get_response(req_id, start_time, raw_request):
            yield chunk

    # 图片生成并发控制：允许同时处理最多 2 个请求
    image_generation_semaphore = asyncio.BoundedSemaphore(2)
    _image_generation_inflight: set[str] = set()
    _image_generation_limiter_contexts: Dict[str, Dict[str, Any]] = {}

    @classmethod
    async def image_generations_do_request(
        cls,
        req: Dict[str, Any],
        api_key: str,
        raw_request: Request,
    ) -> Tuple[str, float]:
        """图片生成流式请求：仅负责提交上游请求并返回 request_id。"""
        await cls.image_generation_semaphore.acquire()
        acquired = True
        req_id: str | None = None
        limiter_context: Dict[str, Any] | None = None

        try:
            if raw_request and await raw_request.is_disconnected():
                logger.warning("Client disconnected before processing image generation request.")
                raise HttpException("Client disconnected", code="499")

            chat_log_type = req.pop("chat_log_type", 1)
            model_name = req.get("model")
            if not model_name:
                raise HttpException("请求缺少'model'参数")

            model_api_key, base_url, real_model_name, model_name, limiter_policy = await cls._get_model_config(
                model_name,
                api_key,
                include_limiter_policy=True,
            )
            req["model"] = real_model_name

            url = urljoin(base_url.rstrip('/') + '/', "/v1/images/generations")
            headers = {
                "Authorization": f"Bearer {model_api_key}",
                "Content-Type": "application/json",
            }
            
            # 透传user-agent
            if raw_request:
                user_agent = raw_request.headers.get("user-agent")
                if user_agent:
                    headers["User-Agent"] = user_agent

            start_time = time.time()
            call_time = datetime.fromtimestamp(start_time)

            # 图片生成请求不使用 tools
            tools_count = 0
            limiter_context = await cls._acquire_pre_submit_limiter(
                limiter_policy=limiter_policy,
                model_name=model_name,
            )

            user_data = {
                "call_time": call_time,
                "api_key": api_key,
                "client_ip": cls._get_client_ip(raw_request),
                "chat_log_type": chat_log_type,
                "model_name": model_name,
                "real_model_name": real_model_name,
                "tools_count": tools_count,
                "limiter_context": limiter_context,
                "image_stream_handoff": True,
            }

            proxy_client = await cls.get_sse_proxy_client()
            req_wrapper = RequestWrapper(
                url=url,
                method="POST",
                headers=headers,
                json=req,
                is_stream=True,
                keep_content_in_memory=True,
                timeout=config.VLM_PROXY_TOTAL_REQUEST_TIMEOUT,
                on_success=cls._request_stream_finish_callback,
                on_failure=cls._request_stream_finish_callback,
                user_data=user_data,
                cancel_behavior=CancelBehavior.TRIGGER_SUCCESS,
                retry_on_stream_error=False,
                max_retries=config.VLM_PROXY_RETRIES,
                retry_interval=config.VLM_PROXY_RETRY_INTERVAL
            )
            req_id = proxy_client.submit(req_wrapper)
            cls._image_generation_inflight.add(req_id)
            cls._image_generation_limiter_contexts[req_id] = limiter_context
            logger.debug(f"Forwarding request {req_id} (Stream: True) to VLM backend")
            await proxy_client.wait_for_upstream_status(req_id)
            return req_id, start_time
        except HttpException as e:
            if req_id and req_id in cls._image_generation_inflight:
                cls._image_generation_inflight.remove(req_id)
            if req_id:
                cls._image_generation_limiter_contexts.pop(req_id, None)
            raise e
        except HttpErrorWithContent:
            if req_id and req_id in cls._image_generation_inflight:
                cls._image_generation_inflight.remove(req_id)
            if req_id:
                cls._image_generation_limiter_contexts.pop(req_id, None)
            raise
        except Exception as e:
            if req_id and req_id in cls._image_generation_inflight:
                cls._image_generation_inflight.remove(req_id)
            if req_id:
                cls._image_generation_limiter_contexts.pop(req_id, None)
            raise HttpException(f"图片生成请求失败: {str(e)}", "500")
        finally:
            # 仅当未成功“交接”给 get_response 时，才在这里释放并发名额
            if acquired and (req_id is None or req_id not in cls._image_generation_inflight):
                await cls._release_limiter_context_once(limiter_context)
                cls.image_generation_semaphore.release()


    @classmethod
    async def image_generations_get_response(
        cls,
        client_request_id: str,
        start_time: float,
        raw_request: Request,
    ) -> AsyncGenerator[Any, None]:
        """图片生成流式响应：仅负责转发上游流；结束时释放并发名额。"""
        proxy_client = None
        try:
            # asyncio.create_task(cls._check_client_disconnected(raw_request, client_request_id))
            proxy_client = await cls.get_sse_proxy_client()
            async for chunk in proxy_client.stream_generator_with_heartbeat(client_request_id):
                yield chunk
        except HttpException as e:
            raise e
        except HttpErrorWithContent as e:
            raise e
        except Exception as e:
            raise HttpException(f"图片生成流式响应失败: {str(e)}", "500")
        finally:
            is_alive = False
            if proxy_client is not None and hasattr(proxy_client, "is_alive"):
                try:
                    is_alive = bool(proxy_client.is_alive(client_request_id))
                except Exception:
                    is_alive = False

            if not is_alive and client_request_id in cls._image_generation_inflight:
                limiter_context = cls._image_generation_limiter_contexts.pop(client_request_id, None)
                await cls._release_limiter_context_once(limiter_context)
                cls._image_generation_inflight.remove(client_request_id)
                cls.image_generation_semaphore.release()

    @classmethod
    async def non_stream_image_generation(cls, req: Dict[str, Any], api_key: str,raw_request: Request) -> Any:
        """
        处理图像生成的非流式代理请求
        Args:
            req: 请求参数
            api_key: API密钥
        Returns:
            响应数据
        """
        await cls.image_generation_semaphore.acquire()
        limiter_context: Dict[str, Any] | None = None
        try:
            if raw_request:
                if await raw_request.is_disconnected():
                    logger.warning("Client disconnected before processing image generation request.")
                    raise HttpException("Client disconnected", code="499")
            
            chat_log_type = req.pop("chat_log_type", 1)
            model_name = req.get("model")
            if not model_name:
                raise HttpException("请求缺少'model'参数")
            
            # 从Redis获取模型配置
            model_api_key, base_url, real_model_name, model_name, limiter_policy = await cls._get_model_config(
                model_name,
                api_key,
                include_limiter_policy=True,
            )
            req["model"] = real_model_name  # 使用真实模型名称

            url = urljoin(base_url.rstrip('/') + '/','/' + "/v1/images/generations".lstrip('/'))
            
            headers = {
                "Authorization": f"Bearer {model_api_key}",
                "Content-Type": "application/json"
            }
            
            # 透传user-agent
            if raw_request:
                user_agent = raw_request.headers.get("user-agent")
                if user_agent:
                    headers["User-Agent"] = user_agent

            status = 1  # 调用状态：0失败 1成功
            prompt_tokens, completion_tokens, total_tokens = 0, 0, 0
            start_time = time.time()
            call_time = datetime.fromtimestamp(start_time)

            # 图片生成请求不使用 tools
            tools_count = 0
            limiter_context = await cls._acquire_pre_submit_limiter(
                limiter_policy=limiter_policy,
                model_name=model_name,
            )

            user_data = {
                "call_time": call_time,
                "api_key": api_key,
                "client_ip": cls._get_client_ip(raw_request),
                "chat_log_type": chat_log_type,
                "model_name": model_name,
                "real_model_name": real_model_name,
                "tools_count": tools_count,
                "limiter_context": limiter_context,
            }

            try:
                proxy_client = await cls.get_sse_proxy_client()
                req_wrapper = RequestWrapper(
                    url=url,
                    method="POST",
                    headers=headers,
                    json=req,
                    timeout=config.VLM_PROXY_TOTAL_REQUEST_TIMEOUT,
                    keep_content_in_memory=True,
                    on_success=cls._image_generation_success_callback,
                    on_failure=cls._request_error_callback,
                    user_data=user_data,
                    max_retries=config.VLM_PROXY_RETRIES,
                    retry_interval=config.VLM_PROXY_RETRY_INTERVAL
                )
                req_id = proxy_client.submit(req_wrapper)
                logger.debug(f"Forwarding request {req_id} (Stream: False) to VLM backend")
                await proxy_client.wait_for_upstream_status(req_id)
                return await proxy_client.json(req_id)
            except HttpException as e:
                raise e
            except HttpErrorWithContent as e:
                raise e
            except Exception as e:
                logger.error(f"Failed proxy_request_non_stream async,err_msg:{e}")
                await cls._release_limiter_context_once(limiter_context)
                raise HttpException(f"图片生成请求失败: {str(e)}", "500")
        finally:
            cls.image_generation_semaphore.release()

    
    @classmethod
    async def stream_image_generation(cls, req: Dict[str, Any], api_key: str,raw_request: Request) -> AsyncGenerator[Any, None]:
        """
        处理图像生成的流式代理请求
        Args:
            req: 请求参数
            api_key: API密钥
        Returns:
            响应数据
        """
        req_id, start_time = await cls.image_generations_do_request(req, api_key, raw_request)
        async for chunk in cls.image_generations_get_response(req_id, start_time, raw_request):
            yield chunk