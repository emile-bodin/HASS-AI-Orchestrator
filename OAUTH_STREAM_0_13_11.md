# OAuth stream recovery and generation roles — 0.13.11

## Evidence and limits

The reported completed snapshot had `output_count=0`, no output/content types, and `status=completed`. The code previously ignored every `response.output_item.done` and used only the raw terminal output. A real-SDK/local-SSE regression reproduced that exact empty-output error with a finalized message preceding `response.completed.output=[]`; `output=null` instead produced the invalid-output error. This proves the parser defect, **not** that earlier done items existed in the affected live stream: those events were not captured. No live OAuth inference or live Home Assistant observation was performed.

The official implementation in `openai/openai-python`, `src/openai/lib/streaming/responses/_responses.py`, records finalized output by index and can repair null terminal output. The installed minimum supported SDK, OpenAI Python **2.45.0**, was inspected separately: its helper instead passes `event.response` directly into parsing and does not accumulate done items. Consequently this release implements its own small per-request accumulator over the public raw streaming API rather than assuming the latest helper is installed.

Primary references reviewed:
- [Official upstream streaming implementation](https://github.com/openai/openai-python/blob/main/src/openai/lib/streaming/responses/_responses.py)
- [OpenAI Python issue 3312](https://github.com/openai/openai-python/issues/3312)
- [OpenAI Python issue 3321](https://github.com/openai/openai-python/issues/3321)

The issues report null terminal output on the Codex backend. They are corroborating examples of this stream shape, not proof about this deployment or grounds for changing its API endpoint.

## Recovery contract

- Sync chat and async reasoning use the same accumulator, created afresh for each request.
- Require a successful `response.completed`; failed, incomplete, error, missing completion, interrupted transport and cancellation never produce executable partial calls.
- A populated terminal output remains authoritative, without merging in stale streamed items.
- Empty/null terminal output may be replaced only by actual `response.output_item.done` payloads, ordered by output index. Deltas, convenience text and added-but-unfinished items are never promoted to completed output.
- Preserve complete item content, encrypted reasoning, phases, call IDs, function arguments, terminal usage/ID/status and stateless continuation. Mapping snapshots and SDK models both work, without mutating the terminal input.
- Reject unfinished items, conflicting identities/completions, explicitly incomplete statuses, malformed JSON arguments and non-object argument values. Existing empty/refusal/commentary/unsupported-output guards remain in force.

## Effective generation model and startup options

For `llm_provider=openai_oauth`, configured `openai_model` controls orchestrator, smart, fast, deep and dashboard generation. Role values are resolved at runtime and in startup logs; saved HA options and agents.yaml are not rewritten. Legacy orchestrator planning, single-shot chat and dashboard generation now route through the selected provider rather than directly calling Ollama. OAuth factory failures do not fall back to Ollama generation. Other provider defaults remain unchanged.

RAG is independent: enabled RAG still requires local Ollama embeddings (`nomic-embed-text` by default); selecting OAuth does not disable it. A separate confirmed startup bug was `.enable_rag // true` (and `.dry_run_mode // true`): jq treats explicit `false` as absent for `//`. Explicit null checks now preserve false; missing/null still default to true, matching backend interpretation. No user option values are changed on disk. Honoring `dry_run_mode=false` means the user-selected non-dry-run behavior applies; other execution/approval policies remain unchanged.

## Verification

Regression tests were written and observed red before their production fixes. Python runs use the existing isolated venv with `python -I`, OpenAI SDK 2.45.0, and the external scratch sandbox runner for `/data` paths. Tests cover real HTTP/SSE SDK parsing, finalized text/tool calls with empty/null terminal output, registry-validated read-only tool -> observation -> final answer, genuine empty second turns, and zero tool execution on incomplete arguments/missing completion/failure. Shell tests execute the actual startup prefix using real bash/jq with false, true, missing and null options, verify effective role logs for OAuth and unchanged Ollama defaults, and verify temporary options files stay unchanged.

Frontend changes are limited to release metadata and the displayed fallback version; the outcome tests and production Vite build were run. Live external MCP tests remain opt-in and were skipped. No commit, push or PR is part of this change.
