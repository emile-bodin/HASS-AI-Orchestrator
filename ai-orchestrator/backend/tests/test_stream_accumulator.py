"""Recover only finalized wire items, never text/argument deltas."""
import pytest

from tests.test_oauth_responses_streaming import sync_provider, completed, response


def done(index, item):
    return {"type": "response.output_item.done", "output_index": index,
            "sequence_number": index + 1, "item": item}


def terminal(output):
    event = completed([])
    event["response"]["output"] = output
    return event


@pytest.mark.parametrize("output", [[], None])
def test_sync_recovers_done_text_from_empty_terminal(monkeypatch, output):
    provider, _, transports = sync_provider(monkeypatch, [
        {"type": "response.created", "sequence_number": 0, "response": response([], "in_progress")},
        {"type": "response.output_item.added", "sequence_number": 1, "output_index": 0,
         "item": {**response()["output"][0], "status": "in_progress", "content": []}},
        {"type": "response.output_text.delta", "sequence_number": 2, "output_index": 0,
         "content_index": 0, "item_id": "msg_test", "delta": " hello "},
        done(0, response()["output"][0]), terminal(output),
    ])
    assert provider.chat("", [{"role": "user", "content": "hi"}]) == "hello"
    assert all(transport.is_closed for transport in transports)


@pytest.mark.parametrize("events", [
    [done(0, response()["output"][0]),
     {"type": "response.output_item.added", "output_index": 1, "item": {"type": "function_call", "id": "fc", "status": "in_progress"}}],
    [done(0, response()["output"][0]),
     {"type": "response.function_call_arguments.delta", "output_index": 1, "delta": "{"}],
    [done(0, response()["output"][0]), done(0, {**response()["output"][0], "id": "other"})],
    [done(0, response()["output"][0]), done(1, response()["output"][0])],
    [done(0, {**response()["output"][0], "status": "in_progress"})],
    [done(0, {"type": "function_call", "id": "fc", "call_id": "call", "name": "read", "status": "completed", "arguments": "{"})],
])
def test_cannot_recover_unfinished_or_conflicting_items(events):
    from openai_response_stream import ResponseStreamAccumulator, response_output
    accumulator = ResponseStreamAccumulator()
    with pytest.raises(RuntimeError):
        for event in events + [terminal([])]:
            result = accumulator.feed(event)
            if result is not None:
                response_output(result, allow_tools=True)


@pytest.mark.parametrize("arguments", [None, "", "{", "[]", "null", "42", {"x": 1}])
def test_completed_tool_requires_complete_json_object(arguments):
    from openai_response_stream import response_output
    call = {"type": "function_call", "call_id": "call", "name": "read", "status": "completed", "arguments": arguments}
    with pytest.raises(RuntimeError, match="arguments"):
        response_output(response([call]), allow_tools=True)


def test_lifecycle_identity_cannot_change_during_recovery():
    from openai_response_stream import ResponseStreamAccumulator
    accumulator = ResponseStreamAccumulator()
    accumulator.feed({"type": "response.created", "response": response([], "in_progress")})
    accumulator.feed(done(0, response()["output"][0]))
    ending = terminal([])
    ending["response"]["id"] = "another_response"
    with pytest.raises(RuntimeError, match="identity"):
        accumulator.feed(ending)


def test_missing_terminal_response_with_done_item_fails_safely():
    from openai_response_stream import ResponseStreamAccumulator
    accumulator = ResponseStreamAccumulator()
    accumulator.feed(done(0, response()["output"][0]))
    with pytest.raises(RuntimeError, match="invalid response.completed"):
        accumulator.feed({"type": "response.completed"})


def test_incomplete_reasoning_is_not_recovered():
    from openai_response_stream import ResponseStreamAccumulator
    accumulator = ResponseStreamAccumulator()
    accumulator.feed(done(0, {"type": "reasoning", "id": "rs", "status": "in_progress"}))
    accumulator.feed(done(1, response()["output"][0]))
    with pytest.raises(RuntimeError, match="incomplete"):
        accumulator.feed(terminal([]))


def test_mapping_recovery_orders_gapped_items_preserves_metadata_without_mutating_terminal():
    from openai_response_stream import ResponseStreamAccumulator
    accumulator = ResponseStreamAccumulator()
    message = response()["output"][0]
    reasoning = {"type": "reasoning", "id": "rs", "encrypted_content": "opaque", "summary": []}
    accumulator.feed(done(5, message))
    accumulator.feed(done(2, reasoning))
    accumulator.feed(done(5, message))  # Exact retransmission is idempotent.
    ending = terminal(None)
    result = accumulator.feed(ending)
    assert result["output"] == [reasoning, message]
    assert result["usage"] == ending["response"]["usage"]
    assert result["id"] == ending["response"]["id"]
    assert ending["response"]["output"] is None
    assert ResponseStreamAccumulator().feed(terminal([]))["output"] == []


def test_populated_terminal_remains_authoritative_and_is_not_merged():
    from openai_response_stream import ResponseStreamAccumulator
    accumulator = ResponseStreamAccumulator()
    accumulator.feed(done(0, response()["output"][0]))
    ending = completed([{"type": "reasoning", "id": "authoritative", "summary": []}])
    assert accumulator.feed(ending) is ending["response"]


@pytest.mark.parametrize("output", [[], None])
def test_deltas_never_become_completed_output(monkeypatch, output):
    provider, _, _ = sync_provider(monkeypatch, [
        {"type": "response.output_text.delta", "output_index": 0, "content_index": 0,
         "item_id": "msg_test", "delta": "SECRET", "sequence_number": 1}, terminal(output),
    ])
    with pytest.raises(RuntimeError, match="unfinished") as error:
        provider.chat("", [])
    assert "SECRET" not in str(error.value)
