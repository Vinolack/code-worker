import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.api_models.api_key.perm_msg_code_enum import CodePlanApiKeyPermMsgCodeEnum
from src.api_models.api_key.resp_model import CodePlanApiKeyPermResp
from src.base.constants.const import (
    CODE_PLAN_API_KEY_DIGEST_ONLY_CACHE_PREFIX,
    CODE_PLAN_API_KEY_PERM_CACHE_PREFIX,
    LEGACY_KEY_NAMESPACE_MODULE,
    build_governed_key,
)
from src.config import config
from src.dao.redis import RedisManager
from src.services.api_key import ApiKeyService


class FakeRedisClient:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None):
        self.store[key] = value
        return True


def run(coro):
    return asyncio.run(coro)


def _governed_cache_key(prefix: str, *parts: str) -> str:
    return build_governed_key(
        prefix=prefix,
        env=config.LEGACY_REDIS_ENV,
        service=config.LEGACY_REDIS_SERVICE,
        module=LEGACY_KEY_NAMESPACE_MODULE,
        parts=parts,
    )


@pytest.fixture(autouse=True)
def _cleanup_shared_client(monkeypatch):
    monkeypatch.setattr(config, "LEGACY_REDIS_ENV", "test", raising=False)
    monkeypatch.setattr(config, "LEGACY_REDIS_SERVICE", "worker", raising=False)
    yield
    run(ApiKeyService.close_shared_client())


def test_perm_reserved_fields_present_are_kept_in_cache_and_model(monkeypatch):
    fake_redis = FakeRedisClient()
    monkeypatch.setattr(RedisManager, "client", fake_redis)

    service = ApiKeyService()
    expected = CodePlanApiKeyPermResp(
        allowed=True,
        cache_seconds=120,
        msg_code=CodePlanApiKeyPermMsgCodeEnum.SUCCESS.code,
        user_id="user-42",
        user_total_concurrency_limit=9,
        user_model_concurrency_limit=3,
    )

    async def fake_remote(_: str, __: str) -> CodePlanApiKeyPermResp:
        return expected

    monkeypatch.setattr(service, "_remote_get_code_plan_api_key_perm", fake_remote)

    first = run(service.get_code_plan_api_key_perm("digest-a", "model-x"))
    assert first.user_id == "user-42"
    assert first.user_total_concurrency_limit == 9
    assert first.user_model_concurrency_limit == 3

    cache_key = _governed_cache_key(CODE_PLAN_API_KEY_PERM_CACHE_PREFIX, "digest-a", "model-x")
    cached_payload = json.loads(fake_redis.store[cache_key])
    assert cached_payload["user_id"] == "user-42"
    assert cached_payload["user_total_concurrency_limit"] == 9
    assert cached_payload["user_model_concurrency_limit"] == 3

    async def should_not_call_remote(_: str, __: str) -> CodePlanApiKeyPermResp:
        raise AssertionError("cache hit should not call remote")

    monkeypatch.setattr(service, "_remote_get_code_plan_api_key_perm", should_not_call_remote)
    second = run(service.get_code_plan_api_key_perm("digest-a", "model-x"))

    assert second.user_id == "user-42"
    assert second.user_total_concurrency_limit == 9
    assert second.user_model_concurrency_limit == 3


