# Smoke Test Suite

Quick validation tests for Phase 1 core functionality.

## Agent management regressions (0.13.12)

`test_agent_management.py` exercises the real Factory router and shared specialist
lifecycle with temporary YAML and inert agents, without HA or model requests.
Coverage includes paused-by-default hot registration, repeated/concurrent resume,
legacy-off errors, restart compatibility, edit/delete, rollback/disk errors,
domain/blocked precedence, forged IDs, restricted historical removal, drain and
stale decision invalidation. Run it in the isolated backend interpreter alongside
the full suite. The same existing `/data` sandbox requirements below apply.

Dashboard: `node --test tests/*.test.mjs` and `npm run build`. The selector model
regressions cover grouping, friendly-name search, restricted additions and removal.
[Agent management](../../../AGENT_MANAGEMENT.md) documents the actual browser
fixture using the real API/lifecycle with a model-free loopback server.

## OAuth Responses and empty-result regression tests (0.13.11)

From `ai-orchestrator/backend`, use an isolated Python environment (not the system or agent runtime):

```bash
python3 -m venv .venv
.venv/bin/python -I -m pip install -r requirements-dev.txt
.venv/bin/python -I -m pytest tests/test_oauth_responses_streaming.py tests/test_stream_accumulator.py tests/test_oauth_model_roles.py tests/test_empty_agent_results.py tests/test_plan_executor_smoke.py tests/test_openai_oauth.py tests/test_version_contract.py -q
```

`-I` avoids inherited `PYTHONPATH` entries selecting another interpreter's native extensions. These tests use the real OpenAI SDK with local HTTP/SSE transports, not live credentials. They cover required streaming, completed snapshots, failed/incomplete/error streams, missing completion, transport interruption, cancellation/cleanup, tool-call usage and encrypted reasoning continuation, and unchanged API-key behavior.

