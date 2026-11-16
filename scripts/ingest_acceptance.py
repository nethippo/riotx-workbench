"""Explicitly authorized acceptance test. Writes only a generated test prefix.
Run INSIDE a disposable Workbench container, never via the legacy demo test.
"""
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime,timezone
from pathlib import Path

import redis
from bson import ObjectId,Decimal128,json_util
from pymongo import MongoClient
from backend.mongo_source import document_key

BASE=os.environ.get('WORKBENCH_TEST_API','http://127.0.0.1:8088/api')
SOURCE=os.environ.get('WORKBENCH_TEST_SOURCE','redis://host.docker.internal:6379/0')
TARGET=os.environ['WORKBENCH_TEST_TARGET']
MONGO=os.environ.get('WORKBENCH_TEST_MONGO','mongodb://riotx-mongo-acceptance:27017/')
PREFIX='workbench:acceptance:'+uuid.uuid4().hex+':'
report={'prefix':PREFIX,'checks':[],'runs':[]}
s=redis.Redis.from_url(SOURCE,socket_timeout=5)
t=redis.Redis.from_url(TARGET,socket_timeout=5)
m=MongoClient(MONGO,serverSelectionTimeoutMS=5000)
old_flags=None;filters=set();active=None

def request(path,body=None,method=None):
 data=json.dumps(body).encode() if body is not None else None
 req=urllib.request.Request(BASE+path,data=data,method=method or ('POST' if data is not None else 'GET'),headers={'Content-Type':'application/json'})
 try:
  with urllib.request.urlopen(req,timeout=20) as response:return json.load(response)
 except urllib.error.HTTPError as error:raise RuntimeError(f'{error.code}: '+error.read().decode())

def check(label):report['checks'].append(label);print('PASS',label,flush=True)
def wait(fn,timeout=40):
 until=time.monotonic()+timeout
 while time.monotonic()<until:
  result=fn()
  if result:return result
  time.sleep(.1)
 raise AssertionError('Timed out waiting for acceptance state')
def state():return request('/state')
def run_state(ident):return next(r for r in state()['runs'] if r['id']==ident)
def done(ident):
 r=run_state(ident)
 if r['status'] in {'SUCCEEDED','FAILED','UNKNOWN','CANCELLED'}:return r
 return None
def run_job(job):
 global active
 r=request('/jobs/'+job['id']+'/runs',{});active=r['id'];report['runs'].append(active);return active

def finished(ident):
 r=wait(lambda:done(ident),120)
 if r['status']!='SUCCEEDED':raise AssertionError(json.dumps({'run':r,'logs':request('/runs/'+ident+'/logs')},ensure_ascii=False))
 filters.add(r.get('audit_status',{}).get('bloom_filter',''))
 return r

def fingerprint(c):
 result={}
 for key in c.scan_iter(count=200):
  if key.startswith(PREFIX.encode()) or key.startswith(b'wb:dedup:'):continue
  payload=c.dump(key)
  if payload is not None:result[key]=(hashlib.sha256(payload).hexdigest(),c.pttl(key))
 return result

def preserve(c,before):
 changed=[]
 for key,(digest,ttl) in before.items():
  payload=c.dump(key)
  if payload is None and ttl>=0:continue
  if payload is None or hashlib.sha256(payload).hexdigest()!=digest:changed.append(hashlib.sha256(key).hexdigest())
 assert not changed,{'unrelated_changed_keys':len(changed)}
 return len(before)

