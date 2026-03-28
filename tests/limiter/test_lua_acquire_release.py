import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.base.constants.const import (
    KEY_NAMESPACE_SEPARATOR,
    LIMITER_KEY_NAMESPACE_MODULE,
    LIMITER_KEY_PREFIX,
    LIMITER_USER_MODEL_KEY_SCOPE,
    LIMITER_USER_TOTAL_KEY_SCOPE,
)
from src.config import config
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


def run(coro):
    return asyncio.run(coro)


def test_dual_dimension_limit_and_stale_cleanup():
    service = ConcurrencyLimiterService(redis_client=FakeRedis())

    first = run(service.acquire("user-a", "model-x", "req-1", ttl_ms=100, user_total_limit=2, user_model_limit=1, now_ms=1000))
    assert first.granted is True

    same_model_denied = run(
        service.acquire("user-a", "model-x", "req-2", ttl_ms=100, user_total_limit=2, user_model_limit=1, now_ms=1001)
    )
    assert same_model_denied.granted is False
    assert same_model_denied.reason == "user_model_limit"

    other_model_ok = run(
        service.acquire("user-a", "model-y", "req-3", ttl_ms=100, user_total_limit=2, user_model_limit=1, now_ms=1002)
    )
    assert other_model_ok.granted is True

    total_denied = run(
        service.acquire("user-a", "model-z", "req-4", ttl_ms=100, user_total_limit=2, user_model_limit=1, now_ms=1003)
    )
    assert total_denied.granted is False
    assert total_denied.reason == "user_total_limit"

    after_expire = run(
        service.acquire("user-a", "model-x", "req-5", ttl_ms=100, user_total_limit=2, user_model_limit=1, now_ms=1205)
    )
    assert after_expire.granted is True


def test_release_is_owner_scoped_and_idempotent():
    service = ConcurrencyLimiterService(redis_client=FakeRedis())

    run(service.acquire("user-b", "model-a", "req-owner", ttl_ms=1000, user_total_limit=2, user_model_limit=2, now_ms=10))
    run(service.acquire("user-b", "model-a", "req-other", ttl_ms=1000, user_total_limit=2, user_model_limit=2, now_ms=11))

    first_release = run(service.release("user-b", "model-a", "req-owner", ttl_ms=1000, now_ms=12))
    assert first_release.ok is True
    assert first_release.removed is True

    second_release = run(service.release("user-b", "model-a", "req-owner", ttl_ms=1000, now_ms=13))
    assert second_release.ok is True
    assert second_release.removed is False

    other_token_still_renewable = run(service.renew("user-b", "model-a", "req-other", ttl_ms=1000, now_ms=14))
    assert other_token_still_renewable.renewed is True


def test_renew_requires_owner_token():
    service = ConcurrencyLimiterService(redis_client=FakeRedis())

    run(service.acquire("user-c", "model-r", "req-keep", ttl_ms=100, user_total_limit=2, user_model_limit=2, now_ms=500))

    owner_renew = run(service.renew("user-c", "model-r", "req-keep", ttl_ms=100, now_ms=520))
    assert owner_renew.renewed is True

    wrong_owner = run(service.renew("user-c", "model-r", "req-wrong", ttl_ms=100, now_ms=521))
    assert wrong_owner.renewed is False
    assert wrong_owner.reason == "owner_mismatch"

    expired_owner = run(service.renew("user-c", "model-r", "req-keep", ttl_ms=100, now_ms=700))
    assert expired_owner.renewed is False
    assert expired_owner.reason == "owner_mismatch"


def test_key_model_uses_limiter_namespace_constants():
    service = ConcurrencyLimiterService(redis_client=FakeRedis())

    total_key = service._user_total_key("u-1")
    model_key = service._user_model_key("u-1", "m-1")

    expected_prefix = (
        f"{config.LIMITER_REDIS_PREFIX}{KEY_NAMESPACE_SEPARATOR}{config.LIMITER_REDIS_ENV}"
        f"{KEY_NAMESPACE_SEPARATOR}{config.LIMITER_REDIS_SERVICE}{KEY_NAMESPACE_SEPARATOR}{LIMITER_KEY_NAMESPACE_MODULE}"
        f"{KEY_NAMESPACE_SEPARATOR}{LIMITER_KEY_PREFIX}"
    )

    assert total_key.startswith(expected_prefix)
    assert model_key.startswith(expected_prefix)
    assert f"{KEY_NAMESPACE_SEPARATOR}{LIMITER_USER_TOTAL_KEY_SCOPE}{KEY_NAMESPACE_SEPARATOR}" in total_key
    assert f"{KEY_NAMESPACE_SEPARATOR}{LIMITER_USER_MODEL_KEY_SCOPE}{KEY_NAMESPACE_SEPARATOR}" in model_key
    assert "user-api-key" not in total_key
    assert "user-api-key" not in model_key
