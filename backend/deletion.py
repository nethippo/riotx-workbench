"""Pure deletion planning; storage keeps jobs and run history for restoration."""
from .inheritance import comparison_parent


def deletion_targets(jobs, *, job_id='', workspace=None):
    selected = {j['id'] for j in jobs if (job_id and j['id'] == job_id)
                or (workspace is not None and j['workspace'] == workspace)}
    while True:
        children = {j['id'] for j in jobs if (p := comparison_parent(j, jobs)) and p['id'] in selected}
        expanded = selected | children
        if expanded == selected:
            return [j for j in jobs if j['id'] in selected]
        selected = expanded


def active_deletion_runs(targets, runs, active_statuses):
    ids = {j['id'] for j in targets}
    return [r for r in runs if r['status'] in active_statuses
            and (r['job_id'] in ids or r.get('snapshot', {}).get('inherited_from', {}).get('job_id') in ids)]
