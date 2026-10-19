// Reasoning finality, review authority, and replay outcomes are separate.
export function getPlanOutcome(plan) {
    const status = plan?.status;
    const out = (label, completed = false, tone = 'is-warning') => ({ completed, label, tone });
    if (status === 'pending') return out('Plan ready · Waiting approval');
    if (status === 'approved') return out('Approved · not executed');
    if (status === 'executing') return out('Executing');
    if (status === 'rejected') return out('Plan rejected');
    if (status === 'expired') return out('Plan expired');
    if (status === 'executed_with_errors' || status === 'failed') return out('Execution failed or partial', false, 'is-danger');
    const rows = plan?.execution_results || [];
    if (status === 'simulated' || (status === 'executed' && rows.some(row => row.result?.dry_run))) return out('Executed (simulation)', true, 'is-success');
    if (status === 'executed') {
        if (!(plan?.intents?.length || plan?.mutating_count)) return out('Read-only completed', true, 'is-success');
        return out('Executed · tool accepted', true, 'is-success');
    }
    return out('Execution status unknown');
}

export function getRunOutcome(result) {
    const hasAnswer = typeof result?.answer === 'string' && !!result.answer.trim();
    if (!hasAnswer || ['llm_error', 'llm_timeout', 'tool_errors'].includes(result?.stopped_reason)) {
        return { completed: false, label: 'Reasoning failed', tone: 'is-warning' };
    }
    if (result?.plan) return getPlanOutcome(result.plan);
    if (result?.stopped_reason === 'final') return { completed: true, label: 'Read-only completed', tone: 'is-success' };
    return { completed: false, label: 'Run finished with limits', tone: 'is-warning' };
}
