// Explicit parent IDs survive later endpoint and workspace changes.
export function comparisonParent(job,jobs){
 if(job.kind!=='compare')return null;
 if(job.parent_job_id)return jobs.find(p=>p.id===job.parent_job_id&&p.kind==='replicate')||null;
 const matches=jobs.filter(p=>p.kind==='replicate'&&p.workspace===job.workspace&&p.source_id===job.source_id&&p.target_id===job.target_id);
 return matches.length===1?matches[0]:null;
}
