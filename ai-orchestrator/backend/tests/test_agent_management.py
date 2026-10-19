"""Offline lifecycle regressions: no Home Assistant or model requests."""
import asyncio
from types import SimpleNamespace
import yaml
import pytest


@pytest.mark.asyncio
async def test_new_agent_hotloads_paused_and_resume_is_idempotent(tmp_path):
    from agent_lifecycle import AgentLifecycle
    class Agent:
        def __init__(self, cfg):
            self.agent_id = cfg['id']
            self.entities = cfg['entities']
            self.decision_interval = 120
            self.runs = 0
        async def run_decision_loop(self):
            self.runs += 1
            await asyncio.Event().wait()
    async def states():
        return [{'entity_id': 'light.kitchen', 'attributes': {'friendly_name': 'Kitchen'}}]
    service = AgentLifecycle({}, tmp_path / 'agents.yaml', Agent, states, True)
    cfg = dict(id='kitchen', name='Kitchen', instruction='Lights', entities=['light.kitchen'])
    await service.save(cfg)
    agent = service.agents['kitchen']
    assert agent.paused and agent.status == 'paused' and not service.tasks
    assert yaml.safe_load(service.path.read_text())['agents'][0]['paused'] is True
    await service.resume('kitchen')
    await service.resume('kitchen')
    await asyncio.sleep(0)
    assert agent.runs == 1 and len(service.tasks) == 1
    await service.pause('kitchen')
    assert not service.tasks and agent.paused
    assert yaml.safe_load(service.path.read_text())['agents'][0]['paused'] is True


@pytest.mark.asyncio
async def test_policy_blocks_forged_empty_and_disallowed_selection(tmp_path, monkeypatch):
    from agent_lifecycle import AgentLifecycle
    monkeypatch.setenv('ALLOWED_DOMAINS', 'light,switch')
    monkeypatch.setenv('BLOCKED_DOMAINS', 'switch')
    async def states():
        return [{'entity_id': e} for e in ['light.real', 'switch.blocked', 'sensor.denied']]
    service = AgentLifecycle({}, tmp_path / 'agents.yaml', lambda c: SimpleNamespace(**c), states, False)
    for entities in [[], ['light.forged'], ['switch.blocked'], ['sensor.denied']]:
        with pytest.raises(ValueError):
            await service.save(dict(id='a', name='A', instruction='A', entities=entities))
    assert not service.path.exists()
    rows = await service.entity_catalog()
    assert [r['entity_id'] for r in rows if r['allowed']] == ['light.real']

@pytest.mark.asyncio
async def test_factory_api_hotload_pause_edit_delete(tmp_path):
    import httpx
    from fastapi import FastAPI
    from factory_router import router
    from agent_lifecycle import AgentLifecycle
    async def states():
        return [{'entity_id': 'light.real'}]
    class Agent:
        def __init__(self, cfg):
            self.entities = cfg['entities']
            self.decision_interval = 120
        async def run_decision_loop(self):
            await asyncio.Event().wait()
    app = FastAPI()
    service = AgentLifecycle({}, tmp_path / 'agents.yaml', Agent, states, True)
    app.state.agent_lifecycle = service
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        cfg = dict(id='test', name='Test', instruction='Test', entities=['light.real'])
        response = await client.post('/api/factory/save', json={'config': cfg})
        assert response.status_code == 200 and response.json()['paused'] is True
        assert (await client.get('/api/factory/entities')).json()['entities'][0]['entity_id'] == 'light.real'
        assert (await client.post('/api/factory/agents/test/resume')).status_code == 200
        assert (await client.patch('/api/factory/agents/test', json={'name': 'Renamed'})).status_code == 200
        assert len(service.tasks) == 1
        assert (await client.post('/api/factory/agents/test/pause')).json()['paused'] is True
        assert (await client.delete('/api/factory/agents/test')).status_code == 200
        assert not service.tasks and not service.agents
        assert yaml.safe_load(service.path.read_text())['agents'] == []


