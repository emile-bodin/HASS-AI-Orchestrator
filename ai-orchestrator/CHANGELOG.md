# Changelog

## [0.13.14] - 2026-10-19
### Fixed — Plan, approve, execute
- Honor saved trigger modes; keep every changing Plan-only proposal visible for review and hold whole Auto-safe plans containing high-impact actions.
- Repair legacy approval queue callers and execute explicit human approvals from exact persisted payloads through current lifecycle, entity, domain and service checks. Persist source revision, atomic claim and executed/simulated/failed results; stale, paused, expired, rejected and unsupported historical work fails closed.
- Separate reasoning finality, pending approval, execution failure/partial, simulation and tool acceptance across the run workspace, chat and action center. Show exact action arguments, distinct legacy/whole-plan records, and durable results; retain API errors and disable in-flight approval controls.
- Prevent external mutations during global simulation; require explicit approved context and locally checkable service policy for real external mutations. Unknown result shapes and requeued actions are not successful execution.
- Keep atomic plan claims/checkpoints, abort later steps after failure, prevent reject/execution races and never automatically replay uncertain work after restart. No kernel migration or agent activation.
### Scope and verification
- Offline full backend: 482 passed, 4 live integration tests deselected; Node: 17 passed; Vite build, bash syntax and whitespace checks passed. A real browser exercised the loopback fixture with actual routes/stores and fake effects only, including exact replay, rejection, simulation, error text and mobile review containment. Browser screenshot capture timed out; DOM/API assertions passed.
- See APPROVAL_FLOW.md for guarantees and limits. Physical postcondition verification, stale-home-state replanning, live HA/model inference and add-on upgrade validation are not claimed.

## [0.13.13] - 2026-10-19
### Fixed
- Make ollama_mode and internal_ollama_port optional in the add-on schema so existing saved configurations remain valid after upgrading. New installations retain auto mode and port 11435 defaults; missing fields use the existing runtime defaults.
- Added a focused schema/default regression check. No manual configuration additions are required.

## [0.13.12] - 2026-10-19
### Fixed — Bundled Ollama lifecycle
- Start the managed server with persistent HOME/models and explicit f16 KV cache. Use a dedicated configurable loopback port (11435), refuse occupied ports, verify owned-process liveness and supervise backend/server shutdown together.
- Added auto/internal/external server selection and explicit effective endpoint binding for RAG embeddings/model pulls. External servers are never stopped or reconfigured. See OLLAMA_RUNTIME.md.
- Follow-up verification: 14 local lifecycle fixture tests passed; no live inference/container build. Full suite not rerun for this follow-up at user request.
### Added — Agent management without backend restart
- Searchable, editable entity selector grouped by HA domain for Factory deployment and agent edits; friendly names, selected counts, remove support, and restricted/missing historical entries.
- Server validates entity existence and the application's allowed/blocked domain policy (blocked wins). Empty selection is not dynamic/all. Instruction edits preserve explicit selections.
- Shared startup/factory lifecycle hot-loads new agents paused; edits affect only the changed specialist. Pause/resume persists in agents.yaml, deletion drains local work, and repeated resume does not duplicate cadence tasks.
- Guard legacy specialist dispatch and MCP execution, discard stale decisions, and cancel unfinished work rather than replaying it. Existing configs without paused preserve their prior activation intent; global legacy loops remain opt-in and resume never enables them.
- Atomic YAML replacement and rollback on runtime registration failure. Existing dry-run and approval checks stay in place; this is not a kernel migration or a global tool sandbox.

### Verification
- Isolated full backend suite: 432 passed, 4 opt-in live MCP tests skipped; 22 agent-management regressions. Node: 11 passed. Vite production build, bash syntax and git whitespace checks passed.
- Real browser exercised only the local HA-free/model-free fixture: grouped search, blocked/empty selections, paused deployment, resume/pause, persisted edits, deletion, and 390 px document-width checks. No live HA mutations or provider inference are claimed.

### Operating scope
- Install this update normally (add-on update/restart). Subsequent Factory saves/edits require no backend restart. Pause cannot retract HA requests already accepted remotely. See AGENT_MANAGEMENT.md for acceptance steps and exact limits.

## [0.13.11] - 2026-10-19
### Fixed
- Recover only finalized streamed output items when OAuth completion has empty/null output; preserve populated snapshots, reasoning, tools, usage, and continuation. Incomplete/failed/missing completion and invalid tool arguments still fail safely.
- Route orchestrator/smart/fast/deep/dashboard generation to the configured OAuth `openai_model`, including legacy paths and startup logs, without rewriting saved Home Assistant options or falling back to Ollama generation.
- Honor explicit false for `enable_rag` and `dry_run_mode` at startup; missing/null still default to true. Enabled RAG continues to require local Ollama embeddings independently of cloud generation.

### Verification
- Tested with the real SDK against local SSE transports and actual startup bash/jq expressions. No live OAuth/Home Assistant inference is claimed; prior streamed items in the affected deployment have not been captured.

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
