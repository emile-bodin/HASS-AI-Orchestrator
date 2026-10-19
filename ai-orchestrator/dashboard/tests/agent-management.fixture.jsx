import React, { useState, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { AgentFactory } from '../src/components/AgentFactory';
import { AgentGrid } from '../src/components/AgentGrid';
import '../src/index.css';
// Confine fixture requests to its model-free, HA-free loopback API.
const realFetch = window.fetch.bind(window);
window.fetch = (input, options) => {
    if (typeof input !== 'string' || !input.startsWith('api/')) throw new Error('Non-fixture request blocked');
    return realFetch(`http://127.0.0.1:18999/${input}`, options);
};
window.alert = text => { window.fixtureNotice = text; };
function Fixture() {
    const [agents, setAgents] = useState([]);
    const [factory, setFactory] = useState(false);
    const refresh = async () => setAgents(await (await fetch('api/agents')).json());
    useEffect(() => { refresh(); }, []);
    return <main className="p-6 dark bg-slate-950 text-slate-200 min-h-screen">
        <h1>Offline fixture · no HA/model connection</h1>
        <AgentGrid agents={agents} onAgentCreate={() => setFactory(true)} onAgentsChanged={refresh} />
        {factory && <AgentFactory startOpen launcher={false} onDismiss={() => setFactory(false)} onAgentCreated={refresh} />}
    </main>;
}
createRoot(document.getElementById('root')).render(<Fixture />);
