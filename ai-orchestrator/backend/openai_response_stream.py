"""Validate Responses snapshots and extract guarded text/function output."""
from collections.abc import Mapping
from copy import copy, deepcopy
import json
from typing import Any


def response_value(value: Any, key: str, default: Any = None) -> Any:
    """Read both SDK models and mapping snapshots without coercing content."""
    return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)


def completed_response(event: Any) -> Any:
    """Return the completed snapshot, never partial output or a failed response.

    Validate either the terminal snapshot or one repaired from finalized items;
    never reconstruct function arguments or reasoning from deltas. No payloads
    in exceptions: they can contain private conversation/reasoning content.
    """
    event_type = response_value(event, "type")
    if event_type in ("response.failed", "response.incomplete", "error"):
        raise RuntimeError(f"OpenAI OAuth stream terminated with {event_type}")
    if event_type != "response.completed":
        return None
    response = response_value(event, "response")
    status = response_value(response, "status")
    if status != "completed":
        if status in ("failed", "incomplete"):
            raise RuntimeError(f"OpenAI OAuth stream response is {status}")
        raise RuntimeError("OpenAI OAuth stream has invalid response.completed")
    if not isinstance(response_value(response, "output"), list):
        raise RuntimeError("OpenAI OAuth stream has invalid response.completed output")
    return response


class ResponseStreamAccumulator:
    """Per-request finalized items shared by sync and async OAuth consumers.

    SDK 2.45's stream helper does not recover done items. Use only actual done
    payloads, never assembled deltas, and retain terminal metadata/SDK models.
    A populated terminal snapshot remains authoritative.
    """

    def __init__(self) -> None:
        self._completed: dict[int, Any] = {}
        self._seen: set[int] = set()
        self._added: dict[int, Any] = {}
        self._response_id: Any = None

    def feed(self, event: Any) -> Any:
        event_type = response_value(event, "type")
        if event_type in {"response.created", "response.queued", "response.in_progress", "response.completed"}:
            identity = response_value(response_value(event, "response"), "id")
            if self._response_id is not None and identity != self._response_id:
                raise RuntimeError("OpenAI OAuth stream response identity changed")
            self._response_id = identity
        index = response_value(event, "output_index")
        if index is not None:
            if type(index) is not int or index < 0:
                raise RuntimeError("OpenAI OAuth stream has invalid output index")
            self._seen.add(index)
        if event_type == "response.output_item.added":
            self._added[index] = deepcopy(response_value(event, "item"))
        if event_type == "response.output_item.done":
            if index is None:
                raise RuntimeError("OpenAI OAuth stream has invalid output index")
            item = response_value(event, "item")
            if not isinstance(response_value(item, "type"), str):
                raise RuntimeError("OpenAI OAuth stream has invalid completed item")
            previous = self._completed.get(index)
            added = self._added.get(index)
            if previous is not None and previous != item:
                raise RuntimeError("OpenAI OAuth stream has conflicting completed items")
            if added is not None and any(
                response_value(added, key) is not None and
                response_value(added, key) != response_value(item, key)
                for key in ("type", "id", "call_id")
            ):
                raise RuntimeError("OpenAI OAuth stream completed item identity changed")
            for position, other in self._completed.items():
                if position != index and any(
                    response_value(item, key) is not None and
                    response_value(item, key) == response_value(other, key)
                    for key in ("id", "call_id")
                ):
                    raise RuntimeError("OpenAI OAuth stream has duplicate output identity")
            self._completed[index] = deepcopy(item)
            return None
        if event_type == "response.completed":
            response = response_value(event, "response")
            if response_value(response, "status") != "completed":
                return completed_response(event)  # Reuse safe terminal validation.
            output = response_value(response, "output")
            if output is None or output == []:
                if self._seen - self._completed.keys():
                    raise RuntimeError("OpenAI OAuth stream has unfinished output items")
            if (output is None or output == []) and self._completed:
                response = dict(response) if isinstance(response, Mapping) else copy(response)
                items = [self._completed[index] for index in sorted(self._completed)]
                if any(response_value(item, "status") not in (None, "completed") for item in items):
                    raise RuntimeError("OpenAI OAuth stream has incomplete completed items")
                if isinstance(response, dict):
                    response["output"] = items
                else:
                    response.output = items
                return completed_response({"type": "response.completed", "response": response})
        return completed_response(event)


