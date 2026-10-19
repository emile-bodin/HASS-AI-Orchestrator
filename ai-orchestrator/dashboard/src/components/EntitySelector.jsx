import React, { useEffect, useState } from 'react';
import { groupEntities, toggleEntity } from './entitySelection.mjs';

export function EntitySelector({ value = [], onChange }) {
    const [catalog, setCatalog] = useState([]);
    const [query, setQuery] = useState('');
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);
    useEffect(() => {
        let active = true;
        fetch('api/factory/entities').then(async response => {
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Entity inventory unavailable');
            if (active) setCatalog(data.entities);
        }).catch(error => { if (active) setError(error.message); })
            .finally(() => { if (active) setLoading(false); });
        return () => { active = false; };
    }, []);
    const groups = groupEntities(catalog, value, query);
    return <section className="space-y-2" aria-label="Agent entity selection">
        <p className="text-sm text-slate-300">{value.length} selected · explicit entities only</p>
        <input aria-label="Search entities" placeholder="Search name or entity ID" value={query}
            onChange={event => setQuery(event.target.value)}
            className="w-full bg-slate-950 border border-slate-700 rounded p-2" />
        {loading && <p role="status">Loading Home Assistant entities…</p>}
        {error && <p role="alert" className="text-red-300">{error}. Existing entries remain removable.</p>}
        {!value.length && <p className="text-amber-300 text-xs">Select at least one entity. Empty never means all.</p>}
        <div className="max-h-64 overflow-auto space-y-3">
            {Object.entries(groups).map(([domain, rows]) => <fieldset key={domain} className="border border-slate-700 rounded p-2">
                <legend className="text-slate-300 px-1">{domain} · {rows.filter(row => value.includes(row.entity_id)).length} selected</legend>
                {rows.map(row => <label key={row.entity_id} className="flex gap-2 py-1 text-sm items-start">
                    <input type="checkbox" checked={value.includes(row.entity_id)}
                        disabled={!row.allowed && !value.includes(row.entity_id)}
                        onChange={() => onChange(toggleEntity(value, row))} className="mt-1" />
                    <span className="min-w-0 break-all">{row.name}<small className="block text-slate-400">{row.entity_id}
                        {!row.allowed && ` · ${row.missing ? 'unavailable' : 'restricted'} (remove only)`}</small></span>
                </label>)}
            </fieldset>)}
            {!loading && !Object.keys(groups).length && <p>No matching entities.</p>}
        </div>
    </section>;
}
