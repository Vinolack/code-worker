import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.services.concurrency_limiter import AcquireResult, ConcurrencyLimiterService


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


def _pick_granted_request(acquires: list[tuple[str, str, AcquireResult]]) -> tuple[str, str]:
    granted = [(request_id, model_name) for request_id, model_name, result in acquires if result.granted]
    assert len(granted) == 1
    return granted[0]


def test_dual_dimension_concurrency_race_and_recovery():
    service = ConcurrencyLimiterService(redis_client=FakeRedis())

    async def _race_same_model() -> list[tuple[str, str, AcquireResult]]:
        attempts = [("req-a", "model-x"), ("req-b", "model-x")]
        results = await asyncio.gather(
            *[
                service.acquire(
                    "user-1",
                    model_name,
                    request_id,
                    ttl_ms=300,
                    user_total_limit=2,
                    user_model_limit=1,
                    now_ms=1000,
                )
                for request_id, model_name in attempts
            ]
        )
        return [
            (request_id, model_name, result)
            for (request_id, model_name), result in zip(attempts, results)
        ]

    same_model_race = run(_race_same_model())
    assert sum(1 for _, _, result in same_model_race if result.granted) == 1
    assert [result.reason for _, _, result in same_model_race if not result.granted] == ["user_model_limit"]

    other_model = run(
        service.acquire(
            "user-1",
            "model-y",
            "req-c",
            ttl_ms=300,
            user_total_limit=2,
            user_model_limit=1,
            now_ms=1001,
        )
    )
    assert other_model.granted is True
    assert other_model.user_total_count == 2

    over_total = run(
        service.acquire(
            "user-1",
            "model-z",
            "req-d",
            ttl_ms=300,
            user_total_limit=2,
            user_model_limit=1,
            now_ms=1002,
        )
    )
    assert over_total.granted is False
    assert over_total.reason == "user_total_limit"

    release_request_id, release_model_name = _pick_granted_request(same_model_race)
    released = run(
        service.release(
            "user-1",
            release_model_name,
            release_request_id,
            ttl_ms=300,
            now_ms=1003,
        )
    )
    assert released.ok is True
    assert released.removed is True

    recovered = run(
        service.acquire(
            "user-1",
            "model-z",
            "req-e",
            ttl_ms=300,
            user_total_limit=2,
            user_model_limit=1,
            now_ms=1004,
        )
    )
    assert recovered.granted is True
    assert recovered.user_total_count == 2


def test_concurrent_users_do_not_share_quota_slots():
    service = ConcurrencyLimiterService(redis_client=FakeRedis())

    async def _acquire_two_users():
        return await asyncio.gather(
            service.acquire(
                "user-a",
                "model-shared",
                "req-a-1",
                ttl_ms=300,
                user_total_limit=1,
                user_model_limit=1,
                now_ms=2000,
            ),
            service.acquire(
                "user-b",
                "model-shared",
                "req-b-1",
                ttl_ms=300,
                user_total_limit=1,
                user_model_limit=1,
                now_ms=2000,
            ),
        )

    user_a_first, user_b_first = run(_acquire_two_users())
    assert user_a_first.granted is True
    assert user_b_first.granted is True

    user_a_second = run(
        service.acquire(
            "user-a",
            "model-other",
            "req-a-2",
            ttl_ms=300,
            user_total_limit=1,
            user_model_limit=1,
            now_ms=2001,
        )
    )
    user_b_second = run(
        service.acquire(
            "user-b",
            "model-other",
            "req-b-2",
            ttl_ms=300,
            user_total_limit=1,
            user_model_limit=1,
            now_ms=2001,
        )
    )

    assert user_a_second.granted is False
    assert user_a_second.reason == "user_total_limit"
    assert user_b_second.granted is False
    assert user_b_second.reason == "user_total_limit"
