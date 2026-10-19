"""Exercise OAuth requests through the real SDK with local SSE transports."""
import asyncio
import json

import httpx
import openai
import pytest

from llm_providers import _OpenAIOAuthChatProvider
from reasoning_harness import OpenAIResponsesBackend


class Store:
    def get_access_token_sync(self):
        return "test-oauth-token"

    async def get_access_token(self):
        return "test-oauth-token"


def response(output=None, status="completed"):
    return {
        "id": "resp_test", "object": "response", "created_at": 0,
        "model": "gpt-5.4-mini", "status": status,
        "output": output if output is not None else [{
            "type": "message", "id": "msg_test", "role": "assistant",
            "status": "completed", "content": [{
                "type": "output_text", "text": " hello ", "annotations": [],
            }],
        }],
        "usage": {"input_tokens": 12, "output_tokens": 7, "total_tokens": 19,
                  "input_tokens_details": {"cached_tokens": 3},
                  "output_tokens_details": {"reasoning_tokens": 4}},
        "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
    }


def sse(events):
    return "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)


def completed(output=None):
    return {"type": "response.completed", "sequence_number": 2, "response": response(output)}


def sync_provider(monkeypatch, events):
    requests = []
    transports = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if body.get("stream") is not True:
            return httpx.Response(400, json={"detail": "Stream must be set to true"})
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse(events))

    real_client = openai.OpenAI

    def client(**kwargs):
        transport = httpx.Client(transport=httpx.MockTransport(handler))
        transports.append(transport)
        return real_client(**kwargs, http_client=transport)

    monkeypatch.setattr(openai, "OpenAI", client)
    return _OpenAIOAuthChatProvider("gpt-5.4-mini", Store()), requests, transports


def test_simple_oauth_chat_requires_stream_and_returns_completed_text(monkeypatch):
    provider, requests, transports = sync_provider(monkeypatch, [
        {"type": "response.output_text.delta", "delta": "not the final snapshot", "sequence_number": 1,
         "item_id": "msg_test", "output_index": 0, "content_index": 0},
        completed(),
    ])
    assert provider.chat("gemma4:e4b", [
        {"role": "system", "content": "Be concise"}, {"role": "user", "content": "hi"},
    ], extra_options={"stream": False}) == "hello"
    assert requests[0]["stream"] is True
    assert requests[0]["store"] is False
    assert requests[0]["model"] == "gpt-5.4-mini"
    assert requests[0]["instructions"] == "Be concise"
    assert all(transport.is_closed for transport in transports)


@pytest.mark.asyncio
async def test_async_oauth_stream_preserves_tools_usage_and_encrypted_continuation():
    requests = []
    reasoning = {"type": "reasoning", "id": "rs_test", "summary": [],
                 "encrypted_content": "opaque-reasoning"}
    call = {"type": "function_call", "id": "fc_test", "call_id": "call_test",
            "name": "get_state", "arguments": '{"entity_id":"light.test"}', "status": "completed"}

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if body.get("stream") is not True:
            return httpx.Response(400, json={"detail": "Stream must be set to true"})
        events = [completed([reasoning, call])] if len(requests) == 1 else [completed()]
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse(events))

    backend = OpenAIResponsesBackend(api_key="test-key", oauth_store=Store(), store=False)
    await backend._client.close()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        async with openai.AsyncOpenAI(api_key="test-key", http_client=transport) as client:
            backend._client = client
            messages = [{"role": "system", "content": "Be concise"}, {"role": "user", "content": "check light"}]
            first = await backend.chat(messages, [])
            assert first.content == ""
            assert first.tool_calls[0].id == "call_test"
            assert first.tool_calls[0].name == "get_state"
            assert first.tool_calls[0].arguments == {"entity_id": "light.test"}
            assert first.usage["input_tokens"] == 12
            assert first.usage["output_tokens"] == 7
            assert first.usage["cached_input_tokens"] == 3
            assert first.usage["reasoning_tokens"] == 4
            messages += [{"role": "assistant", "tool_calls": []},
                         {"role": "tool", "tool_call_id": "call_test", "content": "on"}]
            final = await backend.chat(messages, [], continuation=first.continuation)
            assert final.content == "hello"
            assert final.stop_reason == "completed"
            assert final.raw.id == "resp_test"
            assert client.api_key == "test-oauth-token"
    assert len(requests) == 2
    assert all(body["stream"] is True and body["store"] is False for body in requests)
    assert requests[0]["include"] == ["reasoning.encrypted_content"]
    assert requests[1]["input"] == [
        {"role": "user", "content": "check light"}, reasoning, call,
        {"type": "function_call_output", "call_id": "call_test", "output": "on"},
    ]
    assert "previous_response_id" not in requests[1]


