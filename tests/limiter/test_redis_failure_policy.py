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
        script_index = len(self._scripts)

        async def _runner(*, keys: list[str], args: list[Any]):
            if script_index == 1:
                return self._acquire(keys, args)
            if script_index == 2:
                return self._release(keys, args)
            if script_index == 3:
                return [1, "ok"]
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


class AcquireErrorRedis:
    def __init__(self):
        self._scripts: list[str] = []

    def register_script(self, script: str):
        self._scripts.append(script)
        script_index = len(self._scripts)

        async def _runner(*, keys: list[str], args: list[Any]):
            if script_index == 1:
                raise RuntimeError("redis unavailable")
            if script_index == 2:
                return [1, "ok", 1]
            if script_index == 3:
                return [1, "ok"]
            raise AssertionError("unexpected script registration")

        return _runner


def run(coro):
    return asyncio.run(coro)


def test_fail_open_policy_allows_on_redis_error_with_metrics():
    service = ConcurrencyLimiterService(redis_client=AcquireErrorRedis())

    result = run(
        service.acquire_with_mode(
            user_id="u-1",
            model_name="m-1",
            request_id="req-1",
            ttl_ms=1000,
            user_total_limit=1,
            user_model_limit=1,
            now_ms=100,
            mode="enforce",
            fail_policy="fail-open",
        )
    )

    counters = service.get_event_counters()
    assert result.allowed is True
    assert result.blocked is False
    assert result.reason == "redis_error"
    assert counters["redis_error"] == 1
    assert counters["blocked"] == 0


def test_fail_closed_policy_blocks_on_redis_error_with_metrics():
    service = ConcurrencyLimiterService(redis_client=AcquireErrorRedis())

    result = run(
        service.acquire_with_mode(
            user_id="u-2",
            model_name="m-1",
            request_id="req-1",
            ttl_ms=1000,
            user_total_limit=1,
            user_model_limit=1,
            now_ms=100,
            mode="enforce",
            fail_policy="fail-closed",
        )
    )

    counters = service.get_event_counters()
    assert result.allowed is False
    assert result.blocked is True
    assert result.reason == "redis_error"
    assert counters["redis_error"] == 1
    assert counters["blocked"] == 1


def test_mode_semantics_off_observe_enforce_are_explicit():
    service = ConcurrencyLimiterService(redis_client=FakeRedis())

    seed = run(
        service.acquire_with_mode(
            user_id="u-3",
            model_name="m-1",
            request_id="req-1",
            ttl_ms=1000,
            user_total_limit=1,
            user_model_limit=1,
            now_ms=100,
            mode="enforce",
        )
    )
    enforce_blocked = run(
        service.acquire_with_mode(
            user_id="u-3",
            model_name="m-2",
            request_id="req-2",
            ttl_ms=1000,
            user_total_limit=1,
            user_model_limit=1,
            now_ms=101,
            mode="enforce",
        )
    )
    observe = run(
        service.acquire_with_mode(
            user_id="u-3",
            model_name="m-2",
            request_id="req-3",
            ttl_ms=1000,
            user_total_limit=1,
            user_model_limit=1,
            now_ms=102,
            mode="observe",
        )
    )
    off = run(
        service.acquire_with_mode(
            user_id="u-3",
            model_name="m-2",
            request_id="req-4",
            ttl_ms=1000,
            user_total_limit=1,
            user_model_limit=1,
            now_ms=103,
            mode="off",
        )
    )

    counters = service.get_event_counters()
    assert seed.allowed is True
    assert seed.blocked is False
    assert enforce_blocked.allowed is False
    assert enforce_blocked.blocked is True
    assert observe.allowed is True
    assert observe.would_block is True
    assert observe.blocked is False
    assert off.allowed is True
    assert off.bypass is True
    assert counters["acquire"] == 1
    assert counters["blocked"] == 2


def test_release_event_is_observable():
    service = ConcurrencyLimiterService(redis_client=FakeRedis())

    acquire = run(
        service.acquire_with_mode(
            user_id="u-4",
            model_name="m-1",
            request_id="req-1",
            ttl_ms=1000,
            user_total_limit=1,
            user_model_limit=1,
            now_ms=100,
            mode="enforce",
        )
    )
    release = run(
        service.release(
            user_id="u-4",
            model_name="m-1",
            request_id="req-1",
            ttl_ms=1000,
            now_ms=101,
        )
    )

    counters = service.get_event_counters()
    assert acquire.allowed is True
    assert release.removed is True
    assert counters["acquire"] == 1
    assert counters["release"] == 1
