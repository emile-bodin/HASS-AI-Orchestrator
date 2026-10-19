// A terminal transport event is not proof that the model completed the goal.
export function getRunOutcome(result) {
    const hasAnswer = typeof result?.answer === 'string' && !!result.answer.trim();
    const completed = result?.stopped_reason === 'final' && hasAnswer;
    const failed = !hasAnswer || ['llm_error', 'llm_timeout', 'tool_errors'].includes(result?.stopped_reason);
    return {
        completed,
        label: completed ? 'Goal completed' : failed ? 'Run failed' : 'Run finished with limits',
        tone: completed ? 'is-success' : 'is-warning',
    };
}
