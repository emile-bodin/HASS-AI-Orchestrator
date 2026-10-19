# Specialist agent management (0.13.12)

## What changes

Install the add-on update normally first: installing new code still requires the
update/restart. After that, Factory deployment and agent edits hot-load without
restarting the backend. Only the changed specialist is replaced; other specialists
and unrelated modern reasoning runs are not restarted.

- In **Automation → Agents → New agent**, generate/review a blueprint, then edit
  its entity selection. Entities are grouped by domain, searchable by friendly name
  or ID, and show selected counts. Uncheck to remove. **Deploy paused** registers
  the agent immediately with `paused` status, without running a decision.
- Each card and detail panel has **Pause / Resume**. Activation intent is persisted
  in `agents.yaml`. New agents always start paused even if a submitted blueprint
  says otherwise. Existing configs without `paused` keep their prior enabled intent;
  installing this version does not automatically pause or resume existing agents.
- Edit an existing agent's instruction, name, interval and explicit entity selection.
  Instruction edits no longer silently infer/replace the selected entities. A save
  cancels/drains the changed agent's current local work and starts a fresh cycle if
  it was active. It does not replay a partially completed decision.
- Delete removes the configuration and stops/drains that specialist's local work.

## Execution model and limits

This feature deliberately retains `enable_legacy_autonomous_loops`. It does **not**
migrate specialists to the modern deterministic kernel, add a trigger for each new
agent, or enable global autonomous loops. With legacy loops disabled, old unpaused
agents are `ready`, not stuck `initializing`; new agents are `paused`. Resume returns
an explanatory error without changing the pause flag or global setting. Enabling
legacy loops remains a separate explicit add-on configuration/install decision.

Pause closes the specialist execution gate, cancels and awaits its cadence/delegated
tasks, drains independently dispatched MCP calls, and invalidates stale central
planner actions. A successful response means no further **local specialist**
mutations can start until explicit resume. A service request already sent to or
accepted by HA may still finish remotely: pause cannot undo it. A cancelled model
call running in a worker thread may finish computing, but its cancelled decision is
not executed. Resume creates fresh work, not continuation or replay. Task ownership
is single-process, matching the existing backend; this is not a multi-worker
coordinator or a filesystem watcher for arbitrary external YAML edits.

A specialist pause is **not** an entity lock: a separate user goal, modern trigger,
HA automation or another agent may still act on the same entity. Modern kernel
budgets, durable plans, ordered replay/checkpoints and exact-plan approvals should
not be advertised as guarantees of this retained legacy specialist decision loop.
The existing MCP dry-run/approval policy remains in effect; this feature neither
bypasses it nor grants extra approval authority.

## Entity policy

The catalog comes from the application's existing HA WebSocket state inventory,
not a caller-supplied URL. New selections must be real HA entity IDs in the actual
application `allowed_domains` and outside `blocked_domains`; blocked wins. Policy
uses the same environment/default resolution as MCP service validation. If HA is
disconnected, inventory validation fails closed; retry when it reconnects.

Existing restricted/missing configured IDs remain visible and removable. Edits may
retain such historical IDs, but cannot newly add them; resume and execution require
all selected IDs to exist and be allowed under current policy. Removing an old
restricted ID prevents adding it back while it remains restricted. No nonempty
explicit selection means **no autonomous actions**, never an implicit all-entities
scope. Legacy YAML configs are still loaded, but an old empty/dynamic selection must
be replaced with explicit allowed entities before it can execute actions safely.

Managed specialist MCP calls reject entity targets outside the selected IDs,
including nested entity selectors, and reject area/device/floor/label and targetless
generic service calls. Specialized calls still use their existing tool validation,
dry-run and approval checks. This is not a global MCP sandbox: knowledge searches,
modern goals/external tools and direct independent HA clients are not made
entity-isolated by this selector.

The single lifecycle service serializes API changes and writes YAML using a flushed,
fsynced temporary file and atomic replacement. Construction failure or disk-write
failure leaves the old runtime untouched; registration/scheduling failure restores
prior persisted configuration and runtime intent where the scheduler is available.
A genuinely broken scheduler/disk still requires operator attention, not invented
success. API changes do not rewrite unrelated HA options or silently enable loops.

## API

- `GET /api/factory/entities`: named entity catalog with `domain` and `allowed`.
- `POST /api/factory/save`: `{ "config": { "id", "name", "instruction", "entities": [...] } }`;
  creates and registers paused. Duplicate IDs are rejected.
- `PATCH /api/factory/agents/{id}`: updates explicit name/instruction/interval/entities.
- `POST /api/factory/agents/{id}/pause` and `/resume`: persisted activation changes.
- `DELETE /api/factory/agents/{id}`: persisted deletion plus local drain.
- `GET /api/agents` includes `paused` and `legacy_enabled` in addition to runtime status.

## Safe local acceptance guide

Never run acceptance mutations against a live home without separate approval.
The repository includes a **model-free, HA-free loopback fixture** using the real
Factory API and lifecycle but an inert test agent (its loop only counts starts).

1. From `ai-orchestrator/backend`, use a compatible isolated environment:
   `python -I tests/agent_management_fixture.py`. It binds only `127.0.0.1:18999`
   and stores YAML in a temporary directory under `TMPDIR`. No `main` startup,
   HA connection, model request, `/config`, or live `/data` is used.
2. From `ai-orchestrator/dashboard`, run
   `npm run dev -- --host 127.0.0.1 --port 13000 --strictPort`.
3. Open `http://127.0.0.1:13000/tests/agent-management.fixture.html`.
   Click **New agent**, enter an instruction and generate the fixture blueprint.
4. Verify separate `light`, `sensor`, `switch` groups and friendly-name search.
   `switch.restricted` is disabled. Remove the last selected ID: deploy is disabled.
   Select kitchen and temperature; deploy. The card must say paused immediately.
5. Read `http://127.0.0.1:18999/api/fixture/state`: persisted `paused: true`, `tasks: 0`,
   agent `runs: 0`. Resume from the card: exactly one task/start. Pause: zero tasks.
6. Open details → Edit. Remove temperature, add hall and save. Re-read fixture state:
   exactly those explicit IDs persisted, pause remains true, no active task. No
   backend restart was needed. Delete and verify empty persisted/runtime maps.
7. At a 390 px viewport, verify grouped picker remains usable without horizontal
   document overflow. Keyboard focus should stay inside the active dialog.
8. Stop both fixture servers when finished. Fixture success is not a live HA or
   provider inference result. The fixture is a dev-only HTML entry, not shipped in
   the production build.

Automated regressions: `backend/tests/test_agent_management.py` covers real API
routing, load/compatibility, lifecycle/task ownership, persistence failure/rollback,
policy/forged IDs, stale actions and pause draining. Run the backend suite in its
isolated interpreter (in the Hermes scratch environment the inspected sandbox
runner redirects `/data` to scratch); run `node --test tests/*.test.mjs` and
`npm run build` from the dashboard. No live credentials are required.
