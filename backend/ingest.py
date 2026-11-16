"""Atomic writers and managed Redis replication. Bloom is advisory only."""
import asyncio
import fnmatch
import hashlib
import json
import time
from urllib.parse import urlparse

import redis.asyncio as redis
from redis.exceptions import RedisError

INTERNAL_PREFIX=b'wb:dedup:'
RESTORE = """
local exists=redis.call('EXISTS',KEYS[1])
if ARGV[3]=='skip' and exists==1 then return 0 end
redis.call('RESTORE',KEYS[1],ARGV[1],ARGV[2],'REPLACE')
if exists==1 then return 2 else return 1 end
"""
DOCUMENT = """
local exists=redis.call('EXISTS',KEYS[1])
if ARGV[2]=='skip' and exists==1 then return 0 end
if ARGV[3]=='json' then
 redis.call('JSON.SET',KEYS[1],'$',ARGV[1])
else
 if tonumber(ARGV[4])>0 then redis.call('SET',KEYS[1],ARGV[1],'PX',ARGV[4])
 else redis.call('SET',KEYS[1],ARGV[1]) end
end
if ARGV[3]=='json' then
 if tonumber(ARGV[4])>0 then redis.call('PEXPIRE',KEYS[1],ARGV[4])
 else redis.call('PERSIST',KEYS[1]) end
end
if exists==1 then return 2 else return 1 end
"""
DUMP = "return {redis.call('DUMP',KEYS[1]) or false,redis.call('PTTL',KEYS[1])}"


def client(profile, binary=True):
    kwargs=dict(username=profile.get('username') or None,password=profile.get('password') or None,
                decode_responses=not binary,socket_connect_timeout=4,socket_timeout=8,health_check_interval=15)
    if profile.get('cluster'):
        return redis.RedisCluster.from_url(profile['url'],**kwargs)
    return redis.Redis.from_url(profile['url'],**kwargs)


def bloom_enabled(profile, settings):
    configured=profile.get('bloom_enabled')
    enabled=(profile.get('product')=='enterprise') if configured is None else configured
    return bool(enabled and settings.get('bloom_enabled',True) and profile.get('deployment','standard')!='active-active')


class SafeWriter:
    def __init__(self, target, profile, settings, audit, policy='skip', status=None):
        self.target,self.profile,self.settings,self.audit,self.policy=target,profile,settings,audit,policy
        self.status=status or (lambda **kw:None)
        # Identity is the destination namespace and mapping, not a transient file/run.
        scope=json.dumps([profile['url'],settings.get('scope','default')],sort_keys=True)
        self.filter=(INTERNAL_PREFIX+hashlib.sha256(scope.encode()).hexdigest()[:24].encode())
        self.bloom=bloom_enabled(profile,settings)

    async def prepare(self):
        self.status(bloom_state='assumed' if self.bloom else 'off')
        if not self.bloom:return
        try:
            try:
                await self.target.execute_command('BF.RESERVE',self.filter,self.settings['bloom_error_rate'],self.settings['bloom_capacity'],'EXPANSION',2)
            except redis.ResponseError as exc:
                if 'exist' not in str(exc).lower():raise
                await self.target.execute_command('BF.INFO',self.filter)
            self.status(bloom_state='confirmed',bloom_filter=self.filter.decode())
        except RedisError as exc:
            self.bloom=False
            self.status(bloom_state='fallback',bloom_error=type(exc).__name__)

    async def check(self,key):
        if not self.bloom:return None
        try:return bool(await self.target.execute_command('BF.EXISTS',self.filter,key))
        except RedisError as exc:
            self.bloom=False;self.status(bloom_state='fallback',bloom_error=type(exc).__name__)
            return None

    async def remember(self,key):
        if not self.bloom:return
        try:await self.target.execute_command('BF.ADD',self.filter,key)
        except RedisError as exc:
            self.bloom=False;self.status(bloom_state='fallback',bloom_error=type(exc).__name__)

    async def write(self,key,value,*,ttl=0,storage='dump',phase='scan'):
        if isinstance(key,str):key=key.encode()
        if key.startswith(INTERNAL_PREFIX):raise ValueError('Reserved metadata key prefix')
        hit=await self.check(key)
        seq=self.audit.intent(key,phase,hit)
        try:
            if self.policy=='skip' and hit is True and await self.target.exists(key):
                self.audit.finish(seq,'skipped',duplicate=True,evidence='exists')
                await self.remember(key)
                return 'skipped'
            if storage=='dump':
                result=await self.target.eval(RESTORE,1,key,max(0,int(ttl)),value,self.policy)
            else:
                if storage not in {'string','json'}:raise ValueError('Unsupported storage format')
                # JSON and TTL arguments are validated before entering the script.
                json.loads(value)
                result=await self.target.eval(DOCUMENT,1,key,value,self.policy,storage,max(0,int(ttl)))
            outcome={0:'skipped',1:'created',2:'updated'}[result]
            self.audit.finish(seq,outcome,duplicate=result in {0,2},evidence='atomic-write')
        except (redis.TimeoutError,redis.ConnectionError) as exc:
            self.audit.finish(seq,'unknown',evidence='acknowledgement-unknown',error_type=type(exc).__name__)
            raise
        except Exception as exc:
            self.audit.finish(seq,'error',evidence='write-rejected',error_type=type(exc).__name__)
            raise
        await self.remember(key)
        return outcome


