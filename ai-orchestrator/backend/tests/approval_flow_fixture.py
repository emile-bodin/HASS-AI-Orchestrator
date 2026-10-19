"""Loopback-only real API/UI fixture. No HA, network provider or agent loops.
Run: python -I tests/approval_flow_fixture.py (127.0.0.1:19014).
All /data paths and SQLite connections are redirected into a scratch temp dir.
"""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEMP = tempfile.TemporaryDirectory(prefix='approval-flow-fixture-')
DATA = Path(TEMP.name)
original_path_init = Path.__init__
def sandbox_path(self, *args, **kwargs):
    if args and (str(args[0]) == '/data' or str(args[0]).startswith('/data/')):
        args = (str(DATA) + str(args[0])[5:], *args[1:])
    original_path_init(self, *args, **kwargs)
Path.__init__ = sandbox_path
original_connect = sqlite3.connect
def sandbox_connect(path, *args, **kwargs):
    if str(path) == '/data' or str(path).startswith('/data/'):
        path = str(DATA) + str(path)[5:]
    return original_connect(path, *args, **kwargs)
sqlite3.connect = sandbox_connect
original_exists = os.path.exists
os.path.exists = lambda path: original_exists(str(DATA) + str(path)[5:]) if str(path) == '/data' or str(path).startswith('/data/') else original_exists(path)

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
import uvicorn
import main
import agents.deep_reasoning_agent as dra
from agent_lifecycle import AgentLifecycle
from approval_queue import ApprovalQueue
from mcp_server import MCPServer
from plan_executor import PlanStore
from reasoning_harness import LLMResponse, ToolCall

os.environ['ALLOWED_DOMAINS'] = 'light,lock,climate'
os.environ['BLOCKED_DOMAINS'] = 'homeassistant'
class FakeHA:
    connected = True
    def __init__(self):
        self.calls = []
    async def call_service(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get('entity_id') == 'light.fixture_fail':
            raise RuntimeError('Fixture tool refused the action')
        await asyncio.sleep(0.2)
        return {'fixture_accepted': True}
    async def get_climate_state(self, entity_id):
        return {'target_temperature': 20}

class ScriptedPlanner:
    name = 'fixture-script (no model)'
    model = 'no-model'
    def __init__(self):
        self.calls = 0
    async def chat(self, messages, tools):
        self.calls += 1
        if any(m.get('role') == 'tool' for m in messages):
            return LLMResponse(content='Actions recorded for exact review. Tool acceptance is not physical verification.')
        goal = ' '.join(str(m.get('content', '')) for m in messages if m.get('role') == 'user').lower()
        if 'read-only' in goal:
            return LLMResponse(content='Fixture read-only response; no changes requested.')
        if 'high' in goal:
            calls = [ToolCall('one', 'lock_door', {'entity_id': 'lock.front'}), ToolCall('two', 'turn_on_light', {'entity_id': 'light.fixture'})]
        else:
            entity = 'light.fixture_fail' if 'fail' in goal else 'light.fixture'
            calls = [ToolCall('one', 'turn_on_light', {'entity_id': entity})]
        return LLMResponse(content='Prepare fixture actions.', tool_calls=calls)

ha = FakeHA()
queue = ApprovalQueue(str(DATA/'approvals.db'), timeout_default=3600)
mcp = MCPServer(ha, approval_queue=queue, dry_run=True)
planner = ScriptedPlanner()
dra.OllamaToolBackend = lambda **kw: planner
reasoner = dra.DeepReasoningAgent(local_mcp=mcp, provider='ollama', plan_store=PlanStore(str(DATA/'plans.db')))
async def states():
    return [{'entity_id': 'light.fixture'}, {'entity_id': 'light.fixture_fail'}, {'entity_id': 'lock.front'}]
lifecycle = AgentLifecycle({'fixture_specialist': SimpleNamespace(paused=False, entities=['light.fixture'], decision_interval=0)}, DATA/'agents.yaml', None, states, True)
lifecycle.write({'agents': [{'id': 'fixture_specialist', 'paused': False, 'entities': ['light.fixture']}]})
mcp.agent_lifecycle = lifecycle
queue.executor = mcp.execute_approval
main.deep_reasoner, main.approval_queue = reasoner, queue
main.mcp_server, main.ha_client = mcp, ha

@asynccontextmanager
async def setup(app):
    await mcp.queue_action('fixture_specialist', 'turn_on_light', {'entity_id': 'light.fixture'})
    yield
app = FastAPI(lifespan=setup)
# Reuse the actual route implementations; never start production lifespan.
for route in main.app.routes:
    path = getattr(route, 'path', '')
    if path.startswith('/api/reasoning') or path.startswith('/api/approvals'):
        app.router.routes.append(route)

@app.get('/api/fixture/state')
async def fixture_state():
    return {'data_dir': str(DATA), 'model_calls': planner.calls, 'fake_ha_calls': ha.calls,
            'simulation': mcp.dry_run, 'plans': [p.to_dict() for p in reasoner.plan_store.list()],
            'approvals': [{'id': r.id, 'status': r.status, 'result': r.execution_result} for r in queue.list_requests('all')]}
@app.post('/api/fixture/expire/{request_id}')
async def expire(request_id: str):
    with sqlite3.connect(queue.db_path) as conn:
        conn.execute("UPDATE approvals SET timestamp='2000-01-01T00:00:00' WHERE id=? AND status='pending'", (request_id,))
    return {'fixture_expired': request_id}
@app.post('/api/fixture/new-approval')
async def new_approval():
    req = await mcp.queue_action('fixture_specialist', 'turn_on_light', {'entity_id': 'light.fixture'})
    return {'request_id': req.id}
@app.post('/api/fixture/simulation/{enabled}')
async def simulation(enabled: bool):
    mcp.dry_run = enabled
    return {'dry_run': mcp.dry_run}
@app.get('/api/config')
async def config():
    return {'dry_run_mode': mcp.dry_run, 'version': '0.13.14-fixture'}
@app.get('/api/health')
async def health():
    return {'version': '0.13.14-fixture', 'home_assistant': {'connected': False}}
@app.get('/api/agents')
@app.get('/api/decisions')
@app.get('/api/stats/daily')
@app.get('/api/factory/suggestions')
async def empty():
    return []
@app.get('/api/stats/performance')
async def performance():
    return {}
@app.websocket('/ws')
async def socket(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            await ws.receive_text()
    except Exception:
        pass
app.mount('/', StaticFiles(directory=ROOT.parent/'dashboard'/'dist', html=True), name='fixture-dashboard')
if __name__ == '__main__':
    print(f'FIXTURE DATA ONLY: {DATA}', flush=True)
    uvicorn.run(app, host='127.0.0.1', port=19014)