@pytest.mark.asyncio
async def test_restart_compatibility_legacy_off_and_rollback(tmp_path):
    from agent_lifecycle import AgentLifecycle
    async def states():
        return [{'entity_id': 'light.real'}]
    class Agent:
        def __init__(self, cfg):
            self.entities = cfg['entities']
            self.decision_interval = 120
        async def run_decision_loop(self):
            await asyncio.Event().wait()
    cfg = dict(id='old', name='Old', instruction='Old', entities=['light.real'])
    path = tmp_path / 'agents.yaml'
    path.write_text(yaml.safe_dump({'agents': [cfg, {**cfg, 'id': 'paused', 'paused': True}]}))
    service = AgentLifecycle({}, path, Agent, states, False)
    service.load()
    assert not service.agents['old'].paused and service.agents['old'].status == 'ready'
    assert service.agents['paused'].paused
    with pytest.raises(ValueError, match='disabled'):
        await service.resume('paused')
    assert service.agents['paused'].paused
    before = path.read_text()
    original = service.agents['old']
    service.legacy_enabled = True
    service.spawn = lambda *_: (_ for _ in ()).throw(RuntimeError('schedule failed'))
    with pytest.raises(RuntimeError):
        await service.save({**cfg, 'name': 'Changed'}, updating=True)
    assert path.read_text() == before
    assert service.agents['old'] is original


@pytest.mark.asyncio
async def test_mcp_managed_gate_blocks_paused_and_out_of_scope(tmp_path):
    from agent_lifecycle import AgentLifecycle
    from mcp_server import MCPServer
    async def states():
        return [{'entity_id': 'light.real'}]
    service = AgentLifecycle({'a': SimpleNamespace(paused=True, entities=['light.real'])}, tmp_path/'agents.yaml', None, states, True)
    mcp = object.__new__(MCPServer)
    mcp.agent_lifecycle = service
    # Gate must precede even normal validation/tool lookup.
    result = await mcp.execute_tool('turn_on_light', {'entity_id': 'light.real'}, 'a')
    assert result['error_code'] == 'agent_inactive'
    service.agents['a'].paused = False
    result = await mcp.execute_tool('turn_on_light', {'entity_id': 'light.other'}, 'a')
    assert result['error_code'] == 'agent_inactive'

@pytest.mark.asyncio
async def test_paused_base_loop_never_gathers_or_decides():
    from agents.universal_agent import UniversalAgent
    agent = object.__new__(UniversalAgent)
    agent.paused = True
    agent.status = 'paused'
    async def forbidden():
        raise AssertionError('paused agent gathered context')
    agent.gather_context = forbidden
    await asyncio.wait_for(agent.run_decision_loop(), timeout=0.05)
    assert agent.status == 'paused'


@pytest.mark.asyncio
async def test_orchestrator_drops_paused_and_stale_decisions():
    from orchestrator import Orchestrator
    orchestrator = object.__new__(Orchestrator)
    orchestrator.agents = {'a': SimpleNamespace(paused=True)}
    orchestrator.mcp_server = SimpleNamespace(agent_lifecycle=SimpleNamespace(generations={'a': 1}))
    orchestrator.progress_ledger = {'a': {'decision': {'agent_id': 'a', 'actions': [{'tool': 'log', 'parameters': {}}]}}}
    state = await orchestrator.aggregate_decisions({'decisions': []})
    assert state['decisions'] == []
    state = {'approved_actions': [{'_source_agent': 'a', '_generation': 0, 'tool': 'log', 'parameters': {}}]}
    assert (await orchestrator.execute_approved_actions(state))['execution_results'] == []

@pytest.mark.asyncio
async def test_architect_filters_actual_domain_policy(monkeypatch):
    from agents.architect_agent import ArchitectAgent
    monkeypatch.setenv('ALLOWED_DOMAINS', 'light,switch')
    monkeypatch.setenv('BLOCKED_DOMAINS', 'switch')
    async def states():
        return [{'entity_id': e} for e in ['light.kitchen_one', 'light.kitchen_two', 'switch.kitchen_bad', 'sensor.kitchen_bad']]
    architect = ArchitectAgent(SimpleNamespace(connected=True, get_states=states))
    discovered = await architect.discover_entities_from_instruction('kitchen')
    assert discovered == ['light.kitchen_one', 'light.kitchen_two']
    suggestions = await architect.suggest_agents()
    assert suggestions
    assert all(e.startswith('light.') for s in suggestions for e in s['blueprint']['entities'])

