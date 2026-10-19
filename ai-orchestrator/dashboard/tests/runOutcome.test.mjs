import assert from 'node:assert/strict';
import test from 'node:test';
import { getRunOutcome } from '../src/components/runOutcome.js';

for (const answer of ['', ' \n ', undefined]) {
    test(`empty final (${JSON.stringify(answer)}) is never successful`, () => {
        const outcome = getRunOutcome({ stopped_reason: 'final', answer });
        assert.equal(outcome.completed, false);
        assert.equal(outcome.label, 'Reasoning failed');
        assert.equal(outcome.tone, 'is-warning');
    });
}

test('real final text completes, without requiring tools for conversational goals', () => {
    assert.deepEqual(getRunOutcome({ stopped_reason: 'final', answer: 'Hello' }), {
        completed: true, label: 'Read-only completed', tone: 'is-success',
    });
});

for (const reason of ['llm_error', 'llm_timeout', 'tool_errors']) {
    test(`${reason} is a failure, not a successful final or a budget limit`, () => {
        assert.equal(getRunOutcome({ stopped_reason: reason, answer: 'Error details' }).label, 'Reasoning failed');
    });
}

for (const [status, expected] of [['pending', 'Plan ready · Waiting approval'], ['approved', 'Approved · not executed'], ['executing', 'Executing'], ['executed_with_errors', 'Execution failed or partial'], ['simulated', 'Executed (simulation)'], ['rejected', 'Plan rejected']]) {
    test(`plan ${status} overrides a final answer`, () => {
        const result = getRunOutcome({ stopped_reason: 'final', answer: 'Done', plan: { status, intents: [{}] } });
        assert.equal(result.label, expected);
        assert.equal(result.completed, status === 'simulated');
    });
}

test('budget exhaustion is distinct from provider failure', () => {
    assert.equal(getRunOutcome({ stopped_reason: 'max_iterations', answer: 'Partial' }).label, 'Run finished with limits');
});
