import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.responses import JSONResponse, StreamingResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.base.exceptions import HttpException
from src.base.utils.uuid_util import UUIDUtil
from src.config import config
from src.routers import vlm as vlm_router
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


class FakeUploadFile:
    def __init__(
        self,
        content: bytes = b"audio-bytes",
        filename: str = "audio.wav",
        content_type: str = "audio/wav",
        fail_on_read: bool = False,
    ):
        self._content = content
        self.filename = filename
        self.content_type = content_type
        self.fail_on_read = fail_on_read
        self.read_calls = 0

    async def read(self):
        self.read_calls += 1
        if self.fail_on_read:
            raise AssertionError("file.read should not be called")
        return self._content


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
        self.release_calls = []

    async def acquire_with_mode(self, **kwargs):
        self.calls.append(kwargs)
        return self.result

    async def release(self, **kwargs):
        self.release_calls.append(kwargs)
        return SimpleNamespace(ok=True, removed=True)


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


def _make_transcription_request(*, stream: bool, upload_file: FakeUploadFile | None = None) -> Any:
    return SimpleNamespace(
        file=upload_file or FakeUploadFile(),
        model="alias-model",
        language="zh",
        prompt="hello",
        response_format="json",
        temperature=0.0,
        timestamp_granularities=[],
        top_p=None,
        top_k=None,
        min_p=None,
        seed=None,
        frequency_penalty=0.0,
        repetition_penalty=None,
        presence_penalty=0.0,
        to_language=None,
        stream=stream,
        stream_include_usage=False,
        stream_continuous_usage_stats=False,
    )


def _dummy_request() -> Any:
    return DummyRequest()


@pytest.fixture(autouse=True)
def _reset_vlm_singletons():
    VlmService._limiter_service = None
    yield
    VlmService._limiter_service = None


def _patch_service_common(monkeypatch, limiter_result: ModeAwareAcquireResult):
    fake_proxy = FakeProxyClient()
    fake_limiter = FakeLimiterService(limiter_result)
    limiter_policy = _make_policy()

    async def fake_get_model_config(cls, model, api_key_digest, include_limiter_policy=False):
        if include_limiter_policy:
            return "up-key", "https://upstream.example", "real-model", "alias-model", limiter_policy
        return "up-key", "https://upstream.example", "real-model", "alias-model"

    async def fake_get_sse_proxy_client(cls):
        return fake_proxy

    monkeypatch.setattr(VlmService, "_get_model_config", classmethod(fake_get_model_config))
    monkeypatch.setattr(VlmService, "get_sse_proxy_client", classmethod(fake_get_sse_proxy_client))
    monkeypatch.setattr(VlmService, "_limiter_service", fake_limiter)

    monkeypatch.setattr(UUIDUtil, "generate_uuid_v4", staticmethod(lambda: "limiter-req-1"))
    monkeypatch.setattr(config, "LIMITER_LEASE_TTL_MS", 30000, raising=False)
    monkeypatch.setattr(config, "LIMITER_MODE", "enforce", raising=False)
    monkeypatch.setattr(config, "LIMITER_FAIL_POLICY", "fail-open", raising=False)

    return fake_proxy, fake_limiter


def test_audio_non_stream_blocked_returns_429_before_file_read_and_submit(monkeypatch):
    blocked_result = _make_result(allowed=False, blocked=True, reason="user_total_limit")
    fake_proxy, fake_limiter = _patch_service_common(monkeypatch, blocked_result)

    upload_file = FakeUploadFile(fail_on_read=True)
    request = _make_transcription_request(stream=False, upload_file=upload_file)

    with pytest.raises(HttpException) as exc_info:
        run(VlmService.audio_transcriptions_non_stream(request, "digest-1", _dummy_request()))

    assert exc_info.value.code == "429"
    assert "reason=user_total_limit" in exc_info.value.msg
    assert len(fake_proxy.submitted) == 0
    assert len(fake_limiter.calls) == 1
    assert upload_file.read_calls == 0


def test_audio_non_stream_allowed_submits_and_attaches_context(monkeypatch):
    allowed_result = _make_result(allowed=True, blocked=False, reason="ok")
    fake_proxy, fake_limiter = _patch_service_common(monkeypatch, allowed_result)

    upload_file = FakeUploadFile()
    request = _make_transcription_request(stream=False, upload_file=upload_file)
    result = run(VlmService.audio_transcriptions_non_stream(request, "digest-1", _dummy_request()))

    assert result == {"id": "proxy-req-1", "ok": True}
    assert upload_file.read_calls == 1
    assert len(fake_limiter.calls) == 1
    assert len(fake_proxy.submitted) == 1
    limiter_context = fake_proxy.submitted[0].user_data["limiter_context"]
    assert limiter_context["subject_key"] == "subject-1"
    assert limiter_context["model_name"] == "alias-model"
    assert limiter_context["request_id"] == "limiter-req-1"


def test_audio_stream_do_request_blocked_returns_429_before_submit(monkeypatch):
    blocked_result = _make_result(allowed=False, blocked=True, reason="user_total_limit")
    fake_proxy, fake_limiter = _patch_service_common(monkeypatch, blocked_result)

    request = _make_transcription_request(stream=True)

    with pytest.raises(HttpException) as exc_info:
        run(
            VlmService.audio_transcriptions_do_request(
                request=request,
                api_key="digest-1",
                filename="audio.wav",
                content_type="audio/wav",
                file_content=b"abc",
                raw_request=_dummy_request(),
            )
        )

    assert exc_info.value.code == "429"
    assert "reason=user_total_limit" in exc_info.value.msg
    assert len(fake_proxy.submitted) == 0
    assert len(fake_limiter.calls) == 1


