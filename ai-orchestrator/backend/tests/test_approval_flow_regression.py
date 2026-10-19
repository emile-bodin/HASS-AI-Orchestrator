"""Model/device-free approval regression tests; only fake tools touch effects."""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
import pytest
from approval_queue import ApprovalQueue
from plan_executor import _result_ok
from triggers import TriggerRegistry, TriggerSpec, TriggerStore

@pytest.mark.asyncio
async def test_notification_observes_persisted_request(tmp_path):
    queue = ApprovalQueue(str(tmp_path/'q.db'))
    seen = []
    async def notified(event):
        request = queue.get_request(event['request_id'])
        seen.append(request.status if request else None)
    queue.register_callback(notified)
    await queue.add_request('lighting', 'turn_on_light', {}, 'high', 'test')
    assert seen == ['pending']

@pytest.mark.asyncio
@pytest.mark.parametrize('saved_mode', ['plan', 'auto'])
@pytest.mark.parametrize('entry', ['manual', 'scheduled'])
async def test_trigger_saved_plan_mode_reaches_callback(tmp_path, saved_mode, entry):
    seen = []
    async def reason(goal, context, *, mode='auto'):
        seen.append(mode)
        return {'plan': {'id': 'p', 'status': 'pending', 'requires_approval': True}}
    registry = TriggerRegistry(TriggerStore(str(tmp_path / 'triggers.db')), reason)
    spec = TriggerSpec(id='t', name='test', type='cron', cron='* * * * *', goal_template='test', mode=saved_mode)
    await registry.add(spec)
    if entry == 'scheduled':
        await registry._evaluate_cron(datetime(2030, 1, 1))
        await asyncio.sleep(0)
    else:
        await registry._fire(registry.store.get('t'), reason='manual')
    assert seen == [saved_mode]

@pytest.mark.parametrize('result', [None, 'accepted', {}, {'status': 'pending_approval'}, {'ok': True, 'status': 'queued_for_approval'}, {'ok': True, 'executed': False}])
def test_non_execution_is_not_success(result):
    assert not _result_ok(result)

@pytest.mark.asyncio
async def test_legacy_human_approval_claims_once_and_persists_result(tmp_path):
    queue = ApprovalQueue(str(tmp_path / 'approvals.db'))
    calls = []
    async def execute(request):
        calls.append(request.action_data)
        await asyncio.sleep(0.01)
        return {'ok': True, 'executed': True}
    queue.executor = execute
    request = await queue.add_request('source', 'turn_on_light', {'entity_id': 'light.test'}, 'high', 'test')
    other_instance = ApprovalQueue(queue.db_path)
    other_instance.executor = execute
    await asyncio.gather(queue.approve(request.id), other_instance.approve(request.id))
    assert calls == [{'entity_id': 'light.test'}]
    assert queue.get_request(request.id).status == 'executed'
    assert ApprovalQueue(queue.db_path).get_request(request.id).execution_result == {'ok': True, 'executed': True}

@pytest.mark.asyncio
async def test_orchestrator_queues_exact_source_action(tmp_path):
    from orchestrator import Orchestrator
    from mcp_server import MCPServer
    from agent_lifecycle import AgentLifecycle
    async def states():
        return [{'entity_id': 'light.test'}]
    service = AgentLifecycle({'a': SimpleNamespace(paused=False, entities=['light.test'])}, tmp_path/'agents.yaml', None, states, True)
    service.write({'agents': [{'id': 'a', 'paused': False}]})
    queue = ApprovalQueue(str(tmp_path/'q.db'))
    server = MCPServer(SimpleNamespace(), approval_queue=queue, dry_run=True)
    server.agent_lifecycle = service
    orchestrator = object.__new__(Orchestrator)
    orchestrator.agents, orchestrator.mcp_server, orchestrator.approval_queue = service.agents, server, queue
    action = {'tool': 'turn_on_light', 'parameters': {'entity_id': 'light.test'}}
    await orchestrator.check_approval_requirements({'decisions': [{'agent_id': 'a', 'actions': [action], 'impact_level': 'high'}]})
    request = queue.get_pending()[0]
    assert request.agent_id == 'a'
    assert request.action_data['parameters'] == action['parameters']
    queue.executor = server.execute_approval
    assert await queue.approve(request.id)
    assert queue.get_request(request.id).status == 'simulated'

