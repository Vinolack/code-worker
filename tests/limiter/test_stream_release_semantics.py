import asyncio
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.services.vlm import VlmService
from src.utils.http_client import AsyncHttpClient, RequestWrapper


class FakeContent:
    def __init__(self, chunks: list[bytes], delay: float = 0.0):
        self._chunks = chunks
        self._delay = delay

    async def iter_any(self):
        for chunk in self._chunks:
            if self._delay > 0:
                await asyncio.sleep(self._delay)
            yield chunk


class FakeResponse:
    def __init__(self, status: int, chunks: list[bytes], delay: float = 0.0):
        self.status = status
        self.content = FakeContent(chunks, delay)
        self._chunks = chunks

    async def read(self):
        return b"".join(self._chunks)


class FakeRequestContext:
    def __init__(self, response: FakeResponse):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    def __init__(self, response: FakeResponse):
        self._response = response
        self.closed = False

    def request(self, method, url, **kwargs):
        return FakeRequestContext(self._response)

    async def close(self):
        self.closed = True


class FakeLimiterService:
    def __init__(self):
        self.release_calls = []

    async def release(self, **kwargs):
        self.release_calls.append(kwargs)
        return SimpleNamespace(ok=True, removed=True)


def run(coro):
    return asyncio.run(coro)


def _build_limiter_context() -> dict:
    return {
        "release_required": True,
        "release_state": "pending",
        "subject_key": "subject-1",
        "model_name": "model-1",
        "request_id": "limiter-req-1",
        "ttl_ms": 30000,
    }


def _build_user_data(limiter_context: dict) -> dict:
    return {
        "call_time": datetime.now(),
        "api_key": "digest-1",
        "client_ip": "127.0.0.1",
        "chat_log_type": 1,
        "model_name": "model-1",
        "real_model_name": "real-model-1",
        "tools_count": 0,
        "limiter_context": limiter_context,
    }


@pytest.fixture(autouse=True)
def _reset_vlm_state(monkeypatch):
    VlmService._limiter_service = None
    VlmService._image_generation_inflight.clear()

    async def _noop_save_log_task_stream(cls, log_entry, buffer):
        return None

    monkeypatch.setattr(VlmService, "_save_log_task_stream", classmethod(_noop_save_log_task_stream))
    yield
    VlmService._limiter_service = None
    VlmService._image_generation_inflight.clear()


async def _submit_stream_request(client: AsyncHttpClient, user_data: dict, *, response: FakeResponse):
    client.session = cast(Any, FakeSession(response))
    req_wrapper = RequestWrapper(
        url="https://upstream.example/v1/stream",
        method="POST",
        json={"stream": True},
        is_stream=True,
        keep_content_in_memory=True,
        on_success=VlmService._request_stream_finish_callback,
        on_failure=VlmService._request_stream_finish_callback,
        user_data=user_data,
        retry_on_stream_error=False,
        max_retries=0,
    )
    req_id = client.submit(req_wrapper)
    return req_id


def test_stream_normal_completion_releases_once_on_worker_terminal():
    async def _case():
        limiter = FakeLimiterService()
        VlmService._limiter_service = cast(Any, limiter)

        client = AsyncHttpClient(result_retention_seconds=5)
        limiter_context = _build_limiter_context()
        req_id = await _submit_stream_request(
            client,
            _build_user_data(limiter_context),
            response=FakeResponse(status=200, chunks=[b"a", b"b", b"c"]),
        )

        await client.wait_for_upstream_status(req_id)
        chunks = []
        async for chunk in client.stream_generator(req_id):
            chunks.append(chunk)

        assert chunks == [b"a", b"b", b"c"]
        await asyncio.sleep(0.05)
        assert len(limiter.release_calls) == 1
        assert limiter_context["release_state"] == "released"

        await client.close()

    run(_case())


def test_stream_client_disconnect_does_not_release_early():
    async def _case():
        limiter = FakeLimiterService()
        VlmService._limiter_service = cast(Any, limiter)

        client = AsyncHttpClient(result_retention_seconds=5)
        limiter_context = _build_limiter_context()
        req_id = await _submit_stream_request(
            client,
            _build_user_data(limiter_context),
            response=FakeResponse(status=200, chunks=[b"x", b"y", b"z"], delay=0.03),
        )

        await client.wait_for_upstream_status(req_id)

        gen = client.stream_generator(req_id)
        first_chunk = await gen.__anext__()
        assert first_chunk == b"x"
        await gen.aclose()

        await asyncio.sleep(0.01)
        assert len(limiter.release_calls) == 0

        await client.content(req_id)
        await asyncio.sleep(0.05)
        assert len(limiter.release_calls) == 1
        assert limiter_context["release_state"] == "released"

        await client.close()

    run(_case())


def test_stream_upstream_failure_releases_once():
    async def _case():
        limiter = FakeLimiterService()
        VlmService._limiter_service = cast(Any, limiter)

        client = AsyncHttpClient(result_retention_seconds=5)
        limiter_context = _build_limiter_context()
        req_id = await _submit_stream_request(
            client,
            _build_user_data(limiter_context),
            response=FakeResponse(status=500, chunks=[b"upstream failed"]),
        )

        with pytest.raises(Exception):
            await client.wait_for_upstream_status(req_id)

        await asyncio.sleep(0.05)
        assert len(limiter.release_calls) == 1
        assert limiter_context["release_state"] == "released"

        await client.close()

    run(_case())


def test_release_limiter_context_once_is_idempotent():
    async def _case():
        limiter = FakeLimiterService()
        VlmService._limiter_service = cast(Any, limiter)

        limiter_context = _build_limiter_context()
        await VlmService._release_limiter_context_once(limiter_context)
        await VlmService._release_limiter_context_once(limiter_context)

        assert len(limiter.release_calls) == 1
        assert limiter_context["release_state"] == "released"
        assert limiter.release_calls[0]["request_id"] == "limiter-req-1"

    run(_case())