BAD_OUTPUTS = [
    pytest.param([], "empty", id="empty"),
    pytest.param([{"type": "reasoning", "id": "rs", "summary": [],
                   "encrypted_content": "SECRET"}], "empty", id="reasoning-only"),
    pytest.param([{"type": "message", "id": "msg", "role": "assistant", "status": "completed",
                   "content": [{"type": "refusal", "refusal": "SECRET"}]}], "refusal", id="refusal"),
    pytest.param([{"type": "custom_tool_call", "call_id": "call", "name": "read",
                   "input": "SECRET"}], "unsupported", id="custom-tool"),
    pytest.param([{"type": "mcp_call", "id": "mc", "name": "read", "arguments": "SECRET"}],
                 "unsupported", id="hosted-mcp"),
    pytest.param([{"type": "SECRET", "content": "SECRET"}], "unsupported", id="unknown-type"),
    pytest.param([{"type": "message", "id": "msg", "role": "assistant", "status": "completed",
                   "phase": "commentary", "content": [{"type": "output_text", "text": "checking", "annotations": []}]}],
                 "commentary", id="commentary-only"),
]


@pytest.mark.parametrize("output, message", BAD_OUTPUTS)
def test_simple_oauth_rejects_unusable_completed_output(monkeypatch, output, message):
    provider, _, transports = sync_provider(monkeypatch, [completed(output)])
    with pytest.raises(RuntimeError, match=message) as error:
        provider.chat("gpt-5.6-terra", [{"role": "user", "content": "check lights"}])
    assert "SECRET" not in str(error.value)
    assert "output_types" in str(error.value)
    assert all(transport.is_closed for transport in transports)


@pytest.mark.asyncio
@pytest.mark.parametrize("output, message", BAD_OUTPUTS)
async def test_async_oauth_rejects_unusable_completed_output(output, message):
    backend = OpenAIResponsesBackend(api_key="test-key", oauth_store=Store())
    await backend._client.close()
    async with openai.AsyncOpenAI(api_key="test-key", http_client=httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, text=sse([completed(output)]),
        )),
    )) as client:
        backend._client = client
        with pytest.raises(RuntimeError, match=message) as error:
            await backend.chat([{"role": "user", "content": "check lights"}], [])
        assert "SECRET" not in str(error.value)
        assert "output_types" in str(error.value)


BAD_STREAMS = [
    pytest.param([{"type": "response.failed", "sequence_number": 1,
                   "response": response(status="failed")}, completed()], "failed", id="failed"),
    pytest.param([{"type": "response.incomplete", "sequence_number": 1,
                   "response": response(status="incomplete")}, completed()], "incomplete", id="incomplete"),
    pytest.param([{"type": "error", "code": "server_error", "message": "stream error",
                   "param": None, "sequence_number": 1}, completed()], "error", id="error"),
    pytest.param([{"type": "response.output_text.delta", "delta": "partial", "sequence_number": 1,
                   "item_id": "msg_test", "output_index": 0, "content_index": 0}],
                 "without response.completed", id="no-completed"),
    pytest.param([{"type": "response.completed", "sequence_number": 1,
                   "response": response(status="incomplete")}], "incomplete", id="bad-status"),
    pytest.param([{"type": "response.output_item.done", "output_index": 0, "sequence_number": 1,
                   "item": response()["output"][0]}], "without response.completed", id="done-without-completion"),
    pytest.param([{"type": "response.output_item.done", "output_index": 0, "sequence_number": 1,
                   "item": response()["output"][0]},
                  {"type": "response.failed", "sequence_number": 2,
                   "response": response(status="failed")}], "failed", id="done-then-failed"),
    pytest.param([{"type": "response.output_item.done", "output_index": 0, "sequence_number": 1,
                   "item": response()["output"][0]},
                  {"type": "response.incomplete", "sequence_number": 2,
                   "response": response(status="incomplete")}], "incomplete", id="done-then-incomplete"),
    pytest.param([{"type": "response.completed", "sequence_number": 1}],
                 "invalid response.completed", id="missing-response"),
]


