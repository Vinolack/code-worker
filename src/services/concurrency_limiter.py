from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


LIMITER_MODE_OFF = "off"
LIMITER_MODE_OBSERVE = "observe"
LIMITER_MODE_ENFORCE = "enforce"

FAIL_POLICY_OPEN = "fail-open"
FAIL_POLICY_CLOSED = "fail-closed"

ERROR_POLICY_ACTION_NONE = "none"
ERROR_POLICY_ACTION_FAIL_OPEN_ALLOW = "fail_open_allow"
ERROR_POLICY_ACTION_FAIL_CLOSED_BLOCK = "fail_closed_block"

EVENT_ACQUIRE = "acquire"
EVENT_BLOCKED = "blocked"
EVENT_RELEASE = "release"
EVENT_REDIS_ERROR = "redis_error"

from redis import asyncio as aioredis

from src.base.constants.const import (
    KEY_NAMESPACE_SEPARATOR,
    KEY_NAMESPACE_TEMPLATE,
    LIMITER_KEY_NAMESPACE_MODULE,
    LIMITER_KEY_PREFIX,
    LIMITER_USER_MODEL_KEY_SCOPE,
    LIMITER_USER_TOTAL_KEY_SCOPE,
)
from src.config import config
from src.base.logging import logger


ACQUIRE_LUA_SCRIPT = """
local now_ms = tonumber(ARGV[1])
local ttl_ms = tonumber(ARGV[2])
local user_total_limit = tonumber(ARGV[3])
local user_model_limit = tonumber(ARGV[4])
local request_id = ARGV[5]
local expires_at = now_ms + ttl_ms

redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now_ms)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now_ms)

local current_total = redis.call('ZCARD', KEYS[1])
local current_model = redis.call('ZCARD', KEYS[2])

if current_total >= user_total_limit then
    return {0, 'user_total_limit', current_total, current_model}
end

if current_model >= user_model_limit then
    return {0, 'user_model_limit', current_total, current_model}
end

redis.call('ZADD', KEYS[1], expires_at, request_id)
redis.call('ZADD', KEYS[2], expires_at, request_id)
redis.call('PEXPIRE', KEYS[1], ttl_ms)
redis.call('PEXPIRE', KEYS[2], ttl_ms)

return {1, 'ok', current_total + 1, current_model + 1}
""".strip()


RELEASE_LUA_SCRIPT = """
local now_ms = tonumber(ARGV[1])
local ttl_ms = tonumber(ARGV[2])
local request_id = ARGV[3]

redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now_ms)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now_ms)

local removed_total = redis.call('ZREM', KEYS[1], request_id)
local removed_model = redis.call('ZREM', KEYS[2], request_id)

local removed = 0
if removed_total == 1 or removed_model == 1 then
    removed = 1
end

if redis.call('ZCARD', KEYS[1]) == 0 then
    redis.call('DEL', KEYS[1])
else
    redis.call('PEXPIRE', KEYS[1], ttl_ms)
end

if redis.call('ZCARD', KEYS[2]) == 0 then
    redis.call('DEL', KEYS[2])
else
    redis.call('PEXPIRE', KEYS[2], ttl_ms)
end

return {1, 'ok', removed}
""".strip()


RENEW_LUA_SCRIPT = """
local now_ms = tonumber(ARGV[1])
local ttl_ms = tonumber(ARGV[2])
local request_id = ARGV[3]
local expires_at = now_ms + ttl_ms

redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now_ms)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now_ms)

local exists_total = redis.call('ZSCORE', KEYS[1], request_id)
local exists_model = redis.call('ZSCORE', KEYS[2], request_id)

if (not exists_total) or (not exists_model) then
    return {0, 'owner_mismatch'}
end

redis.call('ZADD', KEYS[1], 'XX', expires_at, request_id)
redis.call('ZADD', KEYS[2], 'XX', expires_at, request_id)
redis.call('PEXPIRE', KEYS[1], ttl_ms)
redis.call('PEXPIRE', KEYS[2], ttl_ms)

return {1, 'ok'}
""".strip()


