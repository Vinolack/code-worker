#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
'''
@File    ：api-key.py
@Author  ：even_lin
@Date    ：2025/6/25 11:48 
@Desc     : {模块描述}
'''
import base64
import hashlib
from typing import Any, Dict, List, Optional, Tuple

from src.api_models.api_key.perm_msg_code_enum import CodePlanApiKeyPermMsgCodeEnum
from src.api_models.api_key.resp_model import CodePlanApiKeyDigestOnlyResp, CodePlanApiKeyPermResp, CodePlanApiKeySyncValueResp
from src.base.constants.const import (
    CODE_PLAN_API_KEY_DIGEST_ONLY_CACHE_PREFIX,
    CODE_PLAN_API_KEY_PERM_CACHE_PREFIX,
    CODE_PLAN_USER_API_KEY_AUTH_CACHE_PREFIX,
    LEGACY_KEY_NAMESPACE_MODULE,
    USER_API_KEY_SET_PREFIX,
    build_governed_key,
    build_governed_key_prefix,
)
from src.base.enums.error import BizErrCodeEnum
from src.base.exceptions.base import HttpException
from src.base.logging import logger
from src.base.utils.uuid_util import UUIDUtil
from src.config import config
import httpx

from src.dao.redis import RedisManager
from src.decorators.timing import timing_logger
from src.services.chat_log import ChatLogReporter
from src.utils.api_key_digest_util import ApiKeyDigestUtil