def test_perm_reserved_fields_absent_keeps_legacy_behavior(monkeypatch):
    fake_redis = FakeRedisClient()
    monkeypatch.setattr(RedisManager, "client", fake_redis)

    service = ApiKeyService()
    expected = CodePlanApiKeyPermResp(
        allowed=False,
        cache_seconds=60,
        msg_code=CodePlanApiKeyPermMsgCodeEnum.INVALID_API_KEY.code,
    )

    async def fake_remote(_: str, __: str) -> CodePlanApiKeyPermResp:
        return expected

    monkeypatch.setattr(service, "_remote_get_code_plan_api_key_perm", fake_remote)

    first = run(service.get_code_plan_api_key_perm("digest-b", "model-y"))
    assert first.allowed is False
    assert first.msg_code == CodePlanApiKeyPermMsgCodeEnum.INVALID_API_KEY.code
    assert first.user_id is None
    assert first.user_total_concurrency_limit is None
    assert first.user_model_concurrency_limit is None

    cache_key = _governed_cache_key(CODE_PLAN_API_KEY_PERM_CACHE_PREFIX, "digest-b", "model-y")
    cached_payload = json.loads(fake_redis.store[cache_key])
    assert cached_payload.get("user_id") is None
    assert cached_payload.get("user_total_concurrency_limit") is None
    assert cached_payload.get("user_model_concurrency_limit") is None

    second = run(service.get_code_plan_api_key_perm("digest-b", "model-y"))
    assert second.allowed is False
    assert second.msg_code == CodePlanApiKeyPermMsgCodeEnum.INVALID_API_KEY.code
    assert second.user_id is None
    assert second.user_total_concurrency_limit is None
    assert second.user_model_concurrency_limit is None


def test_digest_only_cache_keeps_reserved_fields_when_present(monkeypatch):
    fake_redis = FakeRedisClient()
    monkeypatch.setattr(RedisManager, "client", fake_redis)

    digest = "digest-present"
    cache_key = _governed_cache_key(CODE_PLAN_API_KEY_DIGEST_ONLY_CACHE_PREFIX, digest)
    fake_redis.store[cache_key] = json.dumps(
        {
            "allowed": True,
            "cache_seconds": 45,
            "user_id": "user-99",
            "user_total_concurrency_limit": 12,
            "user_model_concurrency_limit": 4,
        }
    )

    service = ApiKeyService()
    check_result, returned_digest, auth_info = run(service.check_api_key_v3(digest, is_digest=True))

    assert check_result is True
    assert returned_digest == digest
    assert auth_info is not None
    assert auth_info.user_id == "user-99"
    assert auth_info.user_total_concurrency_limit == 12
    assert auth_info.user_model_concurrency_limit == 4


def test_digest_only_cache_without_reserved_fields_remains_stable(monkeypatch):
    fake_redis = FakeRedisClient()
    monkeypatch.setattr(RedisManager, "client", fake_redis)

    digest = "digest-absent"
    cache_key = _governed_cache_key(CODE_PLAN_API_KEY_DIGEST_ONLY_CACHE_PREFIX, digest)
    fake_redis.store[cache_key] = json.dumps(
        {
            "allowed": True,
            "cache_seconds": 30,
            "perms_group": ["group-a"],
        }
    )

    service = ApiKeyService()
    check_result, returned_digest, auth_info = run(service.check_api_key_v3(digest, is_digest=True))

    assert check_result is True
    assert returned_digest == digest
    assert auth_info is not None
    assert auth_info.perms_group == ["group-a"]
    assert auth_info.user_id is None
    assert auth_info.user_total_concurrency_limit is None
    assert auth_info.user_model_concurrency_limit is None


def test_digest_only_cache_falls_back_to_legacy_bare_key(monkeypatch):
    fake_redis = FakeRedisClient()
    monkeypatch.setattr(RedisManager, "client", fake_redis)

    digest = "digest-fallback"
    legacy_cache_key = f"{CODE_PLAN_API_KEY_DIGEST_ONLY_CACHE_PREFIX}:{digest}"
    fake_redis.store[legacy_cache_key] = json.dumps(
        {
            "allowed": True,
            "cache_seconds": 33,
            "user_id": "legacy-user",
        }
    )

    service = ApiKeyService()
    check_result, returned_digest, auth_info = run(service.check_api_key_v3(digest, is_digest=True))

    assert check_result is True
    assert returned_digest == digest
    assert auth_info is not None
    assert auth_info.user_id == "legacy-user"
