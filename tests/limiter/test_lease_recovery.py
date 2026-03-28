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


def run(coro):
    return asyncio.run(coro)


def test_expired_leak_allows_new_owner_after_ttl_cleanup():
    service = ConcurrencyLimiterService(redis_client=FakeRedis())

    leaked = run(
        service.acquire(
            "user-lease",
            "model-x",
            "req-leaked",
            ttl_ms=100,
            user_total_limit=1,
            user_model_limit=1,
            now_ms=1000,
        )
    )
    assert leaked.granted is True

    before_ttl = run(
        service.acquire(
            "user-lease",
            "model-x",
            "req-before-expire",
            ttl_ms=100,
            user_total_limit=1,
            user_model_limit=1,
            now_ms=1050,
        )
    )
    assert before_ttl.granted is False
    assert before_ttl.reason == "user_total_limit"

    after_ttl = run(
        service.acquire(
            "user-lease",
            "model-x",
            "req-after-expire",
            ttl_ms=100,
            user_total_limit=1,
            user_model_limit=1,
            now_ms=1101,
        )
    )
    assert after_ttl.granted is True
    assert after_ttl.user_total_count == 1
    assert after_ttl.user_model_count == 1


def test_late_release_of_expired_lease_is_idempotent_and_safe():
    service = ConcurrencyLimiterService(redis_client=FakeRedis())

    run(
        service.acquire(
            "user-lease",
            "model-x",
            "req-old",
            ttl_ms=80,
            user_total_limit=1,
            user_model_limit=1,
            now_ms=2000,
        )
    )

    current_owner = run(
        service.acquire(
            "user-lease",
            "model-x",
            "req-new",
            ttl_ms=80,
            user_total_limit=1,
            user_model_limit=1,
            now_ms=2081,
        )
    )
    assert current_owner.granted is True

    old_release = run(
        service.release(
            "user-lease",
            "model-x",
            "req-old",
            ttl_ms=80,
            now_ms=2082,
        )
    )
    assert old_release.ok is True
    assert old_release.removed is False

    old_release_again = run(
        service.release(
            "user-lease",
            "model-x",
            "req-old",
            ttl_ms=80,
            now_ms=2083,
        )
    )
    assert old_release_again.ok is True
    assert old_release_again.removed is False

    current_owner_renew = run(
        service.renew(
            "user-lease",
            "model-x",
            "req-new",
            ttl_ms=80,
            now_ms=2084,
        )
    )
    assert current_owner_renew.renewed is True

    current_release = run(
        service.release(
            "user-lease",
            "model-x",
            "req-new",
            ttl_ms=80,
            now_ms=2085,
        )
    )
    assert current_release.ok is True
    assert current_release.removed is True

    current_release_again = run(
        service.release(
            "user-lease",
            "model-x",
            "req-new",
            ttl_ms=80,
            now_ms=2086,
        )
    )
    assert current_release_again.ok is True
    assert current_release_again.removed is False


def test_renew_extends_lease_and_delays_recovery():
    service = ConcurrencyLimiterService(redis_client=FakeRedis())

    first = run(
        service.acquire(
            "user-lease",
            "model-y",
            "req-active",
            ttl_ms=100,
            user_total_limit=1,
            user_model_limit=1,
            now_ms=3000,
        )
    )
    assert first.granted is True

    renewed = run(
        service.renew(
            "user-lease",
            "model-y",
            "req-active",
            ttl_ms=100,
            now_ms=3090,
        )
    )
    assert renewed.renewed is True

    still_blocked = run(
        service.acquire(
            "user-lease",
            "model-y",
            "req-too-early",
            ttl_ms=100,
            user_total_limit=1,
            user_model_limit=1,
            now_ms=3150,
        )
    )
    assert still_blocked.granted is False
    assert still_blocked.reason == "user_total_limit"

    recovered = run(
        service.acquire(
            "user-lease",
            "model-y",
            "req-after-extended-expire",
            ttl_ms=100,
            user_total_limit=1,
            user_model_limit=1,
            now_ms=3191,
        )
    )
    assert recovered.granted is True
