import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.api_models.api_key.perm_msg_code_enum import CodePlanApiKeyPermMsgCodeEnum
from src.base.constants.const import (
    CODE_PLAN_API_KEY_DIGEST_ONLY_CACHE_PREFIX,
    CODE_PLAN_API_KEY_PERM_CACHE_PREFIX,
    LEGACY_KEY_NAMESPACE_MODULE,
    build_governed_key,
)
from src.config import config
from src.dao.redis import RedisManager
from src.services.api_key import ApiKeyService
from src.services.vlm import VlmService


class FakeRedisClient:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None):
        self.store[key] = value
        return True


class FakeHttpResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeHttpClient:
    def __init__(self, payload_by_path: dict[str, dict]):
        self.payload_by_path = payload_by_path

    async def get(self, url: str, params=None, headers=None):
        del params, headers
        for path_fragment, payload in self.payload_by_path.items():
            if path_fragment in url:
                return FakeHttpResponse(payload)
        raise AssertionError(f"unexpected url: {url}")


class FailOnRemoteHttpClient:
    async def get(self, url: str, params=None, headers=None):
        del url, params, headers
        raise AssertionError("cache hit should not call remote")


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


def test_reserved_contract_cached(monkeypatch):
    fake_redis = FakeRedisClient()
    monkeypatch.setattr(RedisManager, "client", fake_redis)
    monkeypatch.setattr(config, "LIMITER_SUBJECT_USE_USER_ID", False, raising=False)
    monkeypatch.setattr(config, "LIMITER_ENABLE_DYNAMIC_LIMITS", False, raising=False)

    service = ApiKeyService()
    monkeypatch.setattr(
        service,
        "client",
        FakeHttpClient(
            {
                "code_plan_api_key_digest_only": {
                    "data": {
                        "allowed": True,
                        "cache_seconds": 45,
                        "user_id": "user-777",
                        "user_total_concurrency_limit": 13,
                        "user_model_concurrency_limit": 4,
                    }
                },
                "code_plan_api_key_perm": {
                    "data": {
                        "allowed": True,
                        "cache_seconds": 90,
                        "msg_code": CodePlanApiKeyPermMsgCodeEnum.SUCCESS.code,
                        "user_id": "user-777",
                        "user_total_concurrency_limit": 13,
                        "user_model_concurrency_limit": 4,
                    }
                },
            }
        ),
        raising=False,
    )

    digest = "digest-contract"
    digest_ok, digest_returned, digest_auth = run(service.check_api_key_v3(digest, is_digest=True))
    assert digest_ok is True
    assert digest_returned == digest
    assert digest_auth is not None
    assert digest_auth.user_id == "user-777"
    assert digest_auth.user_total_concurrency_limit == 13
    assert digest_auth.user_model_concurrency_limit == 4

    digest_cache_key = _governed_cache_key(CODE_PLAN_API_KEY_DIGEST_ONLY_CACHE_PREFIX, digest)
    digest_cached_payload = json.loads(fake_redis.store[digest_cache_key])
    assert digest_cached_payload["user_id"] == "user-777"
    assert digest_cached_payload["user_total_concurrency_limit"] == 13
    assert digest_cached_payload["user_model_concurrency_limit"] == 4

    monkeypatch.setattr(service, "client", FailOnRemoteHttpClient(), raising=False)
    digest_ok_again, digest_returned_again, digest_auth_again = run(service.check_api_key_v3(digest, is_digest=True))
    assert digest_ok_again is True
    assert digest_returned_again == digest
    assert digest_auth_again is not None
    assert digest_auth_again.user_id == "user-777"

    monkeypatch.setattr(
        service,
        "client",
        FakeHttpClient(
            {
                "code_plan_api_key_perm": {
                    "data": {
                        "allowed": True,
                        "cache_seconds": 90,
                        "msg_code": CodePlanApiKeyPermMsgCodeEnum.SUCCESS.code,
                        "user_id": "user-777",
                        "user_total_concurrency_limit": 13,
                        "user_model_concurrency_limit": 4,
                    }
                }
            }
        ),
        raising=False,
    )
    perm_auth = run(service.get_code_plan_api_key_perm(digest, "model-a"))
    assert perm_auth.user_id == "user-777"
    assert perm_auth.user_total_concurrency_limit == 13
    assert perm_auth.user_model_concurrency_limit == 4

    perm_cache_key = _governed_cache_key(CODE_PLAN_API_KEY_PERM_CACHE_PREFIX, digest, "model-a")
    perm_cached_payload = json.loads(fake_redis.store[perm_cache_key])
    assert perm_cached_payload["user_id"] == "user-777"
    assert perm_cached_payload["user_total_concurrency_limit"] == 13
    assert perm_cached_payload["user_model_concurrency_limit"] == 4

    monkeypatch.setattr(service, "client", FailOnRemoteHttpClient(), raising=False)
    perm_auth_again = run(service.get_code_plan_api_key_perm(digest, "model-a"))
    assert perm_auth_again.user_id == "user-777"

    policy = VlmService._build_limiter_resolution_policy(
        api_key_digest=digest,
        auth_perm=perm_auth_again,
        model_default_total_limit=6,
        model_default_model_limit=2,
        local_fallback_total_limit=99,
        local_fallback_model_limit=99,
    )
    assert policy.subject_source == "api_key_digest"
    assert policy.subject_key == digest
    assert policy.user_total_limit == 6
    assert policy.user_model_limit == 2
    assert policy.limit_source == "model_or_local_default"


