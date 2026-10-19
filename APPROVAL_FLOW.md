# Approval flow — 0.13.14

## What the controls authorize

- **Plan only** reads state during reasoning but records mutations without invoking them. Every changing plan is pending and reviewable, including low-impact plans. Read-only runs have no approval requirement.
- **Auto-safe** records mutations first. A successfully finalized plan without high-impact steps replays inline. One high-impact step holds the **whole plan**, including its low-impact steps, for explicit approval. Saved cron/state/manual trigger modes reach this same path. Direct `execute` remains disabled by default; opt-in configurations retain that existing capability.
- **Approve whole exact plan** authorizes all displayed tool names and JSON arguments, sequentially, from the persisted server-side plan. No model is called again. The client sends only the plan ID; it cannot replace the payload. Approved replay supplies trusted context so local security handlers do not enqueue a second approval.
- **Legacy single-action approval** is a separate record/API, not a reasoning plan. New specialist/orchestrator and public MCP queue requests record a versioned tool/argument envelope and source configuration revision. `/api/approvals/{id}/approve` synchronously claims and executes that stored action through the same local MCP gates. The action center labels the two record types and shows exact arguments and saved results.

## Durable execution and failure behavior

SQLite compare-and-swap claims allow only one executor per record, including concurrent duplicate requests and separate queue/store instances. Rejection also uses a pending-only transition. Expired or rejected legacy requests cannot execute. Plan replay checkpoints after each completed step, stops on the first failed, queued, or unrecognized result, and marks remaining steps skipped. There is no automatic mutation retry or rollback.

An interrupted `executing` record stays **uncertain/in progress** across restart and cannot be approved again. Checkpoints are an audit trail, not an exactly-once transaction with Home Assistant: a crash after remote acceptance but before persistence can leave an uncertain result. Inspect the home and create a fresh plan deliberately; never reset statuses to retry blindly. Completed, simulated, rejected and failed plans cannot be replayed by another execute request.

New legacy actions must still belong to a present, unpaused specialist with legacy execution enabled, matching its persisted configuration revision. Edits and pause/resume rotate revision material; stale approvals do not become valid again after restart. Entity scope and current domain/service policy are checked inside the lifecycle execution gate. Pausing cannot retract a request already accepted by HA. Unknown historical queue shapes and unmanaged sources fail closed with an explicit failure; old system-approved rows are audit-only and have no replay consumer. Legacy autonomous loops remain opt-in; nothing here activates or migrates an agent.

## Simulation is not physical verification

Global simulation continues to use local MCP `dry_run`. For external MCP mutations it returns an explicit **local simulation** without sending a remote tool call, because external transports have no trusted dry-run contract. Read-only tools may still read. External mutations always require approved context; real external execution additionally requires a standard `domain`/`service` envelope that can pass the local service policy. Unknown mutation schemas fail closed as `unsupported_external_policy` outside simulation. Remote server tool semantics remain a trust boundary.

Outcomes are deliberately different:

- **Plan ready / Waiting approval:** recorded intent, nothing replayed.
- **Approved:** authority recorded, not evidence of execution (historical legacy records).
- **Executing:** executing or interrupted/uncertain; no automatic retry.
- **Executed (simulation):** simulated intent; no mutating device invocation. It is terminal, not a candidate for later real replay.
- **Executed · tool accepted:** the tool reported acceptance. **Not** evidence of the physical postcondition.
- **Execution failed or partial:** inspect per-step errors and skipped steps; earlier steps may have been accepted.
- **Reasoning failed / run finished with limits:** not a completed goal or an executable newly published plan.
- **Read-only completed:** reasoning completed without changes; no approval needed.

State may change between planning and approval. Approval does not automatically re-read/replan or replace stale arguments. Current local policy can reject previously recorded intent, but automatic home-state freshness validation, physical postcondition verification, distributed rollback and multi-worker lifecycle coordination are not implemented. Deployment assumes one application lifecycle owner; SQLite execution claims still protect duplicate execution across store instances.

## Local verification (no HA/model access)

`backend/tests/test_approval_flow_regression.py` exercises real stores, claims, actual approval API routes, local MCP validation, lifecycle boundaries, external simulation, failure abort, and restart behavior with fake effects only. Existing plan, trigger and agent-kernel tests cover plan creation and replay.

For browser acceptance, from `ai-orchestrator/backend` run an isolated Python interpreter against `tests/approval_flow_fixture.py`, then open `http://127.0.0.1:19014/#run`. Build the dashboard first. This loopback fixture reuses actual reasoning/approval route handlers and the real planner/harness/store/MCP code, but replaces model generation with a deterministic script and HA with a recording fake; all `/data` paths go to a scratch temporary directory. No agent loop is started.

Fixture goals: `high fixture actions` records lock then light, `fixture fail` produces a fake tool error, and `read-only fixture` creates no changes. `/api/fixture/state` exposes only fixture counters/results. `/api/fixture/simulation/{true|false}` changes only that fake server's simulation setting. Stop the fixture when finished. This verifies the local UX, not live HA, provider inference, an add-on upgrade, or actual hardware.
