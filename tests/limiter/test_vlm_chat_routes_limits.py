import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

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


class FakeProxyClient:
    def __init__(self):
        self.submitted = []

    def submit(self, req_wrapper):
        self.submitted.append(req_wrapper)
        return "proxy-req-1"

    async def wait_for_upstream_status(self, req_id):
        return None

    async def json(self, req_id):
        return {"id": req_id, "ok": True}


class FakeLimiterService:
    def __init__(self, result: ModeAwareAcquireResult):
        self.result = result
        self.calls = []

    async def acquire_with_mode(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def _make_policy() -> LimiterResolutionPolicy:
    return LimiterResolutionPolicy(
        subject_key="subject-1",
        subject_source="api_key_digest",
        user_total_limit=3,
        user_model_limit=2,
        limit_source="model_or_local_default",
    )


def _make_result(
    *,
    allowed: bool,
    blocked: bool,
    reason: str,
    mode: str = "enforce",
    fail_policy: str = "fail-open",
    would_block: bool = False,
    bypass: bool = False,
    error_policy_action: str = "none",
    include_counts: bool = True,
) -> ModeAwareAcquireResult:
    acquire_result = None
    if include_counts:
        acquire_result = AcquireResult(
            granted=allowed,
            reason=reason,
            user_total_count=1,
            user_model_count=1,
        )

    return ModeAwareAcquireResult(
        allowed=allowed,
        blocked=blocked,
        would_block=would_block,
        bypass=bypass,
        error_policy_action=error_policy_action,
        reason=reason,
        mode=mode,
        fail_policy=fail_policy,
        acquire_result=acquire_result,
    )


@pytest.fixture(autouse=True)
def _reset_vlm_singletons():
    VlmService._limiter_service = None
    yield
    VlmService._limiter_service = None


def _patch_common(monkeypatch, limiter_result: ModeAwareAcquireResult):
    fake_proxy = FakeProxyClient()
    fake_limiter = FakeLimiterService(limiter_result)
    limiter_policy = _make_policy()

    async def fake_get_model_config(cls, model, api_key_digest, include_limiter_policy=False):
        if include_limiter_policy:
            return "up-key", "https://upstream.example", "real-model", "alias-model", limiter_policy
        return "up-key", "https://upstream.example", "real-model", "alias-model"

    async def fake_build_request_url_and_headers(cls, model_name, path, base_url, api_key, original_api_type="openai"):
        return f"https://upstream.example{path}", api_key, {}

    async def fake_get_sse_proxy_client(cls):
        return fake_proxy

    monkeypatch.setattr(VlmService, "_get_model_config", classmethod(fake_get_model_config))
    monkeypatch.setattr(VlmService, "_build_request_url_and_headers", classmethod(fake_build_request_url_and_headers))
    monkeypatch.setattr(VlmService, "get_sse_proxy_client", classmethod(fake_get_sse_proxy_client))
    monkeypatch.setattr(VlmService, "_limiter_service", fake_limiter)

    monkeypatch.setattr(UUIDUtil, "generate_uuid_v4", staticmethod(lambda: "limiter-req-1"))
    monkeypatch.setattr(config, "LIMITER_LEASE_TTL_MS", 30000, raising=False)
    monkeypatch.setattr(config, "LIMITER_MODE", "enforce", raising=False)
    monkeypatch.setattr(config, "LIMITER_FAIL_POLICY", "fail-open", raising=False)

    return fake_proxy, fake_limiter


def _build_request_payload(method_name: str):
    if "responses" in method_name:
        return {"model": "alias-model", "input": "hello"}
    if "anthropic" in method_name:
        return {"model": "alias-model", "messages": [{"role": "user", "content": "hello"}]}
    return {"model": "alias-model", "messages": [{"role": "user", "content": "hello"}]}


def _build_path(method_name: str) -> str:
    if "responses" in method_name:
        return "/v1/responses"
    if "anthropic" in method_name:
        return "/v1/messages"
    return "/v1/chat/completions"


def _invoke_method(method_name: str):
    method = getattr(VlmService, method_name)
    return run(method(_build_request_payload(method_name), "digest-1", _build_path(method_name), DummyRequest()))


@pytest.mark.parametrize(
    "method_name",
    [
        "stream_chat_do_request",
        "non_stream_responses",
        "stream_responses_do_request",
        "non_stream_anthropic_messages",
        "anthropic_messages_stream_do_request",
    ],
)
def test_limiter_blocked_returns_429_before_submit(monkeypatch, method_name):
    blocked_result = _make_result(allowed=False, blocked=True, reason="user_total_limit")
    fake_proxy, fake_limiter = _patch_common(monkeypatch, blocked_result)

    with pytest.raises(HttpException) as exc_info:
        _invoke_method(method_name)

    assert exc_info.value.code == "429"
    assert "reason=user_total_limit" in exc_info.value.msg
    assert "subject=subject-1" in exc_info.value.msg
    assert "model=alias-model" in exc_info.value.msg
    assert len(fake_proxy.submitted) == 0
    assert len(fake_limiter.calls) == 1


@pytest.mark.parametrize(
    "method_name",
    [
        "stream_chat_do_request",
        "non_stream_responses",
        "stream_responses_do_request",
        "non_stream_anthropic_messages",
        "anthropic_messages_stream_do_request",
    ],
)
def test_limiter_allowed_path_submits_and_attaches_context(monkeypatch, method_name):
    allowed_result = _make_result(allowed=True, blocked=False, reason="ok")
    fake_proxy, fake_limiter = _patch_common(monkeypatch, allowed_result)

    result = _invoke_method(method_name)

    stream_methods = {
        "stream_chat_do_request",
        "stream_responses_do_request",
        "anthropic_messages_stream_do_request",
    }
    if method_name in stream_methods:
        assert result[0] == "proxy-req-1"
    else:
        assert result == {"id": "proxy-req-1", "ok": True}

    assert len(fake_limiter.calls) == 1
    assert fake_limiter.calls[0]["user_id"] == "subject-1"
    assert fake_limiter.calls[0]["model_name"] == "alias-model"
    assert fake_limiter.calls[0]["request_id"] == "limiter-req-1"

    assert len(fake_proxy.submitted) == 1
    limiter_context = fake_proxy.submitted[0].user_data["limiter_context"]
    assert limiter_context["subject_key"] == "subject-1"
    assert limiter_context["model_name"] == "alias-model"
    assert limiter_context["request_id"] == "limiter-req-1"


@pytest.mark.parametrize(
    "mode_result",
    [
        _make_result(allowed=True, blocked=False, reason="user_total_limit", mode="observe", would_block=True),
        _make_result(allowed=True, blocked=False, reason="mode_off_bypass", mode="off", bypass=True, include_counts=False),
        _make_result(
            allowed=True,
            blocked=False,
            reason="redis_error",
            mode="enforce",
            fail_policy="fail-open",
            error_policy_action="fail_open_allow",
            include_counts=False,
        ),
    ],
)
def test_observe_off_and_fail_open_paths_continue(monkeypatch, mode_result):
    fake_proxy, _ = _patch_common(monkeypatch, mode_result)

    result = _invoke_method("non_stream_responses")

    assert result == {"id": "proxy-req-1", "ok": True}
    assert len(fake_proxy.submitted) == 1
    limiter_context = fake_proxy.submitted[0].user_data["limiter_context"]
    assert limiter_context["allowed"] is True
    assert limiter_context["reason"] == mode_result.reason
    assert limiter_context["mode"] == mode_result.mode
