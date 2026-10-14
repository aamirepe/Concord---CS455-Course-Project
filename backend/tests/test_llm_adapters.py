"""Unit tests for the Mistral and HuggingFace LLM adapters, with the HTTP
layer replaced by an httpx.MockTransport (real httpx.Response/Request
objects, no network) — no adapter had any test coverage before this.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.llm import adapters as adapters_module
from app.llm.adapters import HuggingFaceAdapter, MistralAdapter

_RealAsyncClient = httpx.AsyncClient


def _patch_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Make `httpx.AsyncClient()` (called with no args in both adapters)
    route through a MockTransport instead of the network."""
    transport = httpx.MockTransport(handler)

    def _fake_async_client(*args, **kwargs):
        return _RealAsyncClient(transport=transport)

    monkeypatch.setattr(adapters_module.httpx, "AsyncClient", _fake_async_client)


@pytest.mark.asyncio
async def test_mistral_generate_completion_success(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})

    _patch_client(monkeypatch, handler)
    adapter = MistralAdapter(api_key="0123456789")
    result = await adapter.generate_completion(prompt="hi", system_prompt="sys")
    assert result == "hello"


@pytest.mark.asyncio
async def test_mistral_generate_completion_http_error_propagates(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid api key"})

    _patch_client(monkeypatch, handler)
    adapter = MistralAdapter(api_key="0123456789")
    with pytest.raises(httpx.HTTPStatusError):
        await adapter.generate_completion(prompt="hi", system_prompt="sys")


@pytest.mark.asyncio
async def test_mistral_generate_completion_stream_reconstructs_text(monkeypatch: pytest.MonkeyPatch):
    chunks = ["hel", "lo ", "world"]
    sse_body = "".join(
        f"data: {json.dumps({'choices': [{'delta': {'content': c}}]})}\n\n" for c in chunks
    )
    sse_body += "data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse_body.encode())

    _patch_client(monkeypatch, handler)
    adapter = MistralAdapter(api_key="0123456789")
    received = [c async for c in adapter.generate_completion_stream(prompt="hi", system_prompt="sys")]
    assert "".join(received) == "hello world"


def test_mistral_validate_api_key():
    adapter = MistralAdapter(api_key="0123456789")
    assert adapter.validate_api_key("0123456789") is True
    assert adapter.validate_api_key("short") is False


@pytest.mark.asyncio
async def test_huggingface_generate_completion_success(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"generated_text": "hello from hf"}])

    _patch_client(monkeypatch, handler)
    adapter = HuggingFaceAdapter(api_key="hf_0123456789")
    result = await adapter.generate_completion(prompt="hi", system_prompt="sys")
    assert result == "hello from hf"


@pytest.mark.asyncio
async def test_huggingface_error_response_raises_instead_of_leaking_as_text(
    monkeypatch: pytest.MonkeyPatch,
):
    """Regression test: HF's error dict responses (e.g. model still loading)
    used to be silently returned as if they were a real completion."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "Model is currently loading"})

    _patch_client(monkeypatch, handler)
    adapter = HuggingFaceAdapter(api_key="hf_0123456789")
    with pytest.raises(RuntimeError, match="Model is currently loading"):
        await adapter.generate_completion(prompt="hi", system_prompt="sys")


@pytest.mark.asyncio
async def test_huggingface_stream_is_faked_from_a_single_request(monkeypatch: pytest.MonkeyPatch):
    """HuggingFaceAdapter doesn't actually stream from HF — it makes one
    normal request then chunks the full text client-side."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=[{"generated_text": "one two three four five six"}])

    _patch_client(monkeypatch, handler)
    adapter = HuggingFaceAdapter(api_key="hf_0123456789")
    received = [c async for c in adapter.generate_completion_stream(prompt="hi", system_prompt="sys")]

    assert call_count == 1
    assert len(received) > 1
    assert "".join(received).split() == "one two three four five six".split()


def test_huggingface_validate_api_key():
    adapter = HuggingFaceAdapter(api_key="hf_0123456789")
    assert adapter.validate_api_key("hf_0123456789") is True
    assert adapter.validate_api_key("0123456789") is False


def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    delays: list[float] = []

    async def _sleep(d: float) -> None:
        delays.append(d)

    monkeypatch.setattr(adapters_module.asyncio, "sleep", _sleep)
    return delays


