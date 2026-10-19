"""Persisted specialist lifecycle; deliberately retains the legacy loop model.

Pause closes the execution gate, cancels/drains local work and discards unfinished
plans. Already accepted HA requests cannot be undone. Resume starts a fresh cycle.
"""
import asyncio
import copy
import os
import re
import tempfile
from pathlib import Path

import yaml
from mcp_server import DEFAULT_ALLOWED_DOMAINS, DEFAULT_BLOCKED_DOMAINS, get_env_list


def domain_allowed(entity_id):
    domain = entity_id.split('.', 1)[0]
    return (domain in get_env_list('ALLOWED_DOMAINS', DEFAULT_ALLOWED_DOMAINS)
            and domain not in get_env_list('BLOCKED_DOMAINS', DEFAULT_BLOCKED_DOMAINS))


class AgentLifecycle:
    def __init__(self, agents, path, factory, states, legacy_enabled, spawn=None):
        self.agents = agents
        self.path = Path(path)
        self.factory = factory
        self.states = states
        self.legacy_enabled = legacy_enabled
        self.spawn = spawn
        self.tasks = {}
        self.delegated_tasks = {}
        self.lock = asyncio.Lock()
        self.execution_locks = {}
        self.generations = {}
        self.orchestrator = None

    def read(self):
        if not self.path.exists():
            return {'agents': []}
        data = yaml.safe_load(self.path.read_text()) or {}
        data.setdefault('agents', [])
        return data

    def write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.agents-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                yaml.safe_dump(data, stream, sort_keys=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    async def entity_catalog(self):
        rows = await self.states()
        if not isinstance(rows, list):
            raise ValueError('Home Assistant entity inventory unavailable')
        return [dict(entity_id=s['entity_id'],
                     name=s.get('attributes', {}).get('friendly_name', s['entity_id']),
                     domain=s['entity_id'].split('.', 1)[0],
                     allowed=domain_allowed(s['entity_id']), state=s.get('state', 'unknown'))
                for s in rows]

    async def validate_entities(self, selected, previous=()):
        if not isinstance(selected, list) or not selected:
            raise ValueError('Select at least one explicit entity; empty does not mean all')
        rows = {r['entity_id']: r for r in await self.entity_catalog()}
        for eid in selected:
            if not isinstance(eid, str) or not re.fullmatch(r'[a-z0-9_]+\.[a-z0-9_]+', eid):
                raise ValueError('Invalid entity ID')
            if eid in previous:
                continue  # Restricted/unavailable historical entries may be retained or removed.
            if eid not in rows or not rows[eid]['allowed']:
                raise ValueError(f'Entity is missing or restricted: {eid}')
        return list(dict.fromkeys(selected))

    def load(self):
        """Old configs without paused keep their prior enabled intent."""
        for cfg in self.read()['agents']:
            agent = self.factory(cfg)
            agent.paused = cfg.get('paused', False)
            agent.status = 'paused' if agent.paused else 'ready'
            self.agents[cfg['id']] = agent

    def start(self, agent_id):
        agent = self.agents[agent_id]
        task = self.tasks.get(agent_id)
        if (agent.paused or not self.legacy_enabled or agent.decision_interval <= 0
                or (task and not task.done())):
            return
        coro = agent.run_decision_loop()
        try:
            task = (self.spawn(coro, f'legacy-agent-{agent_id}') if self.spawn
                    else asyncio.create_task(coro, name=f'legacy-agent-{agent_id}'))
        except BaseException:
            coro.close()
            raise
        self.tasks[agent_id] = task

    def dispatch(self, agent_id, work):
        agent = self.agents.get(agent_id)
        if not agent or agent.paused or not self.legacy_enabled:
            return
        generation = self.generations.get(agent_id, 0)
        async def run():
            await self.validate_entities(agent.entities)
            if agent.paused or generation != self.generations.get(agent_id, 0):
                return
            await agent.receive_task(work)
        coro = run()
        try:
            task = (self.spawn(coro, f'delegated-agent-{agent_id}') if self.spawn
                    else asyncio.create_task(coro, name=f'delegated-agent-{agent_id}'))
        except BaseException:
            coro.close()
            raise
        tasks = self.delegated_tasks.setdefault(agent_id, set())
        tasks.add(task)
        def finished(done):
            tasks.discard(done)
            if not done.cancelled():
                done.exception()  # consume failures; no automatic replay
        task.add_done_callback(finished)

    async def stop(self, agent_id):
        agent = self.agents.get(agent_id)
        if agent:
            agent.paused = True
        self.generations[agent_id] = self.generations.get(agent_id, 0) + 1
        task = self.tasks.pop(agent_id, None)
        work = list(self.delegated_tasks.pop(agent_id, set()))
        if task:
            work.append(task)
        for pending in work:
            pending.cancel()
        if work:
            await asyncio.gather(*work, return_exceptions=True)
        async with self.execution_locks.setdefault(agent_id, asyncio.Lock()):
            pass  # drain independently dispatched MCP calls
        if self.orchestrator:
            self.orchestrator.progress_ledger.pop(agent_id, None)
        if agent:
            agent.status = 'paused'

    async def save(self, cfg, updating=False):
        async with self.lock:
            data = self.read()
            old_cfg = next((c for c in data['agents'] if c['id'] == cfg.get('id')), None)
            if updating != bool(old_cfg):
                raise ValueError('Agent not found' if updating else 'Agent ID already exists')
            new_cfg = {**(old_cfg or {}), **copy.deepcopy(cfg)}
            if not isinstance(new_cfg.get('id'), str) or not re.fullmatch(r'[a-z0-9_]+', new_cfg['id']):
                raise ValueError('Agent ID must contain lowercase letters, digits or underscores')
            for field in ('name', 'instruction'):
                if not isinstance(new_cfg.get(field), str) or not new_cfg[field].strip():
                    raise ValueError(f'{field} is required')
            interval = new_cfg.get('decision_interval', 120)
            if isinstance(interval, bool) or not isinstance(interval, int) or not 1 <= interval <= 86400:
                raise ValueError('decision_interval must be 1–86400 seconds')
            new_cfg['entities'] = await self.validate_entities(new_cfg.get('entities'), (old_cfg or {}).get('entities', ()))
            # Creation always paused; edits never silently change activation intent.
            new_cfg['paused'] = (old_cfg or {}).get('paused', False) if updating else True
            replacement = self.factory(new_cfg)  # construction must succeed before disk/runtime mutation
            replacement.paused = new_cfg['paused']
            replacement.status = 'paused' if replacement.paused else 'ready'
            agent_id = new_cfg['id']
            old_agent = self.agents.get(agent_id)
            candidate = copy.deepcopy(data)
            candidate['agents'] = [c for c in candidate['agents'] if c['id'] != agent_id] + [new_cfg]
            # Atomic disk write before stopping old runtime; failed write leaves it untouched.
            self.write(candidate)
            try:
                if old_agent:
                    await self.stop(agent_id)
                self.agents[agent_id] = replacement
                self.start(agent_id)
            except BaseException:
                replacement.paused = True  # never leave the failed replacement executable
                if old_agent:
                    self.agents[agent_id] = old_agent
                    old_agent.paused = True
                    old_agent.status = 'paused'
                else:
                    self.agents.pop(agent_id, None)
                # If disk rollback itself fails, keep the old runtime stopped (fail closed).
                self.write(data)
                if old_agent:
                    old_agent.paused = (old_cfg or {}).get('paused', False)
                    old_agent.status = 'paused' if old_agent.paused else 'ready'
                    try:
                        self.start(agent_id)
                    except BaseException:
                        old_agent.paused = True
                        old_agent.status = 'error'
                        raise
                raise
            return self.describe(agent_id)

    def describe(self, agent_id):
        agent = self.agents[agent_id]
        return dict(status='success', agent_id=agent_id, paused=agent.paused,
                    runtime_status=agent.status, legacy_enabled=self.legacy_enabled,
                    message=('Paused; resume explicitly to activate.' if agent.paused else
                             'Active legacy execution.' if self.legacy_enabled else
                             'Ready; legacy autonomous loops are disabled.'))

    async def pause(self, agent_id):
        async with self.lock:
            if agent_id not in self.agents:
                raise KeyError(agent_id)
            data = self.read()
            for cfg in data['agents']:
                if cfg['id'] == agent_id:
                    cfg['paused'] = True
            self.write(data)
            await self.stop(agent_id)
            return self.describe(agent_id)

    async def resume(self, agent_id):
        async with self.lock:
            if agent_id not in self.agents:
                raise KeyError(agent_id)
            if not self.legacy_enabled:
                raise ValueError('Legacy autonomous loops are disabled; resume does not enable global loops')
            agent = self.agents[agent_id]
            await self.validate_entities(agent.entities)  # no grandfathering at run time
            data = self.read()
            for cfg in data['agents']:
                if cfg['id'] == agent_id:
                    cfg['paused'] = False
            previous = self.read()
            was_paused = agent.paused
            self.write(data)
            agent.paused = False
            agent.status = 'ready'
            try:
                self.start(agent_id)
            except BaseException:
                agent.paused = was_paused
                agent.status = 'paused' if was_paused else 'ready'
                self.write(previous)
                raise
            return self.describe(agent_id)

    async def delete(self, agent_id):
        async with self.lock:
            if agent_id not in self.agents:
                raise KeyError(agent_id)
            data = self.read()
            data['agents'] = [c for c in data['agents'] if c['id'] != agent_id]
            self.write(data)
            await self.stop(agent_id)
            self.agents.pop(agent_id)
            return dict(status='success', message='Agent deleted and local work drained')

    async def check_execution(self, agent_id, parameters, tool_name=None):
        """Managed specialists only; not a global sandbox for unrelated kernel/tools."""
        agent = self.agents.get(agent_id)
        if not agent or agent.paused or not self.legacy_enabled:
            return 'Agent paused, removed, or legacy execution disabled'
        try:
            await self.validate_entities(agent.entities)
        except ValueError as exc:
            return str(exc)
        if agent.paused or self.agents.get(agent_id) is not agent:
            return 'Agent paused or replaced'
        # Explicit entity selectors only: no area/device/all targeting for specialists.
        def targets(value):
            found = []
            if isinstance(value, list):
                for item in value:
                    found.extend(targets(item))
            elif isinstance(value, dict):
                for key, item in value.items():
                    if key in ('area_id', 'device_id', 'floor_id', 'label_id'):
                        raise ValueError('Specialists require explicit entity targets')
                    if key == 'entity_id':
                        found.extend(item if isinstance(item, list) else str(item).split(','))
                    else:
                        found.extend(targets(item))
            return found
        try:
            ids = targets(parameters)
            if tool_name == 'call_ha_service' and not ids:
                return 'Specialists require explicit entity targets'
            if any(e not in agent.entities for e in ids):
                return 'Action targets an entity outside the selected scope'
        except ValueError as exc:
            return str(exc)
        return None
