import test from 'node:test';
import assert from 'node:assert/strict';
import { groupEntities, toggleEntity } from '../src/components/entitySelection.mjs';

test('groups domains, searches friendly names, preserves restricted selections', () => {
    const groups = groupEntities([
        {entity_id: 'light.kitchen', name: 'Kitchen ceiling', allowed: true},
        {entity_id: 'sensor.power', name: 'Power', allowed: false},
    ], ['switch.old', 'sensor.power'], '');
    assert.deepEqual(Object.keys(groups), ['light', 'sensor', 'switch']);
    assert.equal(groups.switch[0].allowed, false);
    assert.equal(groups.switch[0].missing, true);
    assert.equal(groupEntities(groups.light, [], 'ceiling').light.length, 1);
});

test('cannot add restricted IDs, but can remove historical IDs', () => {
    assert.deepEqual(toggleEntity([], {entity_id: 'sensor.power', allowed: false}), []);
    assert.deepEqual(toggleEntity(['sensor.power'], {entity_id: 'sensor.power', allowed: false}), []);
    assert.deepEqual(toggleEntity([], {entity_id: 'light.kitchen', allowed: true}), ['light.kitchen']);
});

test('empty-scope UI must not promise auto-discovery execution', async () => {
    const { readFile } = await import('node:fs/promises');
    const details = await readFile(new URL('../src/components/AgentDetails.jsx', import.meta.url), 'utf8');
    assert.equal(details.includes('Agent will use Auto-Discovery'), false);
});
