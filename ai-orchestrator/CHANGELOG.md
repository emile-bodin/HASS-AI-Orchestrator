# Changelog

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