@pytest.mark.asyncio
async def test_pause_drains_inflight_tool_and_prevents_next_action(tmp_path):
    from agent_lifecycle import AgentLifecycle
    from mcp_server import MCPServer
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def states():
        return [{'entity_id': 'light.real'}]
    async def handler(params):
        entered.set()
        await release.wait()
        calls.append(params)
        return {'executed': True}
    agent = SimpleNamespace(paused=False, entities=['light.real'], status='ready')
    service = AgentLifecycle({'a': agent}, tmp_path/'agents.yaml', None, states, True)
    service.write({'agents': [dict(id='a', paused=False)]})
    mcp = object.__new__(MCPServer)
    mcp.agent_lifecycle = service
    mcp._execute_tool = lambda *args: handler(args[1])
    running = asyncio.create_task(mcp.execute_tool('turn_on_light', {'entity_id': 'light.real'}, 'a'))
    await entered.wait()
    pausing = asyncio.create_task(service.pause('a'))
    await asyncio.sleep(0)
    assert agent.paused and not pausing.done()
    release.set()
    await running
    await pausing
    assert len(calls) == 1
    result = await mcp.execute_tool('turn_on_light', {'entity_id': 'light.real'}, 'a')
    assert not result['executed'] and len(calls) == 1


@pytest.mark.asyncio
async def test_managed_service_requires_explicit_targets(tmp_path):
    from agent_lifecycle import AgentLifecycle
    from mcp_server import MCPServer
    async def states():
        return [{'entity_id': 'light.real'}]
    service = AgentLifecycle({'a': SimpleNamespace(paused=False, entities=['light.real'])}, tmp_path/'agents.yaml', None, states, True)
    mcp = object.__new__(MCPServer)
    mcp.agent_lifecycle = service
    mcp._execute_tool = lambda *args: asyncio.sleep(0, result={'executed': True})
    result = await mcp.execute_tool('call_ha_service', {'domain': 'light', 'service': 'turn_off'}, 'a')
    assert not result['executed']


@pytest.mark.asyncio
async def test_resume_failure_restores_paused_persistence(tmp_path):
    from agent_lifecycle import AgentLifecycle
    async def states():
        return [{'entity_id': 'light.real'}]
    class Agent:
        def __init__(self, cfg):
            self.entities = cfg['entities']
            self.decision_interval = 120
        async def run_decision_loop(self):
            await asyncio.Event().wait()
    service = AgentLifecycle({}, tmp_path/'agents.yaml', Agent, states, True)
    await service.save(dict(id='a', name='A', instruction='A', entities=['light.real']))
    before = service.path.read_text()
    service.spawn = lambda *_: (_ for _ in ()).throw(RuntimeError('no scheduler'))
    with pytest.raises(RuntimeError):
        await service.resume('a')
    assert service.agents['a'].paused and service.path.read_text() == before

@pytest.mark.asyncio
async def test_delegated_work_cancelled_and_not_replayed_after_resume(tmp_path):
    from agent_lifecycle import AgentLifecycle
    from orchestrator import Orchestrator
    entered = asyncio.Event()
    events = []
    async def states():
        return [{'entity_id': 'light.real'}]
    class Agent:
        def __init__(self, cfg):
            self.entities = cfg['entities']
            self.decision_interval = 120
        async def run_decision_loop(self):
            await asyncio.Event().wait()
        async def receive_task(self, task):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                events.append('cancelled')
    service = AgentLifecycle({}, tmp_path/'agents.yaml', Agent, states, True)
    await service.save(dict(id='a', name='A', instruction='A', entities=['light.real']))
    await service.resume('a')
    orchestrator = object.__new__(Orchestrator)
    orchestrator.agents = service.agents
    orchestrator.mcp_server = SimpleNamespace(agent_lifecycle=service)
    orchestrator.task_ledger = []
    orchestrator.progress_ledger = {}
    service.orchestrator = orchestrator
    await orchestrator.distribute_tasks({'tasks': [dict(agent_id='a', task_id='one')]})
    await entered.wait()
    await service.pause('a')
    assert events == ['cancelled']
    await service.resume('a')
    await asyncio.sleep(0)
    assert events == ['cancelled']
    await service.pause('a')

@pytest.mark.asyncio
async def test_edit_retains_restricted_entries_but_run_refuses_them(tmp_path, monkeypatch):
    from agent_lifecycle import AgentLifecycle
    async def states():
        return [{'entity_id': 'light.real'}, {'entity_id': 'switch.old'}]
    cfg = dict(id='a', name='A', instruction='A', entities=['light.real', 'switch.old'], paused=True)
    path = tmp_path/'agents.yaml'
    path.write_text(yaml.safe_dump({'agents': [cfg]}))
    service = AgentLifecycle({}, path, lambda c: SimpleNamespace(entities=c['entities'], decision_interval=120), states, True)
    service.load()
    monkeypatch.setenv('ALLOWED_DOMAINS', 'light,switch')
    monkeypatch.setenv('BLOCKED_DOMAINS', 'switch')
    await service.save(dict(id='a', name='Renamed'), updating=True)
    assert service.agents['a'].entities == cfg['entities']
    with pytest.raises(ValueError):
        await service.resume('a')
    await service.save(dict(id='a', entities=['light.real']), updating=True)
    with pytest.raises(ValueError):
        await service.save(dict(id='a', entities=['light.real', 'switch.old']), updating=True)