def test_audio_stream_do_request_allowed_submits_and_attaches_context(monkeypatch):
    allowed_result = _make_result(allowed=True, blocked=False, reason="ok")
    fake_proxy, fake_limiter = _patch_service_common(monkeypatch, allowed_result)

    request = _make_transcription_request(stream=True)
    req_id, _ = run(
        VlmService.audio_transcriptions_do_request(
            request=request,
            api_key="digest-1",
            filename="audio.wav",
            content_type="audio/wav",
            file_content=b"abc",
            raw_request=_dummy_request(),
        )
    )

    assert req_id == "proxy-req-1"
    assert len(fake_limiter.calls) == 1
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
def test_audio_submit_limiter_observe_off_fail_open_continue(monkeypatch, mode_result):
    fake_proxy, _ = _patch_service_common(monkeypatch, mode_result)

    request = _make_transcription_request(stream=False)
    result = run(VlmService.audio_transcriptions_non_stream(request, "digest-1", _dummy_request()))

    assert result == {"id": "proxy-req-1", "ok": True}
    assert len(fake_proxy.submitted) == 1
    limiter_context = fake_proxy.submitted[0].user_data["limiter_context"]
    assert limiter_context["allowed"] is True
    assert limiter_context["reason"] == mode_result.reason
    assert limiter_context["mode"] == mode_result.mode


@pytest.mark.parametrize("stream", [False, True])
def test_audio_router_precheck_blocked_fails_before_file_read(monkeypatch, stream):
    upload_file = FakeUploadFile(fail_on_read=True)
    request = _make_transcription_request(stream=stream, upload_file=upload_file)
    call_state = {"non_stream": 0, "stream_do_request": 0}

    async def fake_precheck(cls, *, api_key_digest, requested_model):
        raise HttpException("precheck blocked", "429")

    async def fake_non_stream(cls, request, api_key, raw_request):
        call_state["non_stream"] += 1
        return {"ok": True}

    async def fake_do_request(cls, request, api_key, filename, content_type, file_content, raw_request):
        call_state["stream_do_request"] += 1
        return "x", 0.0

    monkeypatch.setattr(VlmService, "audio_transcriptions_precheck_before_read", classmethod(fake_precheck))
    monkeypatch.setattr(VlmService, "audio_transcriptions_non_stream", classmethod(fake_non_stream))
    monkeypatch.setattr(VlmService, "audio_transcriptions_do_request", classmethod(fake_do_request))

    with pytest.raises(HttpException) as exc_info:
        run(vlm_router.audio_transcriptions(_dummy_request(), request, "digest-1"))

    assert exc_info.value.code == "429"
    assert upload_file.read_calls == 0
    assert call_state["non_stream"] == 0
    assert call_state["stream_do_request"] == 0


def test_audio_router_precheck_allow_non_stream_delegates_without_route_read(monkeypatch):
    upload_file = FakeUploadFile(fail_on_read=True)
    request = _make_transcription_request(stream=False, upload_file=upload_file)
    call_state = {"non_stream": 0}

    async def fake_precheck(cls, *, api_key_digest, requested_model):
        return {"allowed": True}

    async def fake_non_stream(cls, request, api_key, raw_request):
        call_state["non_stream"] += 1
        return {"ok": True}

    monkeypatch.setattr(VlmService, "audio_transcriptions_precheck_before_read", classmethod(fake_precheck))
    monkeypatch.setattr(VlmService, "audio_transcriptions_non_stream", classmethod(fake_non_stream))

    response = run(vlm_router.audio_transcriptions(_dummy_request(), request, "digest-1"))

    assert isinstance(response, JSONResponse)
    assert upload_file.read_calls == 0
    assert call_state["non_stream"] == 1


def test_audio_router_precheck_allow_stream_reads_file_then_submits(monkeypatch):
    upload_file = FakeUploadFile(content=b"stream-audio")
    request = _make_transcription_request(stream=True, upload_file=upload_file)
    call_state = {"do_request": 0, "captured_bytes": b""}

    async def fake_precheck(cls, *, api_key_digest, requested_model):
        return {"allowed": True}

    async def fake_do_request(cls, request, api_key, filename, content_type, file_content, raw_request):
        call_state["do_request"] += 1
        call_state["captured_bytes"] = file_content
        return "stream-req-1", 0.0

    async def fake_get_response(cls, client_request_id, start_time, raw_request):
        if False:
            yield b""

    monkeypatch.setattr(VlmService, "audio_transcriptions_precheck_before_read", classmethod(fake_precheck))
    monkeypatch.setattr(VlmService, "audio_transcriptions_do_request", classmethod(fake_do_request))
    monkeypatch.setattr(VlmService, "audio_transcriptions_get_response", classmethod(fake_get_response))

    response = run(vlm_router.audio_transcriptions(_dummy_request(), request, "digest-1"))

    assert isinstance(response, StreamingResponse)
    assert upload_file.read_calls == 1
    assert call_state["do_request"] == 1
    assert call_state["captured_bytes"] == b"stream-audio"
