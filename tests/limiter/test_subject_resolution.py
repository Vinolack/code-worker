import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.api_models.api_key.resp_model import CodePlanApiKeyPermResp
from src.config import config
from src.dao.redis import RedisManager
from src.services.vlm import VlmService


def test_default_subject_is_apikey(monkeypatch):
    monkeypatch.setattr(config, "LIMITER_SUBJECT_USE_USER_ID", False, raising=False)
    monkeypatch.setattr(config, "LIMITER_ENABLE_DYNAMIC_LIMITS", False, raising=False)

    auth_perm = CodePlanApiKeyPermResp(
        allowed=True,
        cache_seconds=120,
        user_id="user-42",
        user_total_concurrency_limit=99,
        user_model_concurrency_limit=88,
    )

    policy = VlmService._build_limiter_resolution_policy(
        api_key_digest="digest-abc",
        auth_perm=auth_perm,
        model_default_total_limit=10,
        model_default_model_limit=4,
        local_fallback_total_limit=5,
        local_fallback_model_limit=2,
    )

    assert policy.subject_key == "digest-abc"
    assert policy.subject_source == "api_key_digest"
    assert policy.user_total_limit == 10
    assert policy.user_model_limit == 4
    assert policy.limit_source == "model_or_local_default"


def test_reserved_fields_not_effective_when_flag_off(monkeypatch):
    monkeypatch.setattr(config, "LIMITER_SUBJECT_USE_USER_ID", False, raising=False)
    monkeypatch.setattr(config, "LIMITER_ENABLE_DYNAMIC_LIMITS", False, raising=False)

    auth_perm = CodePlanApiKeyPermResp(
        allowed=True,
        cache_seconds=60,
        user_id="user-reserved",
        user_total_concurrency_limit=100,
        user_model_concurrency_limit=50,
    )

    policy = VlmService._build_limiter_resolution_policy(
        api_key_digest="digest-stable",
        auth_perm=auth_perm,
        model_default_total_limit=None,
        model_default_model_limit=None,
        local_fallback_total_limit=6,
        local_fallback_model_limit=3,
    )

    assert policy.subject_key == "digest-stable"
    assert policy.subject_source == "api_key_digest"
    assert policy.user_total_limit == 6
    assert policy.user_model_limit == 3
    assert policy.limit_source == "model_or_local_default"


def test_integer_user_id_is_normalized_to_string_when_flag_on(monkeypatch):
    monkeypatch.setattr(config, "LIMITER_SUBJECT_USE_USER_ID", True, raising=False)
    monkeypatch.setattr(config, "LIMITER_ENABLE_DYNAMIC_LIMITS", False, raising=False)

    auth_perm = CodePlanApiKeyPermResp(
        allowed=True,
        cache_seconds=60,
        user_id=123,
        user_total_concurrency_limit=100,
        user_model_concurrency_limit=50,
    )

    policy = VlmService._build_limiter_resolution_policy(
        api_key_digest="digest-int",
        auth_perm=auth_perm,
        model_default_total_limit=10,
        model_default_model_limit=5,
        local_fallback_total_limit=6,
        local_fallback_model_limit=3,
    )

    assert policy.subject_key == "123"
    assert policy.subject_source == "user_id"
    assert policy.user_total_limit == 10
    assert policy.user_model_limit == 5


class _FakeModelRedis:
    def __init__(self, governed_data: dict[str, str] | None = None, legacy_data: dict[str, str] | None = None):
        self.governed_data = governed_data or {}
        self.legacy_data = legacy_data or {}

    async def hget(self, key: str, field: str):
        if key == VlmService._legacy_model_hash_key():
            return self.governed_data.get(field)
        if key == "user-ai-model":
            return self.legacy_data.get(field)
        return None

    async def hgetall(self, key: str):
        if key == VlmService._legacy_model_hash_key():
            return dict(self.governed_data)
        if key == "user-ai-model":
            return dict(self.legacy_data)
        return {}


def test_model_namespace_prefers_governed_key(monkeypatch):
    monkeypatch.setattr(config, "LEGACY_REDIS_ENV", "test", raising=False)
    monkeypatch.setattr(config, "LEGACY_REDIS_SERVICE", "worker", raising=False)
    monkeypatch.setattr(
        RedisManager,
        "client",
        _FakeModelRedis(governed_data={"m-a": "governed"}, legacy_data={"m-a": "legacy"}),
    )

    value = asyncio.run(VlmService._hget_legacy_model("m-a"))
    assert value == "governed"


def test_model_namespace_falls_back_to_legacy_bare_key(monkeypatch):
    monkeypatch.setattr(config, "LEGACY_REDIS_ENV", "test", raising=False)
    monkeypatch.setattr(config, "LEGACY_REDIS_SERVICE", "worker", raising=False)
    monkeypatch.setattr(
        RedisManager,
        "client",
        _FakeModelRedis(governed_data={}, legacy_data={"m-a": "legacy"}),
    )

    value = asyncio.run(VlmService._hget_legacy_model("m-a"))
    assert value == "legacy"