@pytest.mark.asyncio
async def test_failed_persistence_keeps_original_runtime(tmp_path, monkeypatch):
    from agent_lifecycle import AgentLifecycle
    async def states():
        return [{'entity_id': 'light.real'}]
    service = AgentLifecycle({}, tmp_path/'agents.yaml', lambda c: SimpleNamespace(entities=c['entities'], decision_interval=120), states, False)
    await service.save(dict(id='a', name='A', instruction='A', entities=['light.real']))
    original = service.agents['a']
    before = service.path.read_text()
    import agent_lifecycle
    monkeypatch.setattr(agent_lifecycle.os, 'replace', lambda *_: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):
        await service.save(dict(id='a', name='Changed'), updating=True)
    assert service.agents['a'] is original and service.path.read_text() == before
    assert list(tmp_path.iterdir()) == [service.path]


@pytest.mark.asyncio
async def test_concurrent_resume_starts_one_task(tmp_path):
    from agent_lifecycle import AgentLifecycle
    async def states():
        return [{'entity_id': 'light.real'}]
    class Agent:
        def __init__(self, cfg):
            self.entities = cfg['entities']
            self.decision_interval = 120
            self.runs = 0
        async def run_decision_loop(self):
            self.runs += 1
            await asyncio.Event().wait()
    service = AgentLifecycle({}, tmp_path/'agents.yaml', Agent, states, True)
    await service.save(dict(id='a', name='A', instruction='A', entities=['light.real']))
    await asyncio.gather(*(service.resume('a') for _ in range(10)))
    await asyncio.sleep(0)
    assert len(service.tasks) == 1 and service.agents['a'].runs == 1
    await service.pause('a')


@pytest.mark.asyncio
async def test_execution_rejects_work_that_crossed_a_pause_generation(tmp_path):
    from agent_lifecycle import AgentLifecycle
    from mcp_server import MCPServer
    entered, release = asyncio.Event(), asyncio.Event()
    async def states():
        entered.set()
        await release.wait()
        return [{'entity_id': 'light.real'}]
    service = AgentLifecycle({'a': SimpleNamespace(paused=False, entities=['light.real'])}, tmp_path/'agents.yaml', None, states, True)
    mcp = object.__new__(MCPServer)
    mcp.agent_lifecycle = service
    mcp._execute_tool = lambda *args: asyncio.sleep(0, result={'executed': True})
    work = asyncio.create_task(mcp.execute_tool('turn_on_light', {'entity_id': 'light.real'}, 'a'))
    await entered.wait()
    # Simulate a pause/resume boundary while HA inventory lookup was pending.
    service.generations['a'] = 1
    release.set()
    assert not (await work)['executed']

