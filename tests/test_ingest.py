import asyncio
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from cryptography.fernet import Fernet
from redis.exceptions import ResponseError, TimeoutError
from backend.audit import Audit
from backend.ingest import SafeWriter, bloom_enabled
from backend import main as wb
from backend.mongo_source import document_key, validate_filter
from bson import ObjectId

SETTINGS={'bloom_enabled':True,'bloom_error_rate':0.001,'bloom_capacity':1000,'scope':'test','max_document_mib':1,'chunk_mib':1,'retention_days':7}
PROFILE={'url':'redis://target:6379/0','product':'enterprise','id':'target','mask_keys':False}


class FakeRedis:
    def __init__(self,existing=None,positive=False,unavailable=False,uncertain=False):
        self.data=dict(existing or {});self.positive=positive;self.unavailable=unavailable;self.uncertain=uncertain;self.remembered=[]
    async def execute_command(self,cmd,*args):
        if self.unavailable:raise ResponseError('unknown command')
        if cmd=='BF.EXISTS':return int(self.positive)
        if cmd=='BF.ADD':self.remembered.append(args[1])
        return 'OK'
    async def exists(self,key):return key in self.data
    async def eval(self,script,count,key,*args):
        if self.uncertain:raise TimeoutError()
        policy=args[2] if "RESTORE" in script else args[1]
        exists=key in self.data
        if exists and policy=='skip':return 0
        self.data[key]=args[1] if "RESTORE" in script else args[0]
        return 2 if exists else 1


class IngestTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db=sqlite3.connect(':memory:');self.cipher=Fernet(Fernet.generate_key())
        self.audit=Audit(self.db,self.cipher,'run',record_names=True)
    async def asyncTearDown(self):self.db.close()
    async def test_false_positive_does_not_drop_new_data(self):
        target=FakeRedis(positive=True);writer=SafeWriter(target,PROFILE,SETTINGS,self.audit)
        await writer.prepare();self.assertEqual(await writer.write(b'customer:1',b'{"a":1}',storage='json'),'created')
        self.assertIn(b'customer:1',target.data)
        summary=self.audit.summary();self.assertEqual(summary['positive_without_existing_key'],1);self.assertEqual(summary['unique_duplicate_keys'],0)
    async def test_filter_miss_does_not_overwrite_existing_target(self):
        target=FakeRedis({b'k':b'old'},positive=False);writer=SafeWriter(target,PROFILE,SETTINGS,self.audit)
        await writer.prepare();self.assertEqual(await writer.write(b'k',b'new'),'skipped')
        self.assertEqual(target.data[b'k'],b'old');self.assertEqual(self.audit.summary()['unique_duplicate_keys'],1)
    async def test_live_observation_keeps_updating_and_counts_distinct_keys(self):
        target=FakeRedis({b'k':b'old'},positive=True);writer=SafeWriter(target,PROFILE,SETTINGS,self.audit,policy='observe')
        await writer.prepare()
        await writer.write(b'k',b'new',phase='live');await writer.write(b'k',b'newest',phase='live')
        summary=self.audit.summary();self.assertEqual(summary['unique_duplicate_keys'],1);self.assertEqual(summary['duplicate_events'],2)
        self.assertEqual(target.data[b'k'],b'newest');self.assertEqual(self.audit.keys()['items'][0]['hits'],2)
    async def test_missing_bloom_falls_back_without_dropping_data(self):
        states=[];writer=SafeWriter(FakeRedis(unavailable=True),PROFILE,SETTINGS,self.audit,status=lambda **kw:states.append(kw))
        await writer.prepare();self.assertFalse(writer.bloom)
        self.assertEqual(await writer.write(b'new',b'1'),'created');self.assertTrue(any(s.get('bloom_state')=='fallback' for s in states))
    async def test_lost_acknowledgement_is_unknown_not_success(self):
        target=FakeRedis(uncertain=True);writer=SafeWriter(target,PROFILE,SETTINGS,self.audit)
        with self.assertRaises(TimeoutError):await writer.write(b'key',b'payload')
        self.assertEqual(self.audit.summary()['unknown'],1);self.assertEqual(target.remembered,[])
    async def test_names_encrypted_masking_and_binary_names(self):
        seq=self.audit.intent(b'private:customer');self.audit.finish(seq,'skipped',duplicate=True)
        stored=self.db.execute('SELECT body FROM ingest_events').fetchone()[0]
        self.assertNotIn('private:customer',stored);self.assertNotIn('encrypted_key',self.audit.events()['items'][0])
        self.assertEqual(self.audit.events()['items'][0]['key'],'private:customer')
        masked=Audit(self.db,self.cipher,'masked',record_names=False)
        seq=masked.intent(b'sensitive');masked.finish(seq,'created')
        self.assertTrue(masked.events()['items'][0]['key'].startswith('key:'))
        seq=self.audit.intent(b'\xff\x00');self.audit.finish(seq,'created')
        self.assertEqual(self.audit.events()['items'][-1]['encoding'],'base64')
    async def test_same_attempt_completion_is_idempotent_and_cursor_paged(self):
        for n in range(3):
            seq=self.audit.intent(str(n));self.audit.finish(seq,'skipped',duplicate=True);self.audit.finish(seq,'skipped',duplicate=True)
        first=self.audit.keys(limit=2);self.assertEqual(len(first['items']),2)
        second=self.audit.keys(after=first['next_cursor'],limit=2);self.assertEqual(len(second['items']),1)
        self.assertEqual(self.audit.summary()['duplicate_events'],3)
    async def test_profile_defaults_and_live_skip_guard(self):
        p=wb.Profile(name='Enterprise',url='redis://target:12000',product='enterprise')
        self.assertFalse(p.cluster);self.assertTrue(p.bloom_enabled)
        p=wb.Profile(name='Enterprise',url='redis://target:12000',product='enterprise',connection_mode='cluster')
        self.assertTrue(p.cluster)
        profiles={'s':{'id':'s','url':'redis://source:6379'},'t':dict(PROFILE,id='t')}
        with patch.object(wb,'secret_profile',side_effect=lambda i:profiles[i]):
            with self.assertRaises(wb.HTTPException) as error:
                wb.validate_managed({'kind':'replicate','source_id':'s','target_id':'t','duplicate_policy':'skip','options':{'mode':'LIVE'}})
            self.assertEqual(error.exception.status_code,422)
    async def test_mongo_validation_and_type_safe_document_ids(self):
        validate_filter({'amount':{'$gt':1}})
        with self.assertRaises(ValueError):validate_filter({'$where':'sleep(10000)'})
        self.assertNotEqual(document_key(ObjectId('000000000000000000000001'),'p:'),document_key('000000000000000000000001','p:'))
        self.assertNotEqual(document_key(1,'p:'),document_key('1','p:'))
        with self.assertRaises(ValueError):wb.Profile(name='bad',product='mongodb',url='mongodb://user:pass@host/db')
        with self.assertRaises(ValueError):wb.Profile(name='bad',product='mongodb',url='mongodb://host/?tlsInsecure=true')
        p=wb.Profile(name='mongo',product='mongodb',url='mongodb://host/db?authSource=admin')
        self.assertFalse(p.observe)

    async def test_interruption_is_unknown_and_recovery_is_idempotent(self):
        self.audit.intent(b'pending')
        self.audit.recover_pending();self.audit.recover_pending()
        self.assertEqual(self.audit.summary()['unknown'],1)
        self.assertEqual(self.audit.summary()['pending'],0)
        self.assertEqual(self.audit.events()['items'][0]['evidence'],'worker-interrupted')

    async def test_retention_preserves_counts_but_removes_names_per_event(self):
        old=Audit(self.db,self.cipher,'old',record_names=True,retention_days=1)
        with patch('backend.audit.time.time',return_value=time.time()-2*86400):
            seq=old.intent(b'expired-name');old.finish(seq,'skipped',duplicate=True)
        seq=self.audit.intent(b'recent');self.audit.finish(seq,'created')
        self.audit.prune()
        self.assertEqual(old.events()['items'],[])
        self.assertEqual(old.summary()['unique_duplicate_keys'],1)
        self.assertEqual(len(self.audit.events()['items']),1)

    async def test_recent_event_view_returns_newest_first(self):
        for key in [b'first',b'last']:
            seq=self.audit.intent(key);self.audit.finish(seq,'created')
        self.assertEqual(self.audit.events(limit=1,latest=True)['items'][0]['key'],'last')
