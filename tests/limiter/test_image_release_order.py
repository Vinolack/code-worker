import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.base.exceptions import HttpException
from src.base.utils.uuid_util import UUIDUtil
from src.config import config
from src.services.concurrency_limiter import AcquireResult, ModeAwareAcquireResult
from src.services.vlm import LimiterResolutionPolicy, VlmService


def run(coro):
    return asyncio.run(coro)


class DummyRequest:
    def __init__(self):
        self.headers = {"user-agent": "pytest-agent"}
        self.client = SimpleNamespace(host="127.0.0.1")

    async def is_disconnected(self):
        return False


class FakeSemaphore:
    def __init__(self, events: list[str]):
        self.events = events
        self.acquire_count = 0
        self.release_count = 0

    async def acquire(self):
        self.acquire_count += 1
        self.events.append("local_acquire")

    def release(self):
        if self.release_count >= self.acquire_count:
            raise AssertionError("semaphore over-release")
        self.release_count += 1
        self.events.append("local_release")


class FakeLimiterService:
    def __init__(self, result: ModeAwareAcquireResult, events: list[str]):
        self.result = result
        self.events = events
        self.acquire_calls = []
        self.release_calls = []

    async def acquire_with_mode(self, **kwargs):
        self.acquire_calls.append(kwargs)
        self.events.append("limiter_acquire")
        return self.result

    async def release(self, **kwargs):
        self.release_calls.append(kwargs)
        self.events.append("limiter_release")
        return SimpleNamespace(ok=True, removed=True)


class FakeProxyClient:
    def __init__(self, events: list[str], wait_error: Exception | None = None):
        self.events = events
        self.wait_error = wait_error
        self.submitted = []

    def submit(self, req_wrapper):
        self.submitted.append(req_wrapper)
        self.events.append("submit")
        return "proxy-req-1"

    async def wait_for_upstream_status(self, req_id):
        if self.wait_error:
            raise self.wait_error
        return None

    async def json(self, req_id):
        return {"id": req_id, "ok": True}

    async def stream_generator_with_heartbeat(self, req_id):
        yield b"chunk-1"


def _make_policy() -> LimiterResolutionPolicy:
    return LimiterResolutionPolicy(
        subject_key="subject-1",
        subject_source="api_key_digest",
        user_total_limit=3,
        user_model_limit=2,
        limit_source="model_or_local_default",
    )


def _make_enforce_allowed_result() -> ModeAwareAcquireResult:
    return ModeAwareAcquireResult(
        allowed=True,
        blocked=False,
        would_block=False,
        bypass=False,
        error_policy_action="none",
        reason="ok",
        mode="enforce",
        fail_policy="fail-open",
        acquire_result=AcquireResult(
            granted=True,
            reason="ok",
            user_total_count=1,
            user_model_count=1,
        ),
    )


@pytest.fixture(autouse=True)
def _reset_vlm_singletons():
    VlmService._limiter_service = None
    VlmService._image_generation_inflight.clear()
    VlmService._image_generation_limiter_contexts.clear()
    yield
    VlmService._limiter_service = None
    VlmService._image_generation_inflight.clear()
    VlmService._image_generation_limiter_contexts.clear()


def _patch_common(monkeypatch, *, proxy_client: FakeProxyClient, semaphore: FakeSemaphore, limiter: FakeLimiterService):
    limiter_policy = _make_policy()

    async def fake_get_model_config(cls, model, api_key_digest, include_limiter_policy=False):
        assert include_limiter_policy is True
        return "up-key", "https://upstream.example", "real-model", "alias-model", limiter_policy

    async def fake_get_sse_proxy_client(cls):
        return proxy_client

    monkeypatch.setattr(VlmService, "_get_model_config", classmethod(fake_get_model_config))
    monkeypatch.setattr(VlmService, "get_sse_proxy_client", classmethod(fake_get_sse_proxy_client))
    monkeypatch.setattr(VlmService, "image_generation_semaphore", semaphore)
    monkeypatch.setattr(VlmService, "_limiter_service", limiter)

    monkeypatch.setattr(UUIDUtil, "generate_uuid_v4", staticmethod(lambda: "limiter-req-1"))
    monkeypatch.setattr(config, "LIMITER_LEASE_TTL_MS", 30000, raising=False)
    monkeypatch.setattr(config, "LIMITER_MODE", "enforce", raising=False)
    monkeypatch.setattr(config, "LIMITER_FAIL_POLICY", "fail-open", raising=False)


async def _collect_stream(gen):
    chunks = []
    async for chunk in gen:
        chunks.append(chunk)
    return chunks


def test_image_stream_submit_failure_releases_distributed_then_local_once(monkeypatch):
    events: list[str] = []
    semaphore = FakeSemaphore(events)
    limiter = FakeLimiterService(_make_enforce_allowed_result(), events)
    proxy_client = FakeProxyClient(events, wait_error=RuntimeError("upstream down"))
    _patch_common(monkeypatch, proxy_client=proxy_client, semaphore=semaphore, limiter=limiter)

    with pytest.raises(HttpException) as exc_info:
        run(VlmService.image_generations_do_request({"model": "alias-model"}, "digest-1", cast(Any, DummyRequest())))

    assert exc_info.value.code == "500"
    assert len(limiter.acquire_calls) == 1
    assert len(limiter.release_calls) == 1
    assert limiter.release_calls[0]["request_id"] == "limiter-req-1"
    assert semaphore.acquire_count == 1
    assert semaphore.release_count == 1
    assert events.index("limiter_acquire") < events.index("submit")
    assert events.index("limiter_release") < events.index("local_release")


def test_image_stream_get_response_releases_once_after_handoff(monkeypatch):
    events: list[str] = []
    semaphore = FakeSemaphore(events)
    limiter = FakeLimiterService(_make_enforce_allowed_result(), events)
    proxy_client = FakeProxyClient(events)
    _patch_common(monkeypatch, proxy_client=proxy_client, semaphore=semaphore, limiter=limiter)

    req_id, _ = run(VlmService.image_generations_do_request({"model": "alias-model"}, "digest-1", cast(Any, DummyRequest())))

    assert req_id == "proxy-req-1"
    assert semaphore.acquire_count == 1
    assert semaphore.release_count == 0
    assert len(limiter.release_calls) == 0

    chunks = run(_collect_stream(VlmService.image_generations_get_response(req_id, 0.0, cast(Any, DummyRequest()))))

    assert chunks == [b"chunk-1"]
    assert len(limiter.release_calls) == 1
    assert semaphore.release_count == 1
    assert events.index("limiter_release") < events.index("local_release")


def test_image_non_stream_failure_releases_distributed_then_local_once(monkeypatch):
    events: list[str] = []
    semaphore = FakeSemaphore(events)
    limiter = FakeLimiterService(_make_enforce_allowed_result(), events)
    proxy_client = FakeProxyClient(events, wait_error=RuntimeError("upstream down"))
    _patch_common(monkeypatch, proxy_client=proxy_client, semaphore=semaphore, limiter=limiter)

    with pytest.raises(HttpException) as exc_info:
        run(VlmService.non_stream_image_generation({"model": "alias-model"}, "digest-1", cast(Any, DummyRequest())))

    assert exc_info.value.code == "500"
    assert len(limiter.acquire_calls) == 1
    assert len(limiter.release_calls) == 1
    assert limiter.release_calls[0]["request_id"] == "limiter-req-1"
    assert semaphore.acquire_count == 1
    assert semaphore.release_count == 1
    assert events.index("limiter_acquire") < events.index("submit")
    assert events.index("limiter_release") < events.index("local_release")