@pytest.mark.asyncio
async def test_api_forged_entity_and_legacy_off_are_fail_closed(tmp_path, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from factory_router import router
    from agent_lifecycle import AgentLifecycle
    monkeypatch.setenv('ALLOWED_DOMAINS', 'light,switch')
    monkeypatch.setenv('BLOCKED_DOMAINS', 'switch')
    async def states():
        return [{'entity_id': 'light.real'}, {'entity_id': 'switch.restricted'}]
    service = AgentLifecycle({}, tmp_path/'agents.yaml', lambda c: SimpleNamespace(entities=c['entities'], decision_interval=120), states, False)
    app = FastAPI()
    app.state.agent_lifecycle = service
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        cfg = dict(id='a', name='A', instruction='A', entities=['light.fake'])
        for ids in [[], ['light.fake'], ['switch.restricted']]:
            result = await client.post('/api/factory/save', json={'config': {**cfg, 'entities': ids}})
            assert result.status_code == 400
        cfg['entities'] = ['light.real']
        result = await client.post('/api/factory/save', json={'config': {**cfg, 'paused': False}})
        assert result.status_code == 200 and result.json()['paused']
        before = service.path.read_text()
        result = await client.post('/api/factory/agents/a/resume')
        assert result.status_code == 400 and 'disabled' in result.json()['detail']
        assert service.path.read_text() == before and not service.tasks
        result = await client.patch('/api/factory/agents/a', json={'entities': ['light.fake']})
        assert result.status_code == 400 and service.path.read_text() == before
        assert (await client.post('/api/factory/agents/missing/pause')).status_code == 404


@pytest.mark.asyncio
async def test_new_agent_factory_failure_does_not_persist_or_register(tmp_path):
    from agent_lifecycle import AgentLifecycle
    async def states():
        return [{'entity_id': 'light.real'}]
    def broken(cfg):
        raise RuntimeError('construction failed')
    service = AgentLifecycle({}, tmp_path/'agents.yaml', broken, states, True)
    with pytest.raises(RuntimeError):
        await service.save(dict(id='a', name='A', instruction='A', entities=['light.real']))
    assert not service.path.exists() and not service.agents and not service.tasks


@pytest.mark.asyncio
async def test_real_universal_agent_hotloads_without_deciding(tmp_path):
    from agent_lifecycle import AgentLifecycle
    from agents.universal_agent import UniversalAgent
    async def states():
        return [{'entity_id': 'light.real'}]
    mcp = SimpleNamespace(dry_run=True)
    def build(cfg):
        return UniversalAgent(agent_id=cfg['id'], name=cfg['name'], instruction=cfg['instruction'],
                              entities=cfg['entities'], mcp_server=mcp, ha_client=None)
    service = AgentLifecycle({}, tmp_path/'agents.yaml', build, states, True)
    mcp.agent_lifecycle = service
    result = await service.save(dict(id='a', name='A', instruction='A', entities=['light.real']))
    assert result['paused'] and isinstance(service.agents['a'], UniversalAgent)
    await asyncio.sleep(0)
    assert service.agents['a'].status == 'paused' and not service.tasks
    restarted = AgentLifecycle({}, service.path, build, states, True)
    restarted.load()
    restarted.start('a')
    assert restarted.agents['a'].paused and not restarted.tasks

@pytest.mark.asyncio
async def test_entity_targets_in_nested_lists_cannot_escape_scope(tmp_path):
    from agent_lifecycle import AgentLifecycle
    async def states():
        return [{'entity_id': 'light.real'}]
    service = AgentLifecycle({'a': SimpleNamespace(paused=False, entities=['light.real'])}, tmp_path/'agents.yaml', None, states, True)
    error = await service.check_execution('a', {'entity_id': 'light.real', 'service_data': {'targets': [{'entity_id': 'light.other'}]}}, 'call_ha_service')
    assert error is not None


@pytest.mark.asyncio
async def test_agent_id_must_be_string_without_coercion(tmp_path):
    from agent_lifecycle import AgentLifecycle
    async def states():
        return [{'entity_id': 'light.real'}]
    service = AgentLifecycle({}, tmp_path/'agents.yaml', lambda c: SimpleNamespace(entities=c['entities'], decision_interval=120), states, False)
    with pytest.raises(ValueError):
        await service.save(dict(id=123, name='A', instruction='A', entities=['light.real']))
    assert not service.path.exists()

@pytest.mark.asyncio
async def test_failed_disk_rollback_leaves_runtime_fail_closed(tmp_path):
    from agent_lifecycle import AgentLifecycle
    async def states():
        return [{'entity_id': 'light.real'}]
    class Agent:
        def __init__(self, cfg):
            self.entities = cfg['entities']
            self.decision_interval = 120
        async def run_decision_loop(self):
            await asyncio.Event().wait()
    service = AgentLifecycle({}, tmp_path/'agents.yaml', Agent, states, True)
    cfg = dict(id='a', name='A', instruction='A', entities=['light.real'])
    await service.save(cfg)
    await service.resume('a')
    original = service.agents['a']
    write = service.write
    attempts = 0
    def fail_rollback(data):
        nonlocal attempts
        attempts += 1
        if attempts > 1:
            raise OSError('rollback storage unavailable')
        write(data)
    service.write = fail_rollback
    service.spawn = lambda *_: (_ for _ in ()).throw(RuntimeError('schedule unavailable'))
    with pytest.raises(Exception):
        await service.save({**cfg, 'name': 'Changed'}, updating=True)
    assert service.agents['a'] is original
    assert original.paused and not service.tasks