The implementation follows the official [OpenAI Python SDK streaming implementation](https://github.com/openai/openai-python/blob/main/src/openai/lib/streaming/responses/_responses.py): `responses.create(stream=True)` with synchronous or asynchronous event iteration. A successful `response.completed` is mandatory. Its populated output is authoritative; empty/null output can be repaired only from fully received `response.output_item.done` payloads. Deltas are never exposed as completed answers or tool calls. The installed minimum SDK 2.45.0 does not implement this recovery itself, so no latest-version helper is assumed.

`test_oauth_model_roles.py` also executes the real startup bash/jq prefix against temporary options files (requires `bash` and `jq`), verifies false/true/missing/null defaults and unchanged saved options, and checks legacy generation routing without Ollama calls. Enabled RAG still uses local embeddings; OAuth selection does not disable it. See [OAUTH_STREAM_0_13_11.md](../../../OAUTH_STREAM_0_13_11.md) for evidence and limitations.

The complete backend suite assumes writable Home Assistant `/data` storage. Outside the add-on container, provision an isolated writable equivalent before running the full suite; do not change production storage paths just to run tests. Live MCP tests remain opt-in via `MCP_SERVER_URL`.

The end-to-end SSE regression executes a registry-validated, read-only fixture tool, verifies its observation and call ID on the next HTTP request, and derives the final fixture answer from that observation. It also tests an empty second completion: the run fails and no completed plan is saved. These fixture lights are not observations from a real home.

From `ai-orchestrator/dashboard`, run `node --test tests/runOutcome.test.mjs` for result classification and `npm ci --include=dev && npm run build` for production bundle validation.

Investigation evidence, source references, and the distinction between verified defects and the unconfirmed live trigger are recorded in [EMPTY_RESULTS_0_13_10.md](../../../EMPTY_RESULTS_0_13_10.md).

## Running Smoke Tests

### Prerequisites

```bash
# Install test dependencies
pip install pytest pytest-asyncio
```

### Run All Smoke Tests

```bash
# From backend directory
cd backend
pytest -m smoke -v
```

### Run Specific Test Files

```bash
# API tests only
pytest tests/test_api_smoke.py -v

# MCP server tests only
pytest tests/test_mcp_smoke.py -v

# Agent tests only
pytest tests/test_agent_smoke.py -v

# Configuration tests only
pytest tests/test_config_smoke.py -v
```

### Run with Coverage

```bash
pytest -m smoke --cov=. --cov-report=html
```

## Test Categories

### 🟢 Smoke Tests (`-m smoke`)
Quick, essential functionality validation:
- API endpoints respond correctly
- MCP tools are registered and validate input
- Agents initialize properly
- Configuration parses correctly

**Runtime**: ~5-10 seconds

### 🔵 Unit Tests (`-m unit`)
Isolated component tests (future):
- Individual function testing
- Mock all external dependencies

### 🟡 Integration Tests (`-m integration`)
End-to-end tests requiring external services (future):
- Real Ollama instance
- Real Home Assistant instance

## Test Structure

```
backend/tests/
├── __init__.py
├── conftest.py              # Shared fixtures and configuration
├── test_api_smoke.py        # FastAPI endpoint tests
├── test_mcp_smoke.py        # MCP server tests
├── test_agent_smoke.py      # Agent framework tests
└── test_config_smoke.py     # Configuration validation tests
```

## Fixtures (conftest.py)

- `mock_ha_client`: Mocked Home Assistant WebSocket client
- `mock_ollama_client`: Mocked Ollama client
- `temp_data_dir`: Temporary directory for decision logs

## Expected Output

```
============================= test session starts =============================
collected 25 items / 0 deselected / 25 selected

tests/test_api_smoke.py::TestAPISmoke::test_health_endpoint PASSED        [  4%]
tests/test_api_smoke.py::TestAPISmoke::test_agents_endpoint PASSED        [  8%]
tests/test_api_smoke.py::TestAPISmoke::test_decisions_endpoint PASSED     [ 12%]
tests/test_api_smoke.py::TestAPISmoke::test_config_endpoint PASSED        [ 16%]
tests/test_mcp_smoke.py::TestMCPServerSmoke::test_mcp_server_initialization PASSED [ 20%]
tests/test_mcp_smoke.py::TestMCPServerSmoke::test_tool_schemas_available PASSED [ 24%]
tests/test_mcp_smoke.py::TestMCPServerSmoke::test_set_temperature_validation_success PASSED [ 28%]
tests/test_mcp_smoke.py::TestMCPServerSmoke::test_set_temperature_validation_bounds PASSED [ 32%]
tests/test_mcp_smoke.py::TestMCPServerSmoke::test_execute_tool_dry_run PASSED [ 36%]
tests/test_mcp_smoke.py::TestMCPServerSmoke::test_temperature_change_limit PASSED [ 40%]
tests/test_agent_smoke.py::TestAgentSmoke::test_heating_agent_initialization PASSED [ 44%]
tests/test_agent_smoke.py::TestAgentSmoke::test_agent_gather_context PASSED [ 48%]
tests/test_agent_smoke.py::TestAgentSmoke::test_agent_decide PASSED       [ 52%]
tests/test_agent_smoke.py::TestAgentSmoke::test_skills_loading PASSED     [ 56%]
tests/test_config_smoke.py::TestConfigSmoke::test_environment_variables_set PASSED [ 60%]
tests/test_config_smoke.py::TestConfigSmoke::test_dry_run_mode_parsing PASSED [ 64%]
tests/test_config_smoke.py::TestConfigSmoke::test_decision_interval_parsing PASSED [ 68%]

======================== 25 passed in 2.45s ================================
```

## CI/CD Integration

Add to GitHub Actions workflow:

```yaml
name: Tests
on: [push, pull_request]

jobs:
  smoke-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          cd backend
          pip install -r requirements.txt
      - name: Run smoke tests
        run: |
          cd backend
          pytest -m smoke -v
```

## Troubleshooting

### Import Errors
```bash
# Ensure backend directory is in Python path
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
```

### Async Test Warnings
```bash
# Install pytest-asyncio
pip install pytest-asyncio
```

### Missing test dependencies
```bash
# Update requirements.txt to include
pytest
pytest-asyncio
```