before_s=fingerprint(s);before_t=fingerprint(t)
try:
 sp=request('/profiles',{'name':'Acceptance localhost source','url':SOURCE,'mask_keys':False,'observe':False})
 tp=request('/profiles',{'name':'Acceptance Enterprise target','url':TARGET,'product':'enterprise','connection_mode':'proxy','mask_keys':False,'observe':False})
 defaults=request('/ingest/defaults');defaults.update(record_key_names=True,chunk_mib=1,max_document_mib=2)
 request('/ingest/defaults',defaults,'PUT')
 check('Enterprise defaults: Proxy and Bloom enabled')
 assert tp['bloom_enabled'] and not tp['cluster']

 for i in range(8):s.set(PREFIX+'scan:'+str(i),'source-'+str(i),ex=600)
 s.hset(PREFIX+'scan:hash',mapping={'field':'value'});s.expire(PREFIX+'scan:hash',600)
 s.rpush(PREFIX+'scan:list','a','b');s.expire(PREFIX+'scan:list',600)
 s.sadd(PREFIX+'scan:set','a','b');s.expire(PREFIX+'scan:set',600)
 for i in range(3):t.set(PREFIX+'scan:'+str(i),'old-target',ex=600)
 base={'workspace':'Enterprise acceptance','kind':'replicate','source_id':sp['id'],'target_id':tp['id'],'options':{'mode':'SCAN','key-pattern':PREFIX+'scan:*'},'duplicate_policy':'observe'}
 job=request('/jobs',dict(base,name='Audited SCAN'))
 rid=run_job(job);r=finished(rid)
 summary=request('/runs/'+rid+'/audit/summary')
 assert summary['created']==8 and summary['updated']==3,summary
 assert t.hget(PREFIX+'scan:hash','field')==b'value' and t.lrange(PREFIX+'scan:list',0,-1)==[b'a',b'b']
 assert t.smembers(PREFIX+'scan:set')=={b'a',b'b'} and 500<t.ttl(PREFIX+'scan:0')<=600
 keys=request('/runs/'+rid+'/audit/keys')['items'];assert len(keys)==3
 event=request('/runs/'+rid+'/audit/events?event_id='+keys[0]['id'])['items'][0]
 assert event['key']==keys[0]['key'] and 'encrypted_key' not in event
 check('Audited SCAN preserves String/Hash/List/Set and TTL; 3 exact existing keys; event link resolves')

 t.set(PREFIX+'scan:0','keep-me',ex=600)
 skip_job=request('/jobs',dict(base,name='Safe skip SCAN',duplicate_policy='skip'))
 rid=run_job(skip_job);finished(rid);summary=request('/runs/'+rid+'/audit/summary')
 assert summary['skipped']==11 and summary['unique_duplicate_keys']==11 and t.get(PREFIX+'scan:0')==b'keep-me',summary
 check('Existing-key policy preserves 11 keys without overwrites')

 # Force a true Bloom positive for an absent key; it must still be inserted.
 filter_key=r['audit_status']['bloom_filter'];new=PREFIX+'scan:false-positive'
 t.execute_command('BF.ADD',filter_key,new);s.set(new,'must-not-drop',ex=600)
 rid=run_job(skip_job);finished(rid);summary=request('/runs/'+rid+'/audit/summary')
 assert t.get(new)==b'must-not-drop' and summary['positive_without_existing_key']>=1,summary
 check('Bloom positive with absent actual key never drops the record')

 # Deliberately use a wrong-type owned filter key to exercise exact fallback.
 t.delete(filter_key);t.set(filter_key,'wrong-type',ex=600)
 rid=run_job(skip_job);r=finished(rid)
 assert r['audit_status']['bloom_state']=='fallback',r
 check('Bloom failure falls back to exact/atomic checks')

 old_flags=s.config_get('notify-keyspace-events')['notify-keyspace-events']
 s.config_set('notify-keyspace-events',''.join(dict.fromkeys(old_flags+'KEA')))
 live=request('/jobs',dict(base,name='Audited LIVE',options={'mode':'LIVE','key-pattern':PREFIX+'live:*'}))
 rid=run_job(live)
 wait(lambda:run_state(rid).get('audit_status',{}).get('phase')=='live')
 for value in ['one','two']:
  s.set(PREFIX+'live:key',value,ex=600)
  wait(lambda:t.get(PREFIX+'live:key')==value.encode())
 s.expire(PREFIX+'live:key',120);wait(lambda:0<t.ttl(PREFIX+'live:key')<=120)
 s.delete(PREFIX+'live:key');wait(lambda:not t.exists(PREFIX+'live:key'))
 summary=wait(lambda:(x if (x:=request('/runs/'+rid+'/audit/summary')).get('duplicate_events',0)>=1 else None))
 request('/runs/'+rid+'/cancel',{});wait(lambda:done(rid));filters.add(run_state(rid)['audit_status'].get('bloom_filter',''))
 s.config_set('notify-keyspace-events',old_flags);old_flags=None
 check('LIVE updates existing values, TTL and deletion; cancellation works; source notification setting restored')

 mp=request('/profiles',{'name':'Acceptance MongoDB fixture','url':MONGO,'product':'mongodb'})
 collection=m['workbench_acceptance'][PREFIX.split(':')[2]]
 docs=[{'_id':ObjectId(),'payload':'x'*24000,'price':Decimal128('12.50'),'at':datetime(2026,11,16,tzinfo=timezone.utc),'array':[1,2,3]} for i in range(180)]
 collection.insert_many(docs)
 mongo_job=request('/jobs',{'name':'Mongo bounded resume','workspace':'Enterprise acceptance','kind':'mongo-import','source_id':mp['id'],'target_id':tp['id'],'duplicate_policy':'skip','ingest':{'database':collection.database.name,'collection':collection.name,'key_prefix':PREFIX+'mongo:','storage':'json','bson_mode':'canonical'}})
 rid=run_job(mongo_job)
 wait(lambda:request('/runs/'+rid+'/audit/summary').get('processed',0)>=8)
 if not done(rid):
  request('/runs/'+rid+'/cancel',{});wait(lambda:done(rid))
  request('/runs/'+rid+'/resume',{});finished(rid);check('MongoDB interrupted batch resumes from durable checkpoint')
 else:raise AssertionError('MongoDB fixture completed before interruption could be tested')
 r=run_state(rid);filters.add(r['audit_status'].get('bloom_filter',''))
 assert r['audit_status']['chunks']>=4 and r['audit_status']['chunks_complete']==r['audit_status']['chunks'],r
 for doc in docs:
  payload=t.execute_command('JSON.GET',document_key(doc['_id'],PREFIX+'mongo:'))
  assert json.loads(payload)['price']=={'$numberDecimal':'12.50'}
 assert len(list(t.scan_iter(match=PREFIX+'mongo:*')))==len(docs)
 assert not list((Path('/data/ingest')/rid).glob('*.jsonl'))
 check('MongoDB BSON types, bounded chunk split, exact counts and successful chunk cleanup')
 collection.drop()
 report['mongo_documents']=len(docs);report['mongo_chunks']=r['audit_status']['chunks']
 report['preservation']={'source_original_keys':preserve(s,before_s),'target_original_keys':preserve(t,before_t)}
 check('All unrelated original key payloads preserved')
 report['ok']=True
finally:
 if active:
  try:
   r=run_state(active)
   if r['status'] in {'RUNNING','STARTING','QUEUED','CANCELLING'}:request('/runs/'+active+'/cancel',{})
   filters.add(r.get('audit_status',{}).get('bloom_filter',''))
  except Exception:pass
 if old_flags is not None:s.config_set('notify-keyspace-events',old_flags)
 for c in [s,t]:
  for key in c.scan_iter(match=PREFIX+'*',count=200):c.delete(key)
 for key in filters:
  if key.startswith('wb:dedup:'):t.delete(key)
 report['cleanup']={'source_test_keys':sum(1 for _ in s.scan_iter(match=PREFIX+'*')),'target_test_keys':sum(1 for _ in t.scan_iter(match=PREFIX+'*'))}
 Path('/tmp/ingest-acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
 s.close();t.close();m.close()
print(json.dumps(report,ensure_ascii=False,indent=2))