@pytest.mark.asyncio
async def test_external_global_simulation_prevents_remote_mutation():
    from agents.deep_reasoning_agent import DeepReasoningAgent
    from plan_executor import ToolClassifier
    from reasoning_harness import ToolExecutionContext
    agent = object.__new__(DeepReasoningAgent)
    called = []
    async def remote(name, args):
        called.append(name)
        return {'ok': True}
    agent.external_mcp = SimpleNamespace(call_tool=remote)
    agent.local_mcp = SimpleNamespace(dry_run=True)
    agent.tool_classifier = ToolClassifier()
    agent.tool_timeout_seconds = 1
    result = await agent._external_executor('turn_on', {}, ToolExecutionContext(approved=True))
    assert not called
    assert result['dry_run'] is True and result['executed'] is False
    await agent._external_executor('get_state', {})
    assert called == ['get_state']

@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['paused', 'edited', 'pause_resume', 'deleted', 'blocked', 'expired', 'rejected', 'restart_executing', 'unknown'])
async def test_legacy_approval_fails_closed_at_boundaries(tmp_path, monkeypatch, boundary):
    import sqlite3
    from agent_lifecycle import AgentLifecycle
    from mcp_server import MCPServer
    called = []
    class HA:
        async def call_service(self, **kwargs):
            called.append(kwargs)
    async def states():
        return [{'entity_id': 'light.test'}]
    service = AgentLifecycle({'a': SimpleNamespace(paused=False, entities=['light.test'], decision_interval=0)}, tmp_path/'agents.yaml', None, states, True)
    service.write({'agents': [{'id': 'a', 'paused': False}]})
    queue = ApprovalQueue(str(tmp_path/'q.db'))
    mcp = MCPServer(HA(), approval_queue=queue, dry_run=False)
    mcp.agent_lifecycle = service
    queue.executor = mcp.execute_approval
    req = await mcp.queue_action('a', 'turn_on_light', {'entity_id': 'light.test'})
    if boundary == 'paused':
        service.agents['a'].paused = True
    elif boundary == 'edited':
        service.write({'agents': [{'id': 'a', 'paused': False, 'name': 'changed'}]})
    elif boundary == 'pause_resume':
        await service.pause('a')
        await service.resume('a')
    elif boundary == 'deleted':
        service.agents.clear()
    elif boundary == 'blocked':
        monkeypatch.setenv('BLOCKED_DOMAINS', 'light')
    elif boundary == 'expired':
        with sqlite3.connect(queue.db_path) as conn:
            conn.execute("UPDATE approvals SET timestamp=? WHERE id=?", ((datetime.now()-timedelta(days=1)).isoformat(), req.id))
    elif boundary == 'rejected':
        await queue.reject(req.id)
    elif boundary == 'restart_executing':
        with sqlite3.connect(queue.db_path) as conn:
            conn.execute("UPDATE approvals SET status='executing' WHERE id=?", (req.id,))
        queue = ApprovalQueue(queue.db_path)
        queue.executor = mcp.execute_approval
    elif boundary == 'unknown':
        with sqlite3.connect(queue.db_path) as conn:
            conn.execute("UPDATE approvals SET action_data='{}' WHERE id=?", (req.id,))
    await queue.approve(req.id)
    assert called == []
    assert queue.get_request(req.id).status in ('failed', 'expired', 'rejected', 'executing')

@pytest.mark.asyncio
async def test_registry_unknown_mutating_result_is_failure():
    from reasoning_harness import ToolRegistry, ToolSemantics
    registry = ToolRegistry()
    async def unknown(name, args):
        return {'unexpected': 'shape'}
    registry.register('fake', [{'type':'function', 'function': {'name':'turn_on', 'parameters':{'type':'object'}}}], unknown,
                      semantics=ToolSemantics(read_only=False))
    result = await registry.call('turn_on', {}, context=SimpleNamespace(approved=True))
    assert result['ok'] is False

@pytest.mark.asyncio
async def test_local_named_tool_rechecks_domain_policy(tmp_path, monkeypatch):
    from mcp_server import MCPServer
    monkeypatch.setenv('BLOCKED_DOMAINS', 'light')
    mcp = MCPServer(SimpleNamespace(), dry_run=True)
    result = await mcp.execute_tool('turn_on_light', {'entity_id': 'light.test'})
    assert result['ok'] is False and result['error_code'] == 'blocked_domain'

@pytest.mark.asyncio
async def test_reject_cannot_overwrite_executing_or_finished_plan(tmp_path):
    from plan_executor import PlanStore
    from tests.test_plan_executor_smoke import _make_plan
    from agents.deep_reasoning_agent import DeepReasoningAgent
    store = PlanStore(str(tmp_path/'p.db'))
    plan = _make_plan(intents=[])
    store.save(plan)
    assert store.claim_for_execution(plan.id) == 'claimed'
    agent = object.__new__(DeepReasoningAgent)
    agent.plan_store = store
    assert await agent.reject_plan(plan.id) is False
    assert store.get(plan.id).status == 'executing'

