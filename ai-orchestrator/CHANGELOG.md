# Changelog

## [0.13.10] - 2026-10-19
### Fixed — Empty agent outcomes
- Empty, reasoning-only, refusal, commentary-only, incomplete, and unsupported Responses output no longer completes a goal successfully. Error diagnostics contain only allowlisted shape labels and counts, never model text, arguments, tokens, or encrypted reasoning.
- Supports both mapping snapshots and SDK objects without losing text, JSON function calls, usage, or continuation. Preserves assistant message phases and encrypted reasoning on tool turns.
- Failed reasoning runs do not publish a completed read-only plan, queue partial intentions, or auto-replay mutations. The dashboard distinguishes provider failures from budget limits and requires non-empty final text for success.
- OpenAI OAuth deep reasoning explicitly selects the OpenAI model and logs the actual backend model instead of the legacy local-model label. Existing provider defaults and user settings are unchanged.

### Verification
- Regression tests exercise the real OpenAI SDK against local SSE HTTP transports through guarded tool execution, returned observations, continuation, and a final answer, plus error and empty-completion cases. No live Home Assistant/OAuth inference is claimed.

## [0.13.9] - 2026-10-19
### Fixed
- OpenAI OAuth chat and reasoning requests now use the required streaming Responses API, fixing HTTP 400 "Stream must be set to true" after a successful sign-in.
- Failed, incomplete, interrupted, and missing-completion streams raise errors instead of returning partial answers or executing partial tool calls.
- Tool calls, token usage, and encrypted reasoning are preserved across OAuth tool-continuation turns. API-key provider behavior is unchanged.

### Verification
- Tested locally using the real OpenAI Python SDK with simulated SSE HTTP responses, including tool continuation, transport failures, and cancellation. Live OAuth inference has not been verified.

## [0.13.8] - 2026-10-19
### Fixed
- Uses the stable `urn:uuid:` host identifier required by OpenAI OAuth authorization.
- Keeps the laptop loopback callback and manual callback URL-paste flow for remote Home Assistant installations.
