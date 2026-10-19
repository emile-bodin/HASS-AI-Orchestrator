"""Terminal-event validation shared by the OAuth Responses consumers."""
from typing import Any


def completed_response(event: Any) -> Any:
    """Return the completed snapshot, never partial output or a failed response.

    Responses SSE includes the full output and usage in response.completed;
    using that snapshot preserves function arguments and encrypted reasoning
    without reconstructing them from deltas. Do not include provider payloads
    in exceptions: they can contain private conversation/reasoning content.
    """
    event_type = getattr(event, "type", None)
    if event_type in ("response.failed", "response.incomplete", "error"):
        raise RuntimeError(f"OpenAI OAuth stream terminated with {event_type}")
    if event_type != "response.completed":
        return None
    response = getattr(event, "response", None)
    status = getattr(response, "status", None)
    if status != "completed":
        if status in ("failed", "incomplete"):
            raise RuntimeError(f"OpenAI OAuth stream response is {status}")
        raise RuntimeError("OpenAI OAuth stream has invalid response.completed")
    if not isinstance(getattr(response, "output", None), list):
        raise RuntimeError("OpenAI OAuth stream has invalid response.completed output")
    return response