async def replicate(source_profile,target_profile,job,settings,audit,status):
    source,target=client(source_profile),client(target_profile)
    options=job.get('options',{});mode=str(options.get('mode','SCAN')).upper()
    policy=job.get('duplicate_policy','observe')
    writer=SafeWriter(target,target_profile,settings,audit,policy,status)
    pubsubs=[];subclients=[];tasks=[];queue=asyncio.Queue(maxsize=2048)
    pattern=options.get('key-pattern','*')
    def eligible(key):
        text=key.decode('utf8','surrogateescape') if isinstance(key,bytes) else key
        return not key.startswith(INTERNAL_PREFIX) and fnmatch.fnmatchcase(text,pattern)
    async def copy(key,phase):
        if not eligible(key):return
        if options.get('key-type') and (await source.type(key)).decode()!=options['key-type']:return
        size=await source.memory_usage(key)
        if size and size>settings['max_document_mib']*1024*1024:
            raise ValueError('Managed replication key exceeds configured size; use native RIOTX for chunked big keys')
        started=time.monotonic();payload,ttl=await source.eval(DUMP,1,key)
        if payload is None or ttl==-2:
            if phase=='live':
                seq=audit.intent(key,phase)
                try:
                    await target.delete(key)
                    audit.finish(seq,'deleted',evidence='source-absent')
                except RedisError as exc:
                    audit.finish(seq,'unknown',error_type=type(exc).__name__);raise
            return
        if not options.get('no-ttl') and ttl>=0:
            ttl-=int((time.monotonic()-started)*1000)
            if ttl<=0:return
        else:ttl=0
        await writer.write(key,payload,ttl=ttl,phase=phase)
    async def listen(ps):
        async for event in ps.listen():
            if event['type']=='pmessage':
                await queue.put(event['channel'].split(b'__:',1)[-1])
    try:
        await writer.prepare()
        if mode in {'LIVE','LIVEONLY'}:
            clients=[source]
            if source_profile.get('cluster'):
                await source.initialize()
                clients=[source.get_redis_connection(n) for n in source.get_primaries()]
            database=urlparse(source_profile['url']).path.lstrip('/') or '0'
            for c in clients:
                # Dedicated subscription pools must allow long idle periods.
                pool=redis.ConnectionPool(**(c.connection_pool.connection_kwargs|{'socket_timeout':None}))
                subscriber=redis.Redis(connection_pool=pool);subclients.append(subscriber)
                ps=subscriber.pubsub();pubsubs.append(ps)
                await ps.psubscribe(f'__keyspace@{database}__:*')
                # Ensure the subscription acknowledgement has arrived before SCAN.
                await ps.get_message(ignore_subscribe_messages=False,timeout=5)
                tasks.append(asyncio.create_task(listen(ps)))
            status(coverage='live-best-effort',phase='scan' if mode=='LIVE' else 'live')
        else:status(coverage='complete-decisions',phase='scan')
        if mode!='LIVEONLY':
            async for key in source.scan_iter(match=pattern,count=options.get('scan-count',200)):
                await copy(key,'scan')
                for task in tasks:
                    if task.done():task.result();raise RuntimeError('Source subscription ended')
        if mode in {'LIVE','LIVEONLY'}:
            status(phase='live')
            while True:
                for task in tasks:
                    if task.done():task.result();raise RuntimeError('Source subscription ended')
                try:key=await asyncio.wait_for(queue.get(),1)
                except asyncio.TimeoutError:continue
                await copy(key,'live')
        status(phase='complete')
    finally:
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        for ps in pubsubs:await ps.aclose()
        for subscriber in subclients:
            await subscriber.aclose();await subscriber.connection_pool.aclose()
        await source.aclose();await target.aclose()