@pytest.mark.asyncio
async def test_external_approved_mutation_cannot_bypass_local_policy(monkeypatch):
    from agents.deep_reasoning_agent import DeepReasoningAgent
    from mcp_server import MCPServer
    from plan_executor import ToolClassifier
    from reasoning_harness import ToolExecutionContext
    called = []
    async def remote(name, args):
        called.append(args)
        return {'ok': True}
    agent = object.__new__(DeepReasoningAgent)
    agent.external_mcp = SimpleNamespace(call_tool=remote)
    agent.local_mcp = MCPServer(SimpleNamespace(), dry_run=False)
    agent.tool_classifier = ToolClassifier()
    agent.tool_timeout_seconds = 1
    monkeypatch.setenv('BLOCKED_DOMAINS', 'light')
    result = await agent._external_executor('call_service', {'domain':'light', 'service':'turn_on', 'entity_id':'light.test'}, ToolExecutionContext(approved=True))
    assert not called
    assert result['ok'] is False and result['error_code'] == 'blocked_domain'

@pytest.mark.asyncio
async def test_named_tool_configured_high_impact_is_held_during_auto_planning(monkeypatch):
    from mcp_server import MCPServer
    from agents.deep_reasoning_agent import DeepReasoningAgent
    from plan_executor import ToolClassifier
    monkeypatch.setenv('HIGH_IMPACT_SERVICES', 'light.turn_on')
    agent = object.__new__(DeepReasoningAgent)
    agent.local_mcp = MCPServer(SimpleNamespace(), dry_run=True)
    agent.tool_classifier = ToolClassifier()
    agent.tool_timeout_seconds = 1
    assert agent._local_semantics('turn_on_light', {'entity_id':'light.test'}).impact_level == 'high'

@pytest.mark.asyncio
async def test_actual_legacy_api_returns_durable_execution_and_review_payload(tmp_path, monkeypatch):
    import httpx
    from fastapi import FastAPI
    import main
    from mcp_server import MCPServer
    from agent_lifecycle import AgentLifecycle
    calls = []
    class HA:
        async def call_service(self, **kw):
            calls.append(kw)
            await asyncio.sleep(0.01)
            return {'fixture_accepted': True}
    async def states():
        return [{'entity_id': 'light.test'}]
    lifecycle = AgentLifecycle({'a': SimpleNamespace(paused=False, entities=['light.test'])}, tmp_path/'agents.yaml', None, states, True)
    lifecycle.write({'agents': [{'id':'a', 'paused':False}]})
    queue = ApprovalQueue(str(tmp_path/'q.db'))
    server = MCPServer(HA(), approval_queue=queue, dry_run=False)
    server.agent_lifecycle = lifecycle
    queue.executor = server.execute_approval
    monkeypatch.setattr(main, 'approval_queue', queue)
    request = await server.queue_action('a', 'turn_on_light', {'entity_id':'light.test', 'brightness':42})
    app = FastAPI()
    for route in main.app.routes:
        if getattr(route, 'path', '').startswith('/api/approvals'):
            app.router.routes.append(route)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
        before = (await client.get('/api/approvals')).json()[0]
        assert before['action_data']['parameters'] == {'entity_id':'light.test', 'brightness':42}
        # Request body is ignored: only persisted payload can execute.
        responses = await asyncio.gather(*[client.post(f'/api/approvals/{request.id}/approve', json={'entity_id':'light.attacker'}) for _ in range(2)])
        assert sorted(r.status_code for r in responses) == [200, 404]
        assert (await client.get('/api/approvals?status=executed')).json()[0]['status'] == 'executed'
        assert (await client.get('/api/approvals')).json() == []
    assert calls == [{'domain':'light', 'service':'turn_on', 'entity_id':'light.test', 'brightness_pct':42}]

