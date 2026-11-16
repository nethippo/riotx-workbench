"""Bounded MongoDB batch import with durable chunk manifest and explicit resume."""
import asyncio
import base64
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from bson import json_util
from pymongo import AsyncMongoClient

from .ingest import SafeWriter, client


def mongo_client(profile):
    return AsyncMongoClient(profile['url'],username=profile.get('username') or None,
                            password=profile.get('password') or None,serverSelectionTimeoutMS=5000,
                            connectTimeoutMS=5000,socketTimeoutMS=15000,appname='riotx-workbench')


def validate_filter(value):
    if isinstance(value,dict):
        for key,item in value.items():
            if key in {'$where','$function','$accumulator','$out','$merge'}:raise ValueError('Executable MongoDB filter operators are not allowed')
            validate_filter(item)
    elif isinstance(value,list):
        for item in value:validate_filter(item)


def document_key(ident,prefix):
    # Include BSON type information to prevent ObjectId/string/int collisions.
    normalized=json_util.dumps(ident,json_options=json_util.CANONICAL_JSON_OPTIONS,sort_keys=True,separators=(',',':'))
    encoded=base64.urlsafe_b64encode(normalized.encode()).decode().rstrip('=')
    return (prefix+encoded).encode()


def atomic_manifest(path,data):
    temp=path.with_suffix('.tmp')
    with open(temp,'w') as out:
        json.dump(data,out);out.flush();os.fsync(out.fileno())
    os.replace(temp,path)


async def import_mongo(source_profile,target_profile,job,settings,audit,status,directory):
    options=job['ingest'];database=options['database'];collection=options['collection']
    query=options.get('filter',{});validate_filter(query)
    projection={key:1 for key in options.get('fields',[])} or None
    if projection is not None:projection['_id']=1
    prefix=options.get('key_prefix') or f'mongo:{database}:{collection}:'
    storage=options.get('storage','json');ttl=options.get('ttl_seconds',0)*1000
    mongo=mongo_client(source_profile);target=client(target_profile)
    writer=SafeWriter(target,target_profile,settings,audit,job.get('duplicate_policy','skip'),status)
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=directory/'manifest.json'
    signature=hashlib.sha256(json.dumps([source_profile['url'],target_profile['url'],options,settings],sort_keys=True).encode()).hexdigest()
    if path.exists():
        manifest=json.loads(path.read_text())
        if manifest['signature']!=signature:raise ValueError('Resume settings or connection changed')
    else:
        manifest={'signature':signature,'chunks':[],'read_after':None,'extraction_complete':False,'id_type':None,'records':0}
        atomic_manifest(path,manifest)
    # A crash after checkpoint commit but before unlink may leave a completed file.
    for entry in manifest['chunks']:
        if entry['done']:(directory/entry['file']).unlink(missing_ok=True)
    queue=asyncio.Queue(maxsize=2)
    slots=asyncio.Semaphore(2)
    chunk_limit=settings['chunk_mib']*1024*1024;document_limit=settings['max_document_mib']*1024*1024
    bson_options=json_util.CANONICAL_JSON_OPTIONS if options.get('bson_mode','canonical')=='canonical' else json_util.RELAXED_JSON_OPTIONS
    def save():atomic_manifest(path,manifest)
    async def produce():
        # Existing ready chunks are immutable and must finish before later reads.
        for entry in manifest['chunks']:
            if not entry['done']:
                await slots.acquire();await queue.put(entry)
        if manifest['extraction_complete']:
            await queue.put(None);return
        actual=query
        if manifest['read_after'] is not None:
            last=json_util.loads(manifest['read_after'])
            actual={'$and':[query,{'_id':{'$gt':last}}]}
        cursor=mongo[database][collection].find(actual,projection).sort('_id',1).batch_size(200)
        buffer=[];size=0;last_id=None
        async def flush():
            nonlocal buffer,size
            if not buffer:return
            if shutil.disk_usage(directory).free<max(128*1024*1024,chunk_limit*3):
                raise OSError('Insufficient staging disk space; free space and resume')
            await slots.acquire()
            name=f"chunk-{len(manifest['chunks']):08d}.jsonl";dest=directory/name
            with open(dest.with_suffix('.tmp'),'wb') as out:
                for line in buffer:out.write(line)
                out.flush();os.fsync(out.fileno())
            os.replace(dest.with_suffix('.tmp'),dest)
            checksum=hashlib.sha256()
            with open(dest,'rb') as inp:
                for part in iter(lambda:inp.read(1024*1024),b''):checksum.update(part)
            entry={'file':name,'records':len(buffer),'bytes':size,'sha256':checksum.hexdigest(),'done':False,'next_line':0}
            manifest['chunks'].append(entry);manifest['read_after']=json_util.dumps(last_id,json_options=json_util.CANONICAL_JSON_OPTIONS)
            manifest['records']+=len(buffer);save();buffer=[];size=0
            status(records_read=manifest['records'],chunks=len(manifest['chunks']),staging_bytes=sum(e['bytes'] for e in manifest['chunks'] if not e['done']))
            # The producer cannot create another file until a queue slot is free.
            await queue.put(entry)
        try:
            async for doc in cursor:
                kind=type(doc['_id']).__name__
                if manifest['id_type'] not in {None,kind}:
                    raise ValueError('Mixed BSON _id types are not supported for resumable batch import')
                manifest['id_type']=kind
                line=(json_util.dumps(doc,json_options=bson_options,separators=(',',':'))+'\n').encode()
                if len(line)>document_limit:raise ValueError('A converted document exceeds the configured size limit')
                if buffer and size+len(line)>chunk_limit:await flush()
                buffer.append(line);size+=len(line);last_id=doc['_id']
            await flush();manifest['extraction_complete']=True;save();await queue.put(None)
        finally:await cursor.close()
    async def consume():
        while True:
            entry=await queue.get()
            if entry is None:return
            dest=directory/entry['file'];digest=hashlib.sha256()
            with open(dest,'rb') as inp:
                for part in iter(lambda:inp.read(1024*1024),b''):digest.update(part)
            if digest.hexdigest()!=entry['sha256']:raise ValueError('Staging file checksum mismatch')
            with open(dest,'rb') as inp:
                for index,line in enumerate(inp):
                    if index<entry['next_line']:continue
                    doc=json_util.loads(line)
                    key=document_key(doc['_id'],prefix)
                    # Preserve canonical BSON wrappers in stored JSON, do not stringify twice.
                    await writer.write(key,line.strip(),ttl=ttl,storage=storage,phase='mongo-batch')
                    entry['next_line']=index+1
                    if entry['next_line']%200==0:save()
            entry['done']=True;save();dest.unlink(missing_ok=True);slots.release()
            status(chunks_complete=sum(e['done'] for e in manifest['chunks']),staging_bytes=sum(e['bytes'] for e in manifest['chunks'] if not e['done']))
    try:
        await mongo.admin.command('ping')
        await writer.prepare()
        try:
            total=await mongo[database][collection].count_documents(query,maxTimeMS=5000)
        except Exception:total=None
        status(source_documents=total)
        status(phase='mongo-batch',coverage='complete-decisions',resumable=True,snapshot_consistent=False)
        async with asyncio.TaskGroup() as group:
            group.create_task(produce());group.create_task(consume())
        status(phase='complete')
    finally:
        await mongo.close();await target.aclose()
