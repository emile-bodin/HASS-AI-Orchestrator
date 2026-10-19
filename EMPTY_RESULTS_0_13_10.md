# Empty agent results — 0.13.10 investigation

## Scope and confidence

The reported 0.13.9 run returned `final`, one step, no tools and an empty answer. A healthy Home Assistant WebSocket does not establish that a model requested or received a Home Assistant tool observation. No live Home Assistant or OAuth inference credentials were available for this investigation. The precise response payload from that run is unavailable, so its upstream trigger is **not confirmed**.

Work started from clean `origin/main` at `c1e6d01` on a fresh branch. The local tests use OpenAI Python SDK 2.45.0 and Python 3.13.5 in an isolated environment.

## Verified defects

1. **False success in the harness.** `LLMResponse.is_final` meant only “no function calls.” The harness returned `final` even for empty or whitespace-only text. A deterministic test reproduced the reported success/empty combination before the fix. It now returns `llm_error` with a non-empty error explanation, not invented home data.
2. **Unusable Responses output silently disappeared.** Completed SSE responses containing only reasoning, refusals, unsupported tool items or no output became empty text and no calls. A commentary-only assistant message was also incorrectly treated as a final answer. Real SDK/local-SSE regression tests failed before the fix. The parser now validates response status, output items, content blocks and message phase; it rejects unusable output. Custom/hosted/program tools are not coerced into local JSON function calls or executed outside the guarded registry.
3. **Mapping snapshots were partially unsupported.** Nested item parsing supported mappings, but outer `output`, `usage`, response ID and status used attribute-only access. A mapping response lost its text, token accounting and continuation ID. Mapping events were also ignored by the terminal-event helper. Separate red/green tests demonstrate both defects. Real SDK SSE currently gives object snapshots in these tests; there is no evidence that the user's failing response was a mapping.
4. **Failure still produced a plan.** After any harness stop, plan/auto mode built a proposal. With no mutations it marked it `executed`; with partial mutations auto mode could replay those intentions after a later model failure. Tests demonstrate both situations. Only a non-empty successful final run can now publish or replay a plan.
5. **Frontend trusted the status label alone.** Any `final` displayed “Goal completed,” including empty legacy results. Result classification now requires non-empty text and distinguishes model/tool failure from budget limits. Failed results cannot display an inline completed plan.
6. **OAuth model selection and logging.** DeepReasoningAgent only selected `openai_model` for provider `openai`, not `openai_oauth`. With the default Gemma local-model setting the factory replaced Gemma with `OPENAI_MODEL`, so the old “openai backend (gemma4:...)” log was a stale label, not evidence of an Ollama request. However, an explicit OpenAI model argument was ignored, and a non-Gemma local-model name could be sent as the OAuth cloud model. Both actual model-selection defects have red/green tests. OAuth selection is now explicit and logging reads the instantiated backend model.

## Preserved contracts

- OAuth requests still use `responses.create(stream=True)` and only completed snapshots, not partial deltas.
- API-key Responses requests remain non-streaming.
- JSON function calls still run through the registry, schema validation, semantics and plan interceptor.
- Usage, opaque reasoning, call IDs and assistant message phases survive stateless continuation.
- Provider defaults, RAG defaults and existing user settings are not changed.
- An unknown-shape failure includes only allowlisted protocol labels, counts and snapshot representation. Arbitrary unknown labels are replaced with `unknown`. Text, arguments, IDs, credentials and encrypted reasoning are never included in these new diagnostics.

## Tested tool-to-answer path

`test_sdk_sse_agent_runs_guarded_read_observes_and_answers` runs DeepReasoningAgent with the real async SDK and local SSE HTTP transport. The first response contains opaque reasoning, phase-labelled commentary and a JSON function call. The guarded registry executes a read-only fixture tool. The next HTTP request must contain the original reasoning/message/call items and the correct call-ID observation. The transport builds its final fixture answer from that observation, and the test verifies the emitted result and persisted read-only plan. A second case returns an empty completion after the same successful read: it must emit `llm_error`, include an empty-output diagnostic, and save no plan. Fixture lights are explicitly test data, not observations from the user's home.

Additional tests cover empty/whitespace final output, both snapshot representations, OAuth model selection, refusals, unknown types, custom tools, hosted MCP, commentary-only completions, failed/incomplete streams, interruptions, cancellation, token usage and API-key behavior. Frontend outcome tests run with Node's built-in test runner; Vite production build validates component integration.

## Legacy routing findings, not changed in this patch

`AIOrchestrator` constructs both a provider facade and an Ollama client, then aliases `self.llm_client` to the Ollama client. Its legacy `plan`, legacy `chat`, and non-Gemini `generate_visual_dashboard` methods call that client directly. Those are real direct-Ollama paths even with a cloud provider selected, but they are separate from the deep-reasoning `/api/reasoning` harness involved in the reported empty result. Specialist `BaseAgent._call_llm` uses the provider facade; its missing explicit OAuth model-map entry is currently compensated for Gemma model names by the OAuth facade, not evidence that every agent request is local. Routing redesign of the legacy paths is outside this focused fix.

## Primary references consulted

- [OpenAI function-calling guide](https://developers.openai.com/api/docs/guides/function-calling): function-call output items identify JSON arguments, name and call ID; the application executes the function and submits `function_call_output`; reasoning items must accompany tool outputs for reasoning models.
- [OpenAI reasoning guide](https://developers.openai.com/api/docs/guides/reasoning): token exhaustion can produce `incomplete` before any visible answer; persisted reasoning is opaque. Token usage alone cannot prove a useful answer was produced.
- [Official SDK Response type and output_text property](https://github.com/openai/openai-python/blob/main/src/openai/types/responses/response.py): `output_text` is a derived convenience property over text blocks, not a canonical field required on a wire mapping. It can be empty without a text message.
- [Official SDK output message schema](https://github.com/openai/openai-python/blob/main/src/openai/types/responses/response_output_message.py): messages distinguish commentary and final-answer phases; preserve phase on subsequent turns.
- [Official SDK function-call schema](https://github.com/openai/openai-python/blob/main/src/openai/types/responses/response_function_tool_call.py) and [custom-tool schema](https://github.com/openai/openai-python/blob/main/src/openai/types/responses/response_custom_tool_call.py): JSON functions and free-form custom tools use different protocols.

These sources establish the expected interfaces; they do not identify which upstream shape occurred in the live reported run.