def test_integer_user_id_contract_supports_user_subject(monkeypatch):
    fake_redis = FakeRedisClient()
    monkeypatch.setattr(RedisManager, "client", fake_redis)
    monkeypatch.setattr(config, "LIMITER_SUBJECT_USE_USER_ID", True, raising=False)
    monkeypatch.setattr(config, "LIMITER_ENABLE_DYNAMIC_LIMITS", False, raising=False)

    service = ApiKeyService()
    monkeypatch.setattr(
        service,
        "client",
        FakeHttpClient(
            {
                "code_plan_api_key_digest_only": {
                    "data": {
                        "allowed": True,
                        "cache_seconds": 45,
                        "user_id": 777,
                        "user_total_concurrency_limit": 13,
                        "user_model_concurrency_limit": 4,
                    }
                },
                "code_plan_api_key_perm": {
                    "data": {
                        "allowed": True,
                        "cache_seconds": 90,
                        "msg_code": CodePlanApiKeyPermMsgCodeEnum.SUCCESS.code,
                        "user_id": 777,
                        "user_total_concurrency_limit": 13,
                        "user_model_concurrency_limit": 4,
                    }
                },
            }
        ),
        raising=False,
    )

    digest = "digest-int-contract"
    digest_ok, digest_returned, digest_auth = run(service.check_api_key_v3(digest, is_digest=True))
    assert digest_ok is True
    assert digest_returned == digest
    assert digest_auth is not None
    assert digest_auth.user_id == 777

    perm_auth = run(service.get_code_plan_api_key_perm(digest, "model-int"))
    assert perm_auth.user_id == 777

    policy = VlmService._build_limiter_resolution_policy(
        api_key_digest=digest,
        auth_perm=perm_auth,
        model_default_total_limit=6,
        model_default_model_limit=2,
        local_fallback_total_limit=99,
        local_fallback_model_limit=99,
    )
    assert policy.subject_source == "user_id"
    assert policy.subject_key == "777"
    assert policy.user_total_limit == 6
    assert policy.user_model_limit == 2


def test_old_contract_fallback(monkeypatch):
    fake_redis = FakeRedisClient()
    monkeypatch.setattr(RedisManager, "client", fake_redis)
    monkeypatch.setattr(config, "LIMITER_SUBJECT_USE_USER_ID", False, raising=False)
    monkeypatch.setattr(config, "LIMITER_ENABLE_DYNAMIC_LIMITS", False, raising=False)

    service = ApiKeyService()
    monkeypatch.setattr(
        service,
        "client",
        FakeHttpClient(
            {
                "code_plan_api_key_digest_only": {
                    "data": {
                        "allowed": True,
                        "cache_seconds": 30,
                        "perms_group": ["legacy-group"],
                    }
                },
                "code_plan_api_key_perm": {
                    "data": {
                        "allowed": True,
                        "cache_seconds": 60,
                        "msg_code": CodePlanApiKeyPermMsgCodeEnum.SUCCESS.code,
                    }
                },
            }
        ),
        raising=False,
    )

    digest = "digest-legacy"
    digest_ok, _, digest_auth = run(service.check_api_key_v3(digest, is_digest=True))
    assert digest_ok is True
    assert digest_auth is not None
    assert digest_auth.perms_group == ["legacy-group"]
    assert digest_auth.user_id is None
    assert digest_auth.user_total_concurrency_limit is None
    assert digest_auth.user_model_concurrency_limit is None

    monkeypatch.setattr(service, "client", FailOnRemoteHttpClient(), raising=False)
    digest_ok_again, _, digest_auth_again = run(service.check_api_key_v3(digest, is_digest=True))
    assert digest_ok_again is True
    assert digest_auth_again is not None
    assert digest_auth_again.user_id is None

    monkeypatch.setattr(
        service,
        "client",
        FakeHttpClient(
            {
                "code_plan_api_key_perm": {
                    "data": {
                        "allowed": True,
                        "cache_seconds": 60,
                        "msg_code": CodePlanApiKeyPermMsgCodeEnum.SUCCESS.code,
                    }
                }
            }
        ),
        raising=False,
    )
    perm_auth = run(service.get_code_plan_api_key_perm(digest, "model-b"))
    assert perm_auth.allowed is True
    assert perm_auth.msg_code == CodePlanApiKeyPermMsgCodeEnum.SUCCESS.code
    assert perm_auth.user_id is None
    assert perm_auth.user_total_concurrency_limit is None
    assert perm_auth.user_model_concurrency_limit is None

    monkeypatch.setattr(service, "client", FailOnRemoteHttpClient(), raising=False)
    perm_auth_again = run(service.get_code_plan_api_key_perm(digest, "model-b"))
    assert perm_auth_again.user_id is None
    assert perm_auth_again.user_total_concurrency_limit is None
    assert perm_auth_again.user_model_concurrency_limit is None

    policy = VlmService._build_limiter_resolution_policy(
        api_key_digest=digest,
        auth_perm=perm_auth_again,
        model_default_total_limit=None,
        model_default_model_limit=None,
        local_fallback_total_limit=5,
        local_fallback_model_limit=2,
    )
    assert policy.subject_source == "api_key_digest"
    assert policy.user_total_limit == 5
    assert policy.user_model_limit == 2
    assert policy.limit_source == "model_or_local_default"