# Only allowlisted protocol labels and counts cross the diagnostic boundary.
# Never echo arbitrary type/status strings, text, arguments, IDs, or reasoning.
_OUTPUT_TYPES = frozenset({
    "message", "reasoning", "function_call", "custom_tool_call", "mcp_call",
    "mcp_list_tools", "mcp_approval_request", "computer_call", "web_search_call",
    "file_search_call", "code_interpreter_call", "image_generation_call",
    "local_shell_call", "shell_call", "apply_patch_call", "tool_search_call",
    "program", "program_output", "compaction",
})


def response_shape(response: Any) -> dict:
    """Bounded, allowlisted metadata; safe even for unknown/malformed payloads."""
    output = response_value(response, "output")
    items = output if isinstance(output, list) else []
    types = set()
    blocks = set()
    for item in items:
        kind = response_value(item, "type")
        types.add(kind if isinstance(kind, str) and kind in _OUTPUT_TYPES else "unknown")
        content = response_value(item, "content")
        if isinstance(content, list):
            for block in content:
                kind = response_value(block, "type")
                blocks.add(kind if isinstance(kind, str) and kind in {"output_text", "text", "refusal"} else "unknown")
    status = response_value(response, "status")
    return {
        "status": status if isinstance(status, str) and status in {
            "completed", "failed", "incomplete", "in_progress", "cancelled", "queued",
        } else "unknown",
        "snapshot_kind": "mapping" if isinstance(response, Mapping) else "object",
        "output_count": len(items), "output_types": sorted(types), "content_types": sorted(blocks),
    }


def response_output(response: Any, *, allow_tools: bool = False) -> tuple[str, list]:
    """Accept only usable text or locally supported JSON function calls.

    Hosted/custom/program tools have different protocols and must not be
    mistaken for an empty successful answer or bypass the guarded registry.
    Preserve original output items separately for stateless continuation.
    """
    def fail(reason: str) -> None:
        raise RuntimeError(f"OpenAI response {reason}; shape=" + json.dumps(response_shape(response), sort_keys=True))

    if response_value(response, "status") != "completed":
        fail("not completed")
    output = response_value(response, "output")
    if not isinstance(output, list):
        fail("has invalid output")
    final_text = []
    commentary = []
    calls = []
    for item in output:
        kind = response_value(item, "type")
        if kind == "reasoning":
            continue
        if kind == "function_call" and allow_tools:
            if response_value(item, "status") not in (None, "completed"):
                fail("has incomplete function call")
            if not all(isinstance(response_value(item, key), str) and response_value(item, key).strip()
                       for key in ("name", "call_id")):
                fail("has invalid function call")
            arguments = response_value(item, "arguments")
            if not isinstance(arguments, str):
                fail("has invalid function arguments")
            try:
                parsed = json.loads(arguments)
            except (ValueError, TypeError):
                fail("has invalid function arguments")
            if not isinstance(parsed, dict):
                fail("has invalid function arguments")
            calls.append(item)
        elif kind == "message":
            if response_value(item, "status") not in (None, "completed"):
                fail("has incomplete message")
            content = response_value(item, "content")
            if not isinstance(content, list):
                fail("has invalid message content")
            phase = response_value(item, "phase")
            if phase not in (None, "commentary", "final_answer"):
                fail("has unsupported message phase")
            for block in content:
                block_type = response_value(block, "type")
                if block_type == "refusal":
                    fail("contains refusal")
                if block_type not in ("output_text", "text"):
                    fail("has unsupported content type")
                text = response_value(block, "text")
                if not isinstance(text, str):
                    fail("has invalid text")
                (commentary if phase == "commentary" else final_text).append(text)
        else:
            fail("has unsupported output type")
    text = "\n".join(final_text).strip()
    if not text and not calls:
        fail("contains commentary without final answer" if commentary else "has empty output without tool calls")
    return text or "\n".join(commentary).strip(), calls