class ApiKeyService:
    _shared_client: Optional[httpx.AsyncClient] = None

    def __init__(self):
        self.sync_url = config.SYNC_API_KEYS_URL
        self.sync_url_v2 = config.SYNC_API_KEYS_URL_V2
        if ApiKeyService._shared_client is None or ApiKeyService._shared_client.is_closed:
            limits = httpx.Limits(
                max_connections=200,
                max_keepalive_connections=100,
                keepalive_expiry=30.0,
            )
            timeout = httpx.Timeout(
                connect=3.0,
                read=15.0,
                write=15.0,
                pool=2.0,
            )
            ApiKeyService._shared_client = httpx.AsyncClient(timeout=timeout, limits=limits)
        self.client = ApiKeyService._shared_client
        self.magic_secret = config.SERVICE_CHECK_MAGIC_SECRET

    @classmethod
    async def close_shared_client(cls):
        if cls._shared_client is not None and not cls._shared_client.is_closed:
            await cls._shared_client.aclose()
        cls._shared_client = None

    @staticmethod
    def _legacy_governed_key_prefix(prefix: str) -> str:
        return build_governed_key_prefix(
            prefix=prefix,
            env=config.LEGACY_REDIS_ENV,
            service=config.LEGACY_REDIS_SERVICE,
            module=LEGACY_KEY_NAMESPACE_MODULE,
        )

    @staticmethod
    def _legacy_governed_key(prefix: str, *parts: str) -> str:
        return build_governed_key(
            prefix=prefix,
            env=config.LEGACY_REDIS_ENV,
            service=config.LEGACY_REDIS_SERVICE,
            module=LEGACY_KEY_NAMESPACE_MODULE,
            parts=parts,
        )

    async def check_api_key(self, api_key: str, is_digest: bool = True) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """
        检查API密钥是否有效
        :param api_key: API密钥
        :param is_digest: API密钥是否是摘要
        :return: (is_valid, api_key_digest, user_info)
                 - is_valid: 是否有效
                 - user_info: 用户信息 {"uid": xxx, "level": xxx}，无效时为 None
                 - api_key_digest: API Key 摘要，无效时为 None
        """
        if not api_key:
            return False, None, None

        if not is_digest:
            # 生成摘要
            api_key = self.create_short_key_digest(api_key)

        try:
            redis_json: Any = RedisManager.client.json()
            governed_key = self._legacy_governed_key_prefix(USER_API_KEY_SET_PREFIX)
            info = await redis_json.get(governed_key, f'$.{api_key}')
            if (not info or len(info) == 0) and governed_key != USER_API_KEY_SET_PREFIX:
                info = await redis_json.get(USER_API_KEY_SET_PREFIX, f'$.{api_key}')
            if info and len(info) > 0:
                user_info = info[0]
                logger.debug(f"API key '{api_key}' is valid. User info: {user_info}")
                return True, api_key, user_info
            else:
                logger.warning(f"Invalid API key provided: '{api_key}'")
                return False, None, None
        except Exception as e:
            logger.error(f"An error occurred while checking API key '{api_key}' in Redis: {e}", exc_info=True)
            return False, None, None

    async def check_api_key_v2(self, api_key: str, is_digest: bool = True) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """
        V2：通过外部接口校验 API Key 摘要
        :param api_key: API Key（原始或摘要）
        :param is_digest: API Key 是否已经是摘要
        :return: (is_valid, api_key_digest, auth_info)
                 - is_valid: 是否允许调用
                 - api_key_digest: 传入的摘要（无效时为 None）
                 - auth_info: 鉴权信息 {"allowed": bool, "remaining_quota": float, "msg": str}
        """
        if not api_key:
            return False, None, None

        if not is_digest:
            api_key = self.create_short_key_digest(api_key)
        
        api_key_digest = api_key

        cached_info = await self._get_auth_cache(api_key_digest)
        if cached_info is not None:
            cached_payload = self._normalize_perm_auth_payload(cached_info)
            allowed = bool(cached_payload.get("allowed", False))
            if not allowed:
                return False, None, cached_payload
            return True, api_key_digest, cached_payload

        return await self._remote_auth_check(api_key_digest)

    async def check_api_key_v3(self, api_key: str, is_digest: bool = True) -> Tuple[bool, Optional[str], Optional[CodePlanApiKeyDigestOnlyResp]]:
        """
        V3：通过外部接口校验 API Key 摘要（仅校验digest，不写入上下文）
        :param api_key: API Key（原始或摘要）
        :param is_digest: API Key 是否已经是摘要
        :return: (is_valid, api_key_digest, auth_info)
                 - is_valid: 是否允许调用
                 - api_key_digest: 传入的摘要（无效时为 None）
                 - auth_info: 鉴权信息 CodePlanApiKeyDigestOnlyResp
        """
        if not api_key:
            return False, None, None

        if not is_digest:
            api_key = self.create_short_key_digest(api_key)

        api_key_digest = api_key

        cached_info = await self._get_auth_cache_v3(api_key_digest)
        if cached_info is not None:
            auth_info = CodePlanApiKeyDigestOnlyResp(**cached_info)
            return auth_info.allowed, api_key_digest, auth_info

        return await self._remote_auth_check_v3(api_key_digest)


    @timing_logger(prefix="_remote_auth_check_v3", log_level="info", time_unit="ms")
    async def _remote_auth_check_v3(self, api_key_digest: str) -> Tuple[bool, Optional[str], Optional[CodePlanApiKeyDigestOnlyResp]]:
        """调用远程接口校验 API Key（v3版本，使用 cache_seconds）"""
        try:
            auth_url = f"{config.BACKEND_URL.rstrip('/')}/api/code_plan_to_worker/auth/code_plan_api_key_digest_only"
            params = {"api_key_digest": api_key_digest}
            headers = ChatLogReporter.generate_auth_headers("get")

            response = await self.client.get(auth_url, params=params, headers=headers)
            response.raise_for_status()

            resp_json = response.json()
            logger.debug(f"check_api_key_v3 response: {resp_json}")

            data = (resp_json or {}).get("data") or {}
            auth_info = CodePlanApiKeyDigestOnlyResp(**data)
            cache_seconds = auth_info.cache_seconds

            await self._set_auth_cache_v3(api_key_digest, auth_info.model_dump(), cache_seconds)
            return auth_info.allowed, api_key_digest, auth_info

        except httpx.HTTPStatusError as e:
            logger.error(f"check_api_key_v3 HTTP error: {e.response.status_code}, response: {e.response.text}")
            return False, None, None
        except Exception as e:
            logger.error(f"check_api_key_v3 unexpected error: {e}", exc_info=True)
            return False, None, None


    async def _get_auth_cache_v3(self, api_key_digest: str) -> Optional[Dict]:
        """从 Redis 获取 API Key 鉴权缓存（v3版本）"""
        try:
            cache_key = self._legacy_governed_key(CODE_PLAN_API_KEY_DIGEST_ONLY_CACHE_PREFIX, api_key_digest)
            cached = await RedisManager.client.get(cache_key)
            if cached is None:
                legacy_cache_key = f"{CODE_PLAN_API_KEY_DIGEST_ONLY_CACHE_PREFIX}:{api_key_digest}"
                cached = await RedisManager.client.get(legacy_cache_key)
            if cached:
                import json
                cached_info = json.loads(cached)
                return cached_info
        except Exception as e:
            logger.warning(f"Redis cache error for '{api_key_digest}' (v3), fallback to remote: {e}")
        return None

    async def _set_auth_cache_v3(self, api_key_digest: str, auth_data: Dict, ttl: int = 300) -> None:
        """
        将鉴权结果写入 Redis 缓存（v3版本）
        :param api_key_digest: API Key 摘要
        :param auth_data: 鉴权数据
        :param ttl: 缓存过期时间（秒）
        """
        try:
            import json
            cache_key = self._legacy_governed_key(CODE_PLAN_API_KEY_DIGEST_ONLY_CACHE_PREFIX, api_key_digest)
            cache_data = json.dumps(auth_data)
            await RedisManager.client.set(cache_key, cache_data, ex=ttl)
            logger.debug(f"Cached auth result for '{api_key_digest}' with TTL {ttl}s (v3)")
        except Exception as e:
            logger.warning(f"Failed to cache auth result for '{api_key_digest}' (v3): {e}")

    def _calculate_cache_ttl(self, remaining_quota: float) -> int:
        """
        根据剩余配额计算缓存过期时间
        :param remaining_quota: 剩余配额
        :return: 缓存过期时间（秒）
        """
        if remaining_quota <= 0:
            # 配额用完，缓存 60 秒
            return 60
        if remaining_quota < 50:
            return 30
        elif remaining_quota < 100:
            return 60
        elif remaining_quota < 500:
            return 90
        elif remaining_quota < 2000:
            return 90
        else:
            # 配额充足
            return 300

    async def _get_auth_cache(self, api_key_digest: str) -> Optional[Dict]:
        try:
            cache_key = self._legacy_governed_key(CODE_PLAN_USER_API_KEY_AUTH_CACHE_PREFIX, api_key_digest)
            cached = await RedisManager.client.get(cache_key)
            if cached is None:
                legacy_cache_key = f"{CODE_PLAN_USER_API_KEY_AUTH_CACHE_PREFIX}:{api_key_digest}"
                cached = await RedisManager.client.get(legacy_cache_key)
            if cached:
                import json
                cached_info = json.loads(cached)
                return cached_info
        except Exception as e:
            logger.warning(f"Redis cache error for '{api_key_digest}', fallback to remote: {e}")
        return None

    async def _set_auth_cache(self, api_key_digest: str, auth_data: Dict, ttl: int = 300) -> None:
        """
        将鉴权结果写入 Redis Hash 缓存
        :param api_key_digest: API Key 摘要
        :param auth_data: 鉴权数据
        :param ttl: 缓存过期时间（秒），默认 300 秒
        """
        try:
            import json
            cache_key = self._legacy_governed_key(CODE_PLAN_USER_API_KEY_AUTH_CACHE_PREFIX, api_key_digest)
            cache_data = json.dumps(auth_data)
            await RedisManager.client.set(cache_key, cache_data, ex=ttl)
            logger.debug(f"Cached auth result for '{api_key_digest}' with TTL {ttl}s")
        except Exception as e:
            logger.warning(f"Failed to cache auth result for '{api_key_digest}': {e}")

    @staticmethod
    def _normalize_perm_auth_payload(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        raw_payload = dict(payload or {})
        try:
            perm_resp = CodePlanApiKeyPermResp(**raw_payload)
        except Exception as parse_error:
            logger.warning(f"Invalid api key perm payload, fallback to safe defaults: {parse_error}")
            fallback_payload = {
                "allowed": bool(raw_payload.get("allowed", False)),
                "cache_seconds": 60,
            }
            perm_resp = CodePlanApiKeyPermResp(**fallback_payload)
        return perm_resp.model_dump()

    @timing_logger(prefix="_remote_auth_check", log_level="info", time_unit="ms")
    async def _remote_auth_check(self, api_key_digest: str) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """调用远程接口校验 API Key"""
        try:
            auth_url = f"{config.BACKEND_URL.rstrip('/')}/api/code_plan_to_worker/auth/code_plan_api_key_perm"
            params = {"api_key_digest": api_key_digest}
            headers = ChatLogReporter.generate_auth_headers("get")

            response = await self.client.get(auth_url, params=params, headers=headers)
            response.raise_for_status()

            resp_json = response.json()
            logger.debug(f"check_api_key_v2 response: {resp_json}")

            data = (resp_json or {}).get("data") or {}
            normalized_payload = self._normalize_perm_auth_payload(data)
            allowed = bool(normalized_payload.get("allowed", False))

            if not allowed:
                await self._set_auth_cache(api_key_digest, normalized_payload, 60)
                return False, None, normalized_payload

            await self._set_auth_cache(api_key_digest, normalized_payload, 90)
            return True, api_key_digest, normalized_payload

        except httpx.HTTPStatusError as e:
            logger.error(f"check_api_key_v2 HTTP error: {e.response.status_code}, response: {e.response.text}")
            return False, None, None
        except Exception as e:
            logger.error(f"check_api_key_v2 unexpected error: {e}", exc_info=True)
            return False, None, None

    async def sync_api_keys(self):
        """
        同步API密钥
        :return: True/False
        """
        try:
            logger.info(f"Syncing api keys from {self.sync_url_v2}")
            auth_headers = ChatLogReporter.generate_auth_headers("get")
            response = await self.client.get(self.sync_url_v2, headers=auth_headers)
            response.raise_for_status()  # 状态码为4xx或5xx时抛出异常

            response_json = response.json()
            logger.debug(f"api_key Response: {response_json}")
            api_key_map: Dict[str, Any] = response_json["data"]
            logger.info(f"Parsing {len(api_key_map)} API keys from response map...")
            await self.load_api_keys_to_redis(api_key_map)
            logger.info(f"Successfully sync api-keys. Status: {response.status_code}")
            return True
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to sync api-key, HTTP error: {e.response.status_code}, response: {e.response.text}")
            return False
        except Exception as e:
            logger.error(f"An unexpected error occurred during sync api-key: {e}", exc_info=True)
            return False

    async def load_api_keys_to_redis(self, api_key_map: Dict[str, Any]):
        """
        从数据库加载所有有效的API Key到Redis JSON中。
        :param api_key_map: API Key 映射 {original_api_key: {uid, level}}
        """
        logger.info("Starting to load all valid API keys to Redis JSON...")
        try:
            # 无数据，则清空Redis JSON
            target_key = self._legacy_governed_key_prefix(USER_API_KEY_SET_PREFIX)
            if not api_key_map:
                logger.warning("No valid API keys found in the database. The Redis JSON will be cleared.")
                redis_json: Any = RedisManager.client.json()
                delete_op: Any = redis_json.delete(target_key)
                await delete_op
                return

            # 构造 JSON 数据，key 已经是摘要
            api_keys_dict = {}
            for api_key_digest, value_resp in api_key_map.items():
                api_keys_dict[api_key_digest] = {
                    "uid": value_resp["uid"],
                    "level": value_resp["level"]
                }

            # 添加 magic_secret 的摘要
            if self.magic_secret and len(self.magic_secret) > 0:
                magic_digest = self.create_short_key_digest(self.magic_secret)
                api_keys_dict[magic_digest] = {"uid": 0, "level": 0}

            # rename 实现原子替换
            temp_key = self._legacy_governed_key(
                USER_API_KEY_SET_PREFIX,
                "temp",
                UUIDUtil.generate_random_string(10),
            )
            redis_json: Any = RedisManager.client.json()
            set_op: Any = redis_json.set(temp_key, '$', api_keys_dict)
            await set_op
            rename_op: Any = RedisManager.client.rename(temp_key, target_key)
            await rename_op
            logger.info(f"Successfully loaded {len(api_keys_dict)} API keys into Redis JSON '{target_key}'.")
        except Exception as e:
            logger.error(f"Failed to load API keys to Redis JSON: {e}", exc_info=True)
            raise

    def create_short_key_digest(self, api_key: str) -> str:
        """
        为给定的API Key生成一个简短且安全的SHA-256摘要。
        """
        return ApiKeyDigestUtil.create_short_key_digest(api_key)

    async def get_code_plan_api_key_perm(self, api_key_digest: str, model_name: str) -> CodePlanApiKeyPermResp:
        """
        获取 API Key 权限信息
        :param api_key_digest: API Key 摘要
        :param model_name: 模型名称
        :return: CodePlanApiKeyPermResp 权限响应
        """
        cache_key = self._build_perm_cache_key(api_key_digest, model_name)
        cached = await self._get_perm_cache(cache_key)
        if cached is None:
            legacy_cache_key = f"{CODE_PLAN_API_KEY_PERM_CACHE_PREFIX}:{api_key_digest}:{model_name}"
            cached = await self._get_perm_cache(legacy_cache_key)
        if cached is not None:
            return CodePlanApiKeyPermResp(**cached)

        perm_resp = await self._remote_get_code_plan_api_key_perm(api_key_digest, model_name)
        if perm_resp and perm_resp.cache_seconds and perm_resp.cache_seconds > 0:
            await self._set_perm_cache(cache_key, perm_resp.model_dump(), perm_resp.cache_seconds)
        return perm_resp

    def _build_perm_cache_key(self, api_key_digest: str, model_name: str) -> str:
        """构建权限缓存 key"""
        return self._legacy_governed_key(CODE_PLAN_API_KEY_PERM_CACHE_PREFIX, api_key_digest, model_name)

    async def _get_perm_cache(self, cache_key: str) -> Optional[Dict]:
        """从 Redis 获取权限缓存"""
        try:
            cached = await RedisManager.client.get(cache_key)
            if cached:
                import json
                return json.loads(cached)
        except Exception as e:
            logger.warning(f"Redis perm cache error for '{cache_key}': {e}")
        return None

    async def _set_perm_cache(self, cache_key: str, perm_data: Dict, ttl: int = 300) -> None:
        """将权限结果写入 Redis 缓存"""
        try:
            import json
            cache_data = json.dumps(perm_data)
            await RedisManager.client.set(cache_key, cache_data, ex=ttl)
            logger.debug(f"Cached perm result for '{cache_key}' with TTL {ttl}s")
        except Exception as e:
            logger.warning(f"Failed to cache perm result for '{cache_key}': {e}")

    async def _remote_get_code_plan_api_key_perm(self, api_key_digest: str, model_name: str) -> CodePlanApiKeyPermResp:
        """调用远程接口获取 API Key 权限"""
        try:
            perm_url = f"{config.BACKEND_URL.rstrip('/')}/api/code_plan_to_worker/auth/code_plan_api_key_perm"
            params = {"api_key_digest": api_key_digest, "model_name": model_name}
            headers = ChatLogReporter.generate_auth_headers("get")

            response = await self.client.get(perm_url, params=params, headers=headers)
            response.raise_for_status()

            resp_json = response.json()
            logger.debug(f"get_code_plan_api_key_perm response: {resp_json}")

            data = (resp_json or {}).get("data") or {}
            return CodePlanApiKeyPermResp(**data)

        except httpx.HTTPStatusError as e:
            logger.error(f"get_code_plan_api_key_perm HTTP error: {e.response.status_code}, response: {e.response.text}")
            return CodePlanApiKeyPermResp(allowed=False, cache_seconds=60)
        except Exception as e:
            logger.error(f"get_code_plan_api_key_perm unexpected error: {e}", exc_info=True)
            return CodePlanApiKeyPermResp(allowed=False, cache_seconds=60)

    async def check_code_plan_api_key_perm(self, api_key_digest: str, model_name: str) -> CodePlanApiKeyPermResp:
        """
        检查 API Key 权限并根据结果抛出异常
        :param api_key_digest: API Key 摘要
        :param model_name: 模型名称
        :return: CodePlanApiKeyPermResp 权限响应
        :raises HttpException:
            - 401: 无效API Key (msg_code=1001)
            - 500: 内部异常 (msg_code=9999)
            - 403: 禁止访问 (其它情况)
        """
        perm_resp:CodePlanApiKeyPermResp = await self.get_code_plan_api_key_perm(api_key_digest, model_name)

        if perm_resp is None:
            raise HttpException(msg="系统鉴权异常，请稍后重试～",code="500")

        fallback_code = CodePlanApiKeyPermMsgCodeEnum.INTERNAL_ERROR.value[0]
        msg_code = int(perm_resp.msg_code) if perm_resp.msg_code is not None else fallback_code

        msg_code_enum = CodePlanApiKeyPermMsgCodeEnum.get_by_code(msg_code)
        if msg_code_enum is None:
            msg_code_enum = CodePlanApiKeyPermMsgCodeEnum.INTERNAL_ERROR

        if msg_code == CodePlanApiKeyPermMsgCodeEnum.SUCCESS.code:
            return perm_resp
        elif msg_code == CodePlanApiKeyPermMsgCodeEnum.INTERNAL_ERROR.code:
            raise HttpException(msg=msg_code_enum.desc,code="500")
        elif msg_code == CodePlanApiKeyPermMsgCodeEnum.INVALID_API_KEY.code:
            raise HttpException(msg=msg_code_enum.desc,code="401")
        elif msg_code == CodePlanApiKeyPermMsgCodeEnum.MODEL_NOT_EXIST.code:
            raise HttpException(msg=msg_code_enum.desc,code="404")
        elif msg_code == CodePlanApiKeyPermMsgCodeEnum.NO_API_PERMISSION.code:
            raise HttpException(msg=msg_code_enum.desc,code="402")
        elif msg_code in (CodePlanApiKeyPermMsgCodeEnum.MODEL_NOT_OPEN.code, CodePlanApiKeyPermMsgCodeEnum.NO_MODEL_PERMISSION.code):
            raise HttpException(msg=msg_code_enum.desc,code="403")
        elif msg_code == CodePlanApiKeyPermMsgCodeEnum.QUOTA_INSUFFICIENT.code:
            raise HttpException(msg=msg_code_enum.desc,code="429")
        else:
            raise HttpException(msg=msg_code_enum.desc,code="403")
