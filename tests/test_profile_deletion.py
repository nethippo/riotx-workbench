import asyncio
import json
import sqlite3
import unittest
from unittest.mock import patch
from cryptography.fernet import Fernet
from fastapi import HTTPException
from backend import main as wb


class ProfileDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.executescript('CREATE TABLE objects(kind TEXT,id TEXT,body TEXT,PRIMARY KEY(kind,id)); CREATE TABLE logs(seq INTEGER PRIMARY KEY,run TEXT,body TEXT);')
        self.patches = [patch.object(wb, 'db', self.db), patch.object(wb, 'cipher', Fernet(Fernet.generate_key())),
                        patch.object(wb, 'subscriber_tasks', {}), patch.object(wb, 'telemetry', {}),
                        patch.object(wb, 'series', {}), patch.object(wb, 'event_totals', {})]
        for p in self.patches: p.start()
        self.profile = wb.save_profile(dict(id='demo', name='demo', password='secret', url='redis://source:6379/0'))
        wb.save_profile(dict(id='other', name='other', password='', url='redis://target:6379/0'))
        for ident in ['job', 'deleted']:
            wb.put('job', dict(id=ident, source_id='demo', target_id='other', revision=2, **({'deleted_at':'yesterday'} if ident=='deleted' else {})))
        wb.put('run', dict(id='history', status='SUCCEEDED', snapshot=dict(source_id='demo', target_id='other')))
        self.db.execute("INSERT INTO logs VALUES (1,'history','{}')");self.db.commit()

    async def asyncTearDown(self):
        for p in reversed(self.patches): p.stop()
        self.db.close()

    async def test_delete_detaches_jobs_preserves_history_and_other_profile(self):
        history=wb.objects('run')
        cancelled=asyncio.Event()
        async def observer():
            try: await asyncio.Event().wait()
            finally: cancelled.set()
        task=asyncio.create_task(observer());await asyncio.sleep(0)
        wb.subscriber_tasks['demo']=task
        wb.telemetry['demo']={};wb.series['demo']=[]
        result=await wb.delete_profile('demo')
        self.assertEqual(set(result['updated_jobs']), {'job','deleted'})
        self.assertTrue(cancelled.is_set())
        self.assertNotIn('demo',wb.subscriber_tasks)
        self.assertNotIn('demo',wb.telemetry)
        self.assertEqual(wb.public_profile('other')['id'],'other')
        for j in wb.objects('job',include_deleted=True):
            self.assertEqual((j['source_id'],j['target_id'],j['revision']), ('','other',3))
        self.assertEqual(wb.objects('run'),history)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM logs').fetchone()[0],1)
        with self.assertRaises(HTTPException) as error: await wb.delete_profile('demo')
        self.assertEqual(error.exception.status_code,404)

    async def test_all_active_snapshot_states_block_deletion(self):
        for status in wb.ACTIVE:
            for field in ['source_id','target_id']:
                wb.put('run',dict(id='busy',status=status,snapshot={field:'demo'}))
                with self.assertRaises(HTTPException) as error: await wb.delete_profile('demo')
                self.assertEqual(error.exception.status_code,409)
                self.assertEqual(wb.public_profile('demo')['id'],'demo')
                self.assertEqual(wb.get('job','job')['source_id'],'demo')

    async def test_transaction_failure_keeps_profile_and_assignments(self):
        self.db.execute("CREATE TRIGGER failure BEFORE DELETE ON objects WHEN OLD.kind='profile' BEGIN SELECT RAISE(ABORT,'failure'); END")
        with self.assertRaises(sqlite3.IntegrityError): await wb.delete_profile('demo')
        self.assertEqual(wb.get('job','job')['source_id'],'demo')
        self.assertEqual(wb.public_profile('demo')['id'],'demo')