def test_mistral_default_model_supports_tools():
    adapter = MistralAdapter(api_key="0123456789")
    assert adapter.model == "codestral-latest"
    assert adapter.supports_tools is True
    assert MistralAdapter(api_key="0123456789", model="open-mistral-nemo").model == "open-mistral-nemo"


@pytest.mark.asyncio
async def test_mistral_chat_parses_tool_calls_and_usage(monkeypatch: pytest.MonkeyPatch):
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"id": "abc", "function": {"name": "read_file", "arguments": '{"file_id": 3}'}},
                        # Mistral sometimes returns arguments already decoded
                        {"id": "def", "function": {"name": "list_files", "arguments": {}}},
                    ],
                },
            }],
            "usage": {"prompt_tokens": 12, "completion_tokens": 5},
        })

    _patch_client(monkeypatch, handler)
    adapter = MistralAdapter(api_key="0123456789")
    tools = [{"type": "function", "function": {"name": "read_file", "parameters": {}}}]
    result = await adapter.chat([{"role": "user", "content": "hi"}], tools=tools)

    assert [(t.id, t.name) for t in result.tool_calls] == [("abc", "read_file"), ("def", "list_files")]
    assert json.loads(result.tool_calls[0].arguments) == {"file_id": 3}
    assert json.loads(result.tool_calls[1].arguments) == {}
    assert (result.prompt_tokens, result.completion_tokens, result.total_tokens) == (12, 5, 17)
    assert sent[0]["tools"] == tools and sent[0]["tool_choice"] == "auto"
    assert sent[0]["model"] == "codestral-latest"


@pytest.mark.asyncio
async def test_mistral_chat_without_tools_omits_tool_fields(monkeypatch: pytest.MonkeyPatch):
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    _patch_client(monkeypatch, handler)
    result = await MistralAdapter(api_key="0123456789").chat([{"role": "user", "content": "hi"}])
    assert result.content == "ok" and result.tool_calls == []
    assert "tools" not in sent[0] and "tool_choice" not in sent[0]


@pytest.mark.asyncio
async def test_mistral_retries_429_honouring_retry_after(monkeypatch: pytest.MonkeyPatch):
    delays = _no_sleep(monkeypatch)
    responses = [
        httpx.Response(429, headers={"Retry-After": "2"}, json={"message": "rate limited"}),
        httpx.Response(503, json={"message": "busy"}),
        httpx.Response(200, json={"choices": [{"message": {"content": "finally"}}]}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    _patch_client(monkeypatch, handler)
    result = await MistralAdapter(api_key="0123456789").chat([{"role": "user", "content": "hi"}])
    assert result.content == "finally"
    assert delays == [2.0, 2.0]  # Retry-After, then exponential backoff (1.0 * 2**1)


@pytest.mark.asyncio
async def test_mistral_gives_up_after_max_retries(monkeypatch: pytest.MonkeyPatch):
    delays = _no_sleep(monkeypatch)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"message": "rate limited"})

    _patch_client(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError):
        await MistralAdapter(api_key="0123456789").chat([{"role": "user", "content": "hi"}])
    assert calls == MistralAdapter.MAX_RETRIES + 1
    assert len(delays) == MistralAdapter.MAX_RETRIES


@pytest.mark.asyncio
async def test_mistral_does_not_retry_client_errors(monkeypatch: pytest.MonkeyPatch):
    _no_sleep(monkeypatch)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"message": "bad request"})

    _patch_client(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError):
        await MistralAdapter(api_key="0123456789").chat([{"role": "user", "content": "hi"}])
    assert calls == 1


@pytest.mark.asyncio
async def test_huggingface_chat_refuses_tools_and_flattens_messages(monkeypatch: pytest.MonkeyPatch):
    adapter = HuggingFaceAdapter(api_key="hf_0123456789")
    assert adapter.supports_tools is False
    with pytest.raises(NotImplementedError):
        await adapter.chat([{"role": "user", "content": "hi"}], tools=[{"type": "function"}])

    seen: dict = {}

    async def _fake_completion(prompt, system_prompt, **kwargs):
        seen.update(prompt=prompt, system_prompt=system_prompt)
        return "answer"

    monkeypatch.setattr(adapter, "generate_completion", _fake_completion)
    result = await adapter.chat([
        {"role": "system", "content": "be nice"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "bye"},
    ])
    assert result.content == "answer"
    assert seen["system_prompt"] == "be nice"
    assert seen["prompt"] == "user: hi\n\nassistant: hello\n\nuser: bye"