@dataclass(frozen=True)
class AcquireResult:
    granted: bool
    reason: str
    user_total_count: int
    user_model_count: int


@dataclass(frozen=True)
class ReleaseResult:
    ok: bool
    removed: bool


@dataclass(frozen=True)
class RenewResult:
    renewed: bool
    reason: str


@dataclass(frozen=True)
class ModeAwareAcquireResult:
    allowed: bool
    blocked: bool
    would_block: bool
    bypass: bool
    error_policy_action: str
    reason: str
    mode: str
    fail_policy: str
    acquire_result: Optional[AcquireResult] = None
    error: Optional[str] = None


class ConcurrencyLimiterService:
    def __init__(
        self,
        redis_client: Optional[Any] = None,
        event_hook: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ):
        self._owns_client = redis_client is None
        self._redis = redis_client or aioredis.Redis(
            host=config.LIMITER_REDIS_HOST,
            port=config.LIMITER_REDIS_PORT,
            password=config.LIMITER_REDIS_PASSWORD,
            db=config.LIMITER_REDIS_DB,
            decode_responses=True,
        )
        self._acquire_script = self._redis.register_script(ACQUIRE_LUA_SCRIPT)
        self._release_script = self._redis.register_script(RELEASE_LUA_SCRIPT)
        self._renew_script = self._redis.register_script(RENEW_LUA_SCRIPT)
        self._event_hook = event_hook
        self._event_counters: Dict[str, int] = {
            EVENT_ACQUIRE: 0,
            EVENT_BLOCKED: 0,
            EVENT_RELEASE: 0,
            EVENT_REDIS_ERROR: 0,
        }

    def _emit_event(self, event: str, **fields: Any) -> None:
        self._event_counters[event] = self._event_counters.get(event, 0) + 1
        if self._event_hook is not None:
            try:
                self._event_hook(event, fields)
            except Exception as hook_error:
                logger.warning(f"Limiter event hook failed: event={event}, error={hook_error}")

    def get_event_counters(self) -> Dict[str, int]:
        return dict(self._event_counters)

    async def close(self) -> None:
        if self._owns_client:
            await self._redis.aclose()

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    @staticmethod
    def _namespace_prefix() -> str:
        namespace = KEY_NAMESPACE_TEMPLATE.format(
            env=config.LIMITER_REDIS_ENV,
            service=config.LIMITER_REDIS_SERVICE,
            module=LIMITER_KEY_NAMESPACE_MODULE,
        )
        return f"{config.LIMITER_REDIS_PREFIX}{KEY_NAMESPACE_SEPARATOR}{namespace}"

    @classmethod
    def _user_total_key(cls, user_id: str) -> str:
        return (
            f"{cls._namespace_prefix()}{KEY_NAMESPACE_SEPARATOR}{LIMITER_KEY_PREFIX}"
            f"{KEY_NAMESPACE_SEPARATOR}{LIMITER_USER_TOTAL_KEY_SCOPE}{KEY_NAMESPACE_SEPARATOR}{user_id}"
        )

    @classmethod
    def _user_model_key(cls, user_id: str, model_name: str) -> str:
        return (
            f"{cls._namespace_prefix()}{KEY_NAMESPACE_SEPARATOR}{LIMITER_KEY_PREFIX}"
            f"{KEY_NAMESPACE_SEPARATOR}{LIMITER_USER_MODEL_KEY_SCOPE}{KEY_NAMESPACE_SEPARATOR}{user_id}"
            f"{KEY_NAMESPACE_SEPARATOR}{model_name}"
        )

    async def acquire(
        self,
        user_id: str,
        model_name: str,
        request_id: str,
        ttl_ms: int,
        user_total_limit: int,
        user_model_limit: int,
        now_ms: Optional[int] = None,
    ) -> AcquireResult:
        now = now_ms if now_ms is not None else self._now_ms()
        raw = await self._acquire_script(
            keys=[self._user_total_key(user_id), self._user_model_key(user_id, model_name)],
            args=[now, ttl_ms, user_total_limit, user_model_limit, request_id],
        )
        return AcquireResult(
            granted=bool(int(raw[0])),
            reason=str(raw[1]),
            user_total_count=int(raw[2]),
            user_model_count=int(raw[3]),
        )

    async def release(
        self,
        user_id: str,
        model_name: str,
        request_id: str,
        ttl_ms: int,
        now_ms: Optional[int] = None,
    ) -> ReleaseResult:
        now = now_ms if now_ms is not None else self._now_ms()
        raw = await self._release_script(
            keys=[self._user_total_key(user_id), self._user_model_key(user_id, model_name)],
            args=[now, ttl_ms, request_id],
        )
        release_result = ReleaseResult(ok=bool(int(raw[0])), removed=bool(int(raw[2])))
        self._emit_event(
            EVENT_RELEASE,
            user_id=user_id,
            model_name=model_name,
            request_id=request_id,
            removed=release_result.removed,
        )
        return release_result

    @staticmethod
    def _normalize_mode(mode: Optional[str]) -> str:
        mode_value = (mode or getattr(config, "LIMITER_MODE", LIMITER_MODE_ENFORCE) or LIMITER_MODE_ENFORCE).strip().lower()
        if mode_value not in {LIMITER_MODE_OFF, LIMITER_MODE_OBSERVE, LIMITER_MODE_ENFORCE}:
            return LIMITER_MODE_ENFORCE
        return mode_value

    @staticmethod
    def _normalize_fail_policy(fail_policy: Optional[str]) -> str:
        policy_value = (
            fail_policy
            or getattr(config, "LIMITER_FAIL_POLICY", FAIL_POLICY_OPEN)
            or FAIL_POLICY_OPEN
        ).strip().lower()
        if policy_value == "open":
            policy_value = FAIL_POLICY_OPEN
        if policy_value == "closed":
            policy_value = FAIL_POLICY_CLOSED
        if policy_value not in {FAIL_POLICY_OPEN, FAIL_POLICY_CLOSED}:
            return FAIL_POLICY_OPEN
        return policy_value

    @staticmethod
    def _error_action_for_policy(fail_policy: str) -> str:
        if fail_policy == FAIL_POLICY_CLOSED:
            return ERROR_POLICY_ACTION_FAIL_CLOSED_BLOCK
        return ERROR_POLICY_ACTION_FAIL_OPEN_ALLOW

    @staticmethod
    def _normalize_rollout_percent(rollout_percent: Optional[int]) -> int:
        value = rollout_percent
        if value is None:
            value = getattr(config, "LIMITER_ROLLOUT_PERCENT", 100)
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 100
        return max(0, min(100, parsed))

    @staticmethod
    def _is_user_in_rollout(user_id: str, rollout_percent: int) -> bool:
        if rollout_percent >= 100:
            return True
        if rollout_percent <= 0:
            return False
        bucket = int(hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:8], 16) % 100
        return bucket < rollout_percent

    def _from_redis_error(
        self,
        *,
        user_id: str,
        model_name: str,
        request_id: str,
        fail_policy: str,
        mode: str,
        exc: Exception,
    ) -> ModeAwareAcquireResult:
        allow = fail_policy == FAIL_POLICY_OPEN
        self._emit_event(
            EVENT_REDIS_ERROR,
            user_id=user_id,
            model_name=model_name,
            request_id=request_id,
            mode=mode,
            fail_policy=fail_policy,
            error=str(exc),
        )
        if not allow:
            self._emit_event(
                EVENT_BLOCKED,
                user_id=user_id,
                model_name=model_name,
                request_id=request_id,
                mode=mode,
                reason="redis_error",
            )
        return ModeAwareAcquireResult(
            allowed=allow,
            blocked=not allow,
            would_block=False,
            bypass=False,
            error_policy_action=self._error_action_for_policy(fail_policy),
            reason="redis_error",
            mode=mode,
            fail_policy=fail_policy,
            error=str(exc),
        )

    async def acquire_with_mode(
        self,
        user_id: str,
        model_name: str,
        request_id: str,
        ttl_ms: int,
        user_total_limit: int,
        user_model_limit: int,
        now_ms: Optional[int] = None,
        mode: Optional[str] = None,
        fail_policy: Optional[str] = None,
        rollout_percent: Optional[int] = None,
    ) -> ModeAwareAcquireResult:
        resolved_mode = self._normalize_mode(mode)
        resolved_fail_policy = self._normalize_fail_policy(fail_policy)
        resolved_rollout_percent = self._normalize_rollout_percent(rollout_percent)

        if resolved_mode == LIMITER_MODE_OFF:
            return ModeAwareAcquireResult(
                allowed=True,
                blocked=False,
                would_block=False,
                bypass=True,
                error_policy_action=ERROR_POLICY_ACTION_NONE,
                reason="mode_off_bypass",
                mode=resolved_mode,
                fail_policy=resolved_fail_policy,
            )

        if not self._is_user_in_rollout(user_id=user_id, rollout_percent=resolved_rollout_percent):
            return ModeAwareAcquireResult(
                allowed=True,
                blocked=False,
                would_block=False,
                bypass=True,
                error_policy_action=ERROR_POLICY_ACTION_NONE,
                reason="rollout_bypass",
                mode=resolved_mode,
                fail_policy=resolved_fail_policy,
            )

        try:
            acquire_result = await self.acquire(
                user_id=user_id,
                model_name=model_name,
                request_id=request_id,
                ttl_ms=ttl_ms,
                user_total_limit=user_total_limit,
                user_model_limit=user_model_limit,
                now_ms=now_ms,
            )
        except Exception as exc:
            return self._from_redis_error(
                user_id=user_id,
                model_name=model_name,
                request_id=request_id,
                fail_policy=resolved_fail_policy,
                mode=resolved_mode,
                exc=exc,
            )

        if acquire_result.granted:
            self._emit_event(
                EVENT_ACQUIRE,
                user_id=user_id,
                model_name=model_name,
                request_id=request_id,
                mode=resolved_mode,
            )
        else:
            self._emit_event(
                EVENT_BLOCKED,
                user_id=user_id,
                model_name=model_name,
                request_id=request_id,
                mode=resolved_mode,
                reason=acquire_result.reason,
            )

        if resolved_mode == LIMITER_MODE_OBSERVE:
            if acquire_result.granted:
                try:
                    await self.release(
                        user_id=user_id,
                        model_name=model_name,
                        request_id=request_id,
                        ttl_ms=ttl_ms,
                        now_ms=now_ms,
                    )
                except Exception as exc:
                    return self._from_redis_error(
                        user_id=user_id,
                        model_name=model_name,
                        request_id=request_id,
                        fail_policy=resolved_fail_policy,
                        mode=resolved_mode,
                        exc=exc,
                    )

            return ModeAwareAcquireResult(
                allowed=True,
                blocked=False,
                would_block=not acquire_result.granted,
                bypass=False,
                error_policy_action=ERROR_POLICY_ACTION_NONE,
                reason=acquire_result.reason,
                mode=resolved_mode,
                fail_policy=resolved_fail_policy,
                acquire_result=acquire_result,
            )

        return ModeAwareAcquireResult(
            allowed=acquire_result.granted,
            blocked=not acquire_result.granted,
            would_block=False,
            bypass=False,
            error_policy_action=ERROR_POLICY_ACTION_NONE,
            reason=acquire_result.reason,
            mode=resolved_mode,
            fail_policy=resolved_fail_policy,
            acquire_result=acquire_result,
        )

    async def renew(
        self,
        user_id: str,
        model_name: str,
        request_id: str,
        ttl_ms: int,
        now_ms: Optional[int] = None,
    ) -> RenewResult:
        now = now_ms if now_ms is not None else self._now_ms()
        raw = await self._renew_script(
            keys=[self._user_total_key(user_id), self._user_model_key(user_id, model_name)],
            args=[now, ttl_ms, request_id],
        )
        return RenewResult(renewed=bool(int(raw[0])), reason=str(raw[1]))
