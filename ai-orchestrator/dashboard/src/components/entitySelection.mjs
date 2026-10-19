export function groupEntities(catalog, selected, search = '') {
    const rows = new Map(catalog.map(row => [row.entity_id, row]));
    selected.forEach(id => {
        if (!rows.has(id)) rows.set(id, {entity_id: id, name: id, allowed: false, missing: true});
    });
    const query = search.toLowerCase().trim();
    const groups = {};
    [...rows.values()].sort((a, b) => a.entity_id.localeCompare(b.entity_id)).forEach(row => {
        if (query && !`${row.entity_id} ${row.name}`.toLowerCase().includes(query)) return;
        const domain = row.entity_id.split('.')[0];
        (groups[domain] ||= []).push(row);
    });
    return groups;
}

export function toggleEntity(selected, row) {
    if (selected.includes(row.entity_id)) return selected.filter(id => id !== row.entity_id);
    return row.allowed ? [...selected, row.entity_id] : selected;
}
