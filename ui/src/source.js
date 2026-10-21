const REDIS_SOURCES = new Set(['replicate','compare','stats','file-export','db-export','stream-export']);
export function sourceContext(job, state) {
  if (job?.kind === 'file-import') {
    const file = state.files.find(f=>f.id===job.file_id), status=job.source_status||{};
    return {profile:null, name:file?.name||'입력 파일 미선택', label:'소스 입력 레코드 수',
      telemetry:{keys:status.records,complete:status.stable&&!status.error,updated_at:status.checked_at,
        scope:'입력 원본 레코드 · 쓰기 후 키 수와 다를 수 있음'},file,status};
  }
  const profile=REDIS_SOURCES.has(job?.kind)?state.profiles.find(p=>p.id===job.source_id):null;
  return {profile,name:profile?.name||'입력 데이터',label:profile?'소스 키 수':'소스 생성 수',
    telemetry:profile?state.telemetry[profile.id]||{}:{keys:job?.kind==='gen'?job.options?.count:null,
      complete:job?.kind==='gen',updated_at:state.time,scope:'작업에 정의된 입력'}};
}