@pytest.mark.parametrize("events, message", BAD_STREAMS)
def test_simple_oauth_rejects_unsuccessful_stream(monkeypatch, events, message):
    provider, _, transports = sync_provider(monkeypatch, events)
    with pytest.raises((RuntimeError, openai.APIError), match=message):
        provider.chat("gpt-5.4-mini", [{"role": "user", "content": "hi"}])
    assert all(transport.is_closed for transport in transports)


@pytest.mark.asyncio
@pytest.mark.parametrize("events, message", BAD_STREAMS)
async def test_async_oauth_rejects_unsuccessful_stream(events, message):
    replies = []

    def handler(request):
        reply = httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse(events))
        replies.append(reply)
        return reply

    backend = OpenAIResponsesBackend(api_key="test-key", oauth_store=Store())
    await backend._client.close()
    async with openai.AsyncOpenAI(api_key="test-key", http_client=httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    )) as client:
        backend._client = client
        with pytest.raises((RuntimeError, openai.APIError), match=message):
            await backend.chat([{"role": "user", "content": "hi"}], [])
        assert all(reply.is_closed for reply in replies)


class BrokenSyncStream(httpx.SyncByteStream):
    closed = False

    def __iter__(self):
        yield sse([{"type": "response.created", "sequence_number": 0,
                    "response": response(status="in_progress")}]).encode()
        raise httpx.ReadError("connection interrupted")

    def close(self):
        self.closed = True


def test_simple_oauth_propagates_transport_failure_and_closes_stream(monkeypatch):
    stream = BrokenSyncStream()
    real_client = openai.OpenAI
    transport = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, headers={"content-type": "text/event-stream"}, stream=stream,
    )))
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: real_client(**kwargs, http_client=transport))
    with pytest.raises(httpx.ReadError, match="connection interrupted"):
        _OpenAIOAuthChatProvider("gpt-5.4-mini", Store()).chat("", [])
    assert stream.closed
    assert transport.is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [httpx.ReadError("connection interrupted"), asyncio.CancelledError()])
async def test_async_oauth_propagates_transport_failure_or_cancellation_and_closes_stream(failure):
    class BrokenAsyncStream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield sse([{"type": "response.created", "sequence_number": 0,
                        "response": response(status="in_progress")}]).encode()
            raise failure

        async def aclose(self):
            self.closed = True

    stream = BrokenAsyncStream()
    backend = OpenAIResponsesBackend(api_key="test-key", oauth_store=Store())
    await backend._client.close()
    async with openai.AsyncOpenAI(api_key="test-key", http_client=httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=stream,
        )),
    )) as client:
        backend._client = client
        with pytest.raises(type(failure)):
            await backend.chat([], [])
        assert stream.closed


@pytest.mark.asyncio
async def test_api_key_responses_remains_non_streaming():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response())

    backend = OpenAIResponsesBackend(api_key="test-key")
    await backend._client.close()
    async with openai.AsyncOpenAI(api_key="test-key", http_client=httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    )) as client:
        backend._client = client
        result = await backend.chat([{"role": "user", "content": "hi"}], [])
        assert result.content == "hello"
    assert "stream" not in requests[0]
