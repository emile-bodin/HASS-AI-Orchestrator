"""Local-only browser fixture: never imports main or contacts HA/models.

Run from backend with an isolated interpreter:
  python -I tests/agent_management_fixture.py
Serves the real factory/lifecycle API on 127.0.0.1:18999, data in a temporary
scratch directory. Pair with dashboard/tests/agent-management.fixture.html.
"""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from agent_lifecycle import AgentLifecycle
from factory_router import router

os.environ['ALLOWED_DOMAINS'] = 'light,sensor,switch'
os.environ['BLOCKED_DOMAINS'] = 'switch'

async def states():
    return [dict(entity_id=e, state='on', attributes={'friendly_name': n}) for e, n in [
        ('light.kitchen', 'Kitchen ceiling'), ('light.hall', 'Hall lamp'),
        ('sensor.temperature', 'Indoor temperature'), ('switch.restricted', 'Restricted plug')]]

class FixtureAgent:
    def __init__(self, cfg):
        self.agent_id = cfg['id']
        self.name = cfg['name']
        self.instruction = cfg['instruction']
        self.entities = cfg['entities']
        self.decision_interval = cfg.get('decision_interval', 120)
        self.runs = 0
    async def run_decision_loop(self):
        self.runs += 1
        await asyncio.Event().wait()

class Architect:
    async def suggest_agents(self):
        return []
    async def generate_config(self, prompt):
        return dict(id='fixture_agent', name='Fixture agent', instruction=prompt,
                    entities=['light.kitchen'], decision_interval=120)

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=['http://127.0.0.1:13000'], allow_methods=['*'], allow_headers=['*'])
# Respect TMPDIR (the Hermes scratch directory), never production /config or /data.
temporary = tempfile.TemporaryDirectory(prefix='agent-management-fixture-')
service = AgentLifecycle({}, Path(temporary.name)/'agents.yaml', FixtureAgent, states, True)
app.state.agent_lifecycle = service
app.state.architect = Architect()
app.include_router(router)

@app.get('/api/agents')
async def agents():
    return [dict(agent_id=k, name=a.name, instruction=a.instruction, entities=a.entities,
                 model='fixture-no-model', status=a.status, paused=a.paused,
                 decision_interval=a.decision_interval, legacy_enabled=service.legacy_enabled,
                 last_decision=None) for k, a in service.agents.items()]

@app.get('/api/decisions')
async def decisions():
    return []

@app.get('/api/fixture/state')
async def fixture_state():
    return dict(persisted=service.read(), tasks=len(service.tasks),
                agents={k: dict(paused=a.paused, runs=a.runs, entities=a.entities) for k, a in service.agents.items()})

if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=18999)
