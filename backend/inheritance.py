"""Resolve comparison settings without mutating saved jobs or past run snapshots."""
from copy import deepcopy


def comparison_parent(job, jobs):
    if job.get('kind') != 'compare':
        return None
    if job.get('parent_job_id'):
        return next((p for p in jobs if p['id'] == job['parent_job_id'] and p['kind'] == 'replicate'), None)
    matches = [p for p in jobs if p['kind'] == 'replicate'
               and all(p.get(k) == job.get(k) for k in ('workspace', 'source_id', 'target_id'))]
    return matches[0] if len(matches) == 1 else None


def effective_job(job, jobs, compare_options):
    result = deepcopy(job)
    parent = comparison_parent(job, jobs)
    if not parent:
        if job.get('kind') == 'compare' and job.get('parent_job_id'):
            result['inheritance_error'] = '상위 복제 작업을 찾을 수 없습니다. 연결을 확인하세요.'
        return result
    supported = {v.removeprefix('--') for v in compare_options}
    # Runtime control belongs to the Workbench. Replication-specific flags such as
    # mode/struct are intentionally not forwarded to the standalone compare CLI.
    excluded = {'quick', 'compare', 'progress', 'dry-run'}
    options = {k: deepcopy(v) for k, v in parent.get('options', {}).items()
               if k in supported and k not in excluded and not k.startswith('metrics')}
    mode = str(parent.get('options', {}).get('compare', 'QUICK')).upper()
    options['quick'] = mode == 'QUICK'
    result.update(parent_job_id=parent['id'], workspace=parent['workspace'],
                  source_id=parent['source_id'], target_id=parent['target_id'], options=options,
                  arguments=[], redis_command='', key_template='', file_id='',
                  inherited_from={'job_id': parent['id'], 'name': parent['name'],
                                  'revision': parent['revision'], 'compare_mode': mode},
                  comparison_disabled=mode == 'NONE')
    return result