@pytest.mark.asyncio
async def test_plan_exact_replay_concurrent_failure_and_restart(tmp_path):
    from agents.deep_reasoning_agent import DeepReasoningAgent
    from tests.test_plan_executor_smoke import _make_plan, _make_intent
    from plan_executor import PlanStore
    from reasoning_harness import ToolRegistry, ToolSemantics
    store = PlanStore(str(tmp_path/'plans.db'))
    plan = _make_plan(intents=[_make_intent(1, 'change'), _make_intent(2, 'change'), _make_intent(3, 'change')])
    store.save(plan)
    called = []
    async def change(name, args, context=None):
        assert context.approved is True
        called.append(dict(args))
        await asyncio.sleep(0.01)
        return {'ok': len(called) == 1, 'error': 'fixture failed' if len(called) > 1 else None, 'executed': len(called) == 1}
    agent = object.__new__(DeepReasoningAgent)
    agent.plan_store, agent.registry = store, ToolRegistry()
    agent.registry.register('fixture', [{'type':'function', 'function':{'name':'change','parameters':{'type':'object'}}}], change, semantics=ToolSemantics(read_only=False, max_retries=0))
    results = await asyncio.gather(agent.execute_plan(plan.id), agent.execute_plan(plan.id))
    assert {r['status'] for r in results} == {'executed_with_errors', 'already_executing'}
    assert called == [plan.intents[0].arguments, plan.intents[1].arguments]
    assert store.get(plan.id).execution_results[2]['skipped'] is True
    agent.plan_store = PlanStore(store.db_path)
    assert (await agent.execute_plan(plan.id))['status'] == 'already_executed'
    assert len(called) == 2
    second = _make_plan(plan_id='interrupted', intents=plan.intents)
    store.save(second)
    assert store.claim_for_execution(second.id) == 'claimed'
    assert (await agent.execute_plan(second.id))['status'] == 'already_executing'
    assert len(called) == 2

@pytest.mark.asyncio
async def test_plan_simulation_is_durable_and_cannot_replay_as_real(tmp_path):
    from agents.deep_reasoning_agent import DeepReasoningAgent
    from tests.test_plan_executor_smoke import _make_plan, _make_intent
    from plan_executor import PlanStore
    from reasoning_harness import ToolRegistry, ToolSemantics
    store = PlanStore(str(tmp_path/'plans.db'))
    plan = _make_plan(intents=[_make_intent(1, 'change')])
    store.save(plan)
    called = []
    async def simulate(name, args, context=None):
        called.append(args)
        return {'ok':True, 'executed':False, 'dry_run':True}
    agent = object.__new__(DeepReasoningAgent)
    agent.plan_store, agent.registry = store, ToolRegistry()
    agent.registry.register('fixture', [{'type':'function', 'function':{'name':'change','parameters':{'type':'object'}}}], simulate, semantics=ToolSemantics(read_only=False))
    assert (await agent.execute_plan(plan.id))['status'] == 'simulated'
    assert PlanStore(store.db_path).get(plan.id).status == 'simulated'
    assert (await agent.execute_plan(plan.id))['status'] == 'already_executed'
    assert len(called) == 1

@pytest.mark.asyncio
async def test_plan_list_limit_never_hides_pending_review(tmp_path, monkeypatch):
    import main
    from plan_executor import PlanStore
    from tests.test_plan_executor_smoke import _make_plan
    store = PlanStore(str(tmp_path/'plans.db'))
    old = _make_plan(plan_id='old-pending')
    old.timestamp = '2020-01-01T00:00:00+00:00'
    store.save(old)
    recent = _make_plan(plan_id='recent-executed')
    recent.status = 'executed'
    store.save(recent)
    monkeypatch.setattr(main, 'deep_reasoner', SimpleNamespace(plan_store=store))
    assert 'old-pending' in {p['id'] for p in (await main.reasoning_plans(limit=1))['plans']}

@pytest.mark.asyncio
@pytest.mark.parametrize('approved,args,expected_calls', [
    (False, {}, 0),
    (True, {}, 0),
    (True, {'domain':'light','service':'turn_on','entity_id':'light.test'}, 1),
])
async def test_external_mutation_authority_and_policy_contract(approved, args, expected_calls):
    from agents.deep_reasoning_agent import DeepReasoningAgent
    from mcp_server import MCPServer
    from plan_executor import ToolClassifier
    from reasoning_harness import ToolExecutionContext
    calls = []
    async def remote(name, arguments):
        calls.append(arguments)
        return {'ok':True, 'executed':True}
    agent = object.__new__(DeepReasoningAgent)
    agent.external_mcp = SimpleNamespace(call_tool=remote)
    agent.local_mcp = MCPServer(SimpleNamespace(), dry_run=False)
    agent.tool_classifier, agent.tool_timeout_seconds = ToolClassifier(), 1
    result = await agent._external_executor('call_service', args, ToolExecutionContext(approved=approved))
    assert len(calls) == expected_calls
    assert result['ok'] is bool(expected_calls)







