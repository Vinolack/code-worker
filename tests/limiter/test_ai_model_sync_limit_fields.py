import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.base.constants.const import (
    LEGACY_KEY_NAMESPACE_MODULE,
    USER_AI_MODEL_SET_PREFIX,
    build_governed_key_prefix,
)
from src.config import config
from src.dao.redis import RedisManager
from src.jobs.ai_model import SyncAiModelJob


class FakeRedisClient:
    def __init__(self):
        self.hashes: dict[str, dict[str, str]] = {}

    async def delete(self, key: str):
        self.hashes.pop(key, None)
        return 1

    async def hset(self, key: str, mapping: dict[str, str]):
        self.hashes[key] = dict(mapping)
        return len(mapping)

    async def rename(self, source: str, destination: str):
        self.hashes[destination] = self.hashes.pop(source, {})
        return True


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _patch_supported_types(monkeypatch):
    monkeypatch.setattr(config, "SUPPORT_API_TYPES", ["openai"])
    monkeypatch.setattr(config, "LEGACY_REDIS_ENV", "test", raising=False)
    monkeypatch.setattr(config, "LEGACY_REDIS_SERVICE", "worker", raising=False)


def _governed_model_key() -> str:
    return build_governed_key_prefix(
        prefix=USER_AI_MODEL_SET_PREFIX,
        env=config.LEGACY_REDIS_ENV,
        service=config.LEGACY_REDIS_SERVICE,
        module=LEGACY_KEY_NAMESPACE_MODULE,
    )


def _load_entry(redis_client: FakeRedisClient, key: str) -> dict:
    payload = redis_client.hashes[_governed_model_key()][key]
    return json.loads(payload)


def test_sync_keeps_model_default_concurrency_fields_on_primary_alias_and_lowercase(monkeypatch):
    fake_redis = FakeRedisClient()
    monkeypatch.setattr(RedisManager, "client", fake_redis)

    job = SyncAiModelJob()
    models = [
        {
            "ai_model": "GPT-4O",
            "upstream_type": "openai",
            "model_aliases": "gpt-4o-proxy",
            "is_display_in_worker": 1,
            "model_default_user_total_concurrency_limit": 18,
            "model_default_user_model_concurrency_limit": 6,
        }
    ]

    run(job.load_ai_models_to_redis(models))

    primary = _load_entry(fake_redis, "GPT-4O")
    alias = _load_entry(fake_redis, "gpt-4o-proxy")
    lowercase = _load_entry(fake_redis, "gpt-4o")

    for item in (primary, alias, lowercase):
        assert item["model_default_user_total_concurrency_limit"] == 18
        assert item["model_default_user_model_concurrency_limit"] == 6


def test_sync_old_payload_without_default_concurrency_fields_remains_compatible(monkeypatch):
    fake_redis = FakeRedisClient()
    monkeypatch.setattr(RedisManager, "client", fake_redis)

    job = SyncAiModelJob()
    models = [
        {
            "ai_model": "QWEN-PLUS",
            "upstream_type": "openai",
            "model_aliases": "qwen-plus-proxy",
            "is_display_in_worker": 1,
        }
    ]

    run(job.load_ai_models_to_redis(models))

    primary = _load_entry(fake_redis, "QWEN-PLUS")
    alias = _load_entry(fake_redis, "qwen-plus-proxy")
    lowercase = _load_entry(fake_redis, "qwen-plus")

    for item in (primary, alias, lowercase):
        assert "model_default_user_total_concurrency_limit" not in item
        assert "model_default_user_model_concurrency_limit" not in item
