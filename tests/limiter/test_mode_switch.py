import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.services.concurrency_limiter import ConcurrencyLimiterService


class FakeRedis:
    def __init__(self):
        self._zsets: dict[str, dict[str, int]] = {}
        self._scripts: list[str] = []

    def register_script(self, script: str):
        self._scripts.append(script)
        index = len(self._scripts)

        async def _runner(*, keys: list[str], args: list[Any]):
            if index == 1:
                return self._acquire(keys, args)
            if index == 2:
                return self._release(keys, args)
            if index == 3:
                return self._renew(keys, args)
            raise AssertionError("unexpected script registration")

        return _runner

    def _cleanup(self, key: str, now_ms: int) -> None:
        zset = self._zsets.get(key, {})
        self._zsets[key] = {member: score for member, score in zset.items() if score > now_ms}
        if not self._zsets[key]:
            self._zsets.pop(key, None)

    def _zcard(self, key: str) -> int:
        return len(self._zsets.get(key, {}))

    def _zadd(self, key: str, member: str, score: int) -> None:
        self._zsets.setdefault(key, {})[member] = score

    def _zrem(self, key: str, member: str) -> int:
        zset = self._zsets.get(key)
        if not zset or member not in zset:
            return 0
        del zset[member]
        if not zset:
            self._zsets.pop(key, None)
        return 1

    def _zscore(self, key: str, member: str) -> int | None:
        return self._zsets.get(key, {}).get(member)

    def _acquire(self, keys: list[str], args: list[Any]) -> list[Any]:
        now_ms, ttl_ms, total_limit, model_limit, request_id = args
        now_ms = int(now_ms)
        ttl_ms = int(ttl_ms)
        total_limit = int(total_limit)
        model_limit = int(model_limit)
        expires_at = now_ms + ttl_ms

        total_key, model_key = keys
        self._cleanup(total_key, now_ms)
        self._cleanup(model_key, now_ms)

        current_total = self._zcard(total_key)
        current_model = self._zcard(model_key)

        if current_total >= total_limit:
            return [0, "user_total_limit", current_total, current_model]
        if current_model >= model_limit:
            return [0, "user_model_limit", current_total, current_model]

        self._zadd(total_key, str(request_id), expires_at)
        self._zadd(model_key, str(request_id), expires_at)
        return [1, "ok", current_total + 1, current_model + 1]

    def _release(self, keys: list[str], args: list[Any]) -> list[Any]:
        now_ms, _, request_id = args
        now_ms = int(now_ms)
        total_key, model_key = keys

        self._cleanup(total_key, now_ms)
        self._cleanup(model_key, now_ms)

        removed_total = self._zrem(total_key, str(request_id))
        removed_model = self._zrem(model_key, str(request_id))
        removed = 1 if removed_total == 1 or removed_model == 1 else 0
        return [1, "ok", removed]

    def _renew(self, keys: list[str], args: list[Any]) -> list[Any]:
        now_ms, ttl_ms, request_id = args
        now_ms = int(now_ms)
        ttl_ms = int(ttl_ms)
        expires_at = now_ms + ttl_ms
        total_key, model_key = keys

        self._cleanup(total_key, now_ms)
        self._cleanup(model_key, now_ms)

        if self._zscore(total_key, str(request_id)) is None:
            return [0, "owner_mismatch"]
        if self._zscore(model_key, str(request_id)) is None:
            return [0, "owner_mismatch"]

        self._zadd(total_key, str(request_id), expires_at)
        self._zadd(model_key, str(request_id), expires_at)
        return [1, "ok"]


class ErrorRedis:
    def __init__(self):
        self._scripts: list[str] = []

    def register_script(self, script: str):
        self._scripts.append(script)
        index = len(self._scripts)

        async def _runner(*, keys: list[str], args: list[Any]):
            if index == 1:
                raise RuntimeError("redis unavailable")
            if index == 2:
                return [1, "ok", 1]
            if index == 3:
                return [1, "ok"]
            raise AssertionError("unexpected script registration")

        return _runner


def run(coro):
    return asyncio.run(coro)


def test_observe_mode_would_block_but_allow():
    service = ConcurrencyLimiterService(redis_client=FakeRedis())

    seed = run(
        service.acquire_with_mode(
            "user-1",
            "model-a",
            "req-1",
            ttl_ms=1000,
            user_total_limit=1,
            user_model_limit=1,
            now_ms=100,
            mode="enforce",
        )
    )
    assert seed.allowed is True

    observed = run(
        service.acquire_with_mode(
            "user-1",
            "model-a",
            "req-2",
            ttl_ms=1000,
            user_total_limit=1,
            user_model_limit=1,
            now_ms=101,
            mode="observe",
        )
    )

    assert observed.allowed is True
    assert observed.blocked is False
    assert observed.would_block is True
    assert observed.bypass is False
    assert observed.error_policy_action == "none"
    assert observed.reason == "user_total_limit"


