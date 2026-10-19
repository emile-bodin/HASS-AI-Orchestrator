"""Validate Responses snapshots and extract guarded text/function output."""
from collections.abc import Mapping
import json
from typing import Any


def response_value(value: Any, key: str, default: Any = None) -> Any:
    """Read both SDK models and mapping snapshots without coercing content."""
    return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)


def completed_response(event: Any) -> Any:
    """Return the completed snapshot, never partial output or a failed response.

    Responses SSE includes the full output and usage in response.completed;
    using that snapshot preserves function arguments and encrypted reasoning
    without reconstructing them from deltas. Do not include provider payloads
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