def test_enforce_mode_blocks_when_limit_hit():
    service = ConcurrencyLimiterService(redis_client=FakeRedis())

    first = run(
        service.acquire_with_mode(
            "user-2",
            "model-a",
            "req-1",
            ttl_ms=1000,
            user_total_limit=1,
            user_model_limit=1,
            now_ms=200,
            mode="enforce",
        )
    )
    assert first.allowed is True

    second = run(
        service.acquire_with_mode(
            "user-2",
            "model-b",
            "req-2",
            ttl_ms=1000,
            user_total_limit=1,
            user_model_limit=1,
            now_ms=201,
            mode="enforce",
        )
    )

    assert second.allowed is False
    assert second.blocked is True
    assert second.would_block is False
    assert second.bypass is False
    assert second.error_policy_action == "none"
    assert second.reason == "user_total_limit"


def test_off_mode_bypasses_limiter_check():
    service = ConcurrencyLimiterService(redis_client=ErrorRedis())

    result = run(
        service.acquire_with_mode(
            "user-0",
            "model-x",
            "req-off",
            ttl_ms=1000,
            user_total_limit=1,
            user_model_limit=1,
            now_ms=50,
            mode="off",
        )
    )

    assert result.allowed is True
    assert result.blocked is False
    assert result.would_block is False
    assert result.bypass is True
    assert result.error_policy_action == "none"
    assert result.reason == "mode_off_bypass"


def test_fail_open_on_redis_exception_allows_request():
    service = ConcurrencyLimiterService(redis_client=ErrorRedis())

    result = run(
        service.acquire_with_mode(
            "user-3",
            "model-x",
            "req-1",
            ttl_ms=1000,
            user_total_limit=1,
            user_model_limit=1,
            now_ms=300,
            mode="enforce",
            fail_policy="fail-open",
        )
    )

    assert result.allowed is True
    assert result.blocked is False
    assert result.would_block is False
    assert result.bypass is False
    assert result.reason == "redis_error"
    assert result.error_policy_action == "fail_open_allow"


def test_fail_closed_on_redis_exception_blocks_request():
    service = ConcurrencyLimiterService(redis_client=ErrorRedis())

    result = run(
        service.acquire_with_mode(
            "user-4",
            "model-x",
            "req-1",
            ttl_ms=1000,
            user_total_limit=1,
            user_model_limit=1,
            now_ms=400,
            mode="enforce",
            fail_policy="fail-closed",
        )
    )

    assert result.allowed is False
    assert result.blocked is True
    assert result.would_block is False
    assert result.bypass is False
    assert result.reason == "redis_error"
    assert result.error_policy_action == "fail_closed_block"


def test_rollout_bypass_outside_hash_bucket_avoids_redis():
    service = ConcurrencyLimiterService(redis_client=ErrorRedis())
    subject = None
    for idx in range(256):
        candidate = f"rollout-bypass-{idx}"
        if not service._is_user_in_rollout(candidate, 1):
            subject = candidate
            break
    assert subject is not None

    result = run(
        service.acquire_with_mode(
            subject,
            "model-x",
            "req-rollout-off",
            ttl_ms=1000,
            user_total_limit=1,
            user_model_limit=1,
            now_ms=401,
            mode="enforce",
            rollout_percent=1,
        )
    )

    assert result.allowed is True
    assert result.blocked is False
    assert result.bypass is True
    assert result.reason == "rollout_bypass"


def test_rollout_in_hash_bucket_still_enforces_limits():
    service = ConcurrencyLimiterService(redis_client=FakeRedis())
    subject = None
    for idx in range(2048):
        candidate = f"rollout-enforce-{idx}"
        if service._is_user_in_rollout(candidate, 1):
            subject = candidate
            break
    assert subject is not None

    first = run(
        service.acquire_with_mode(
            subject,
            "model-a",
            "req-rollout-1",
            ttl_ms=1000,
            user_total_limit=1,
            user_model_limit=1,
            now_ms=500,
            mode="enforce",
            rollout_percent=1,
        )
    )
    assert first.allowed is True

    second = run(
        service.acquire_with_mode(
            subject,
            "model-b",
            "req-rollout-2",
            ttl_ms=1000,
            user_total_limit=1,
            user_model_limit=1,
            now_ms=501,
            mode="enforce",
            rollout_percent=1,
        )
    )

    assert second.allowed is False
    assert second.blocked is True
    assert second.bypass is False
    assert second.reason == "user_total_limit"
