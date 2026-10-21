import asyncio
import json
import sqlite3
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import main as wb
from fastapi import HTTPException


class DeletionTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:')
        self.conn.row_factory=sqlite3.Row
        self.conn.executescript('CREATE TABLE objects(kind TEXT,id TEXT,body TEXT,PRIMARY KEY(kind,id)); CREATE TABLE logs(seq INTEGER PRIMARY KEY,run TEXT,body TEXT);')
        self.dbpatch=patch.object(wb,'db',self.conn);self.dbpatch.start()
        self.parent=dict(id='p',name='parent',workspace='one',kind='replicate',source_id='s',target_id='t',options={},revision=1)
        self.child=dict(id='c',name='child',workspace='one',kind='compare',parent_job_id='p',source_id='s',target_id='t',options={},revision=1)
        self.other=dict(self.parent,id='other',name='unrelated',workspace='two')
        for j in [self.parent,self.child,self.other]:wb.put('job',j)
        wb.put('run',dict(id='history',job_id='c',snapshot=deepcopy(self.child),status='SUCCEEDED'))
        self.conn.execute('INSERT INTO logs VALUES (1,?,?)',('history',json.dumps({'message':'retained log'})));self.conn.commit()

    def tearDown(self):
        self.dbpatch.stop();self.conn.close()

    def preview(self,**kwargs):return wb.deletion_preview(wb.DeletionRequest(**kwargs))
    def delete(self,**kwargs):
        preview=self.preview(**kwargs)
        return asyncio.run(wb.delete_entries(wb.DeletionRequest(**kwargs,expected_ids=[j['id'] for j in preview['jobs']])))
    def restore(self,batch):return asyncio.run(wb.restore_entries(batch['deletion']['id']))

    def test_parent_cascade_and_restore_retain_history_and_logs(self):
        before=wb.objects('run')
        batch=self.delete(scope='job',job_id='p')
        self.assertEqual(set(batch['deleted_ids']),{'p','c'})
        self.assertEqual([j['id'] for j in wb.objects('job')],['other'])
        with self.assertRaises(HTTPException) as e:wb.get('job','p')
        self.assertEqual(e.exception.status_code,404)
        self.assertEqual(wb.objects('run'),before)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM logs').fetchone()[0],1)
        self.restore(batch)
        self.assertEqual(wb.get('job','p'),self.parent)
        self.assertEqual(wb.get('job','c'),self.child)

    def test_child_deletion_does_not_delete_parent(self):
        batch=self.delete(scope='job',job_id='c')
        self.assertEqual(batch['deleted_ids'],['c'])
        self.assertEqual(wb.get('job','p'),self.parent)

    def test_workspace_deletion_preserves_other_workspace(self):
        batch=self.delete(scope='workspace',workspace='one')
        self.assertEqual(set(batch['deleted_ids']),{'p','c'})
        self.assertEqual(wb.get('job','other'),self.other)

    def test_all_active_states_block_whole_batch(self):
        for status in wb.ACTIVE:
            wb.put('run',dict(id='busy',job_id='c',snapshot=self.child,status=status))
            preview=self.preview(scope='job',job_id='p')
            self.assertTrue(preview['blocked'])
            with self.assertRaises(HTTPException) as e:self.delete(scope='job',job_id='p')
            self.assertEqual(e.exception.status_code,409)
            self.assertEqual(len(wb.objects('job')),3)
            self.assertEqual(wb.objects('deletion'),[])

    def test_stale_confirmation_cannot_delete_additional_jobs(self):
        request=wb.DeletionRequest(scope='workspace',workspace='one',expected_ids=['p','c'])
        wb.put('job',dict(self.other,id='new',workspace='one'))
        with self.assertRaises(HTTPException) as e:asyncio.run(wb.delete_entries(request))
        self.assertEqual(e.exception.status_code,409)
        self.assertEqual(len(wb.objects('job')),4)

    def test_parent_must_be_restored_before_separately_deleted_child(self):
        child_batch=self.delete(scope='job',job_id='c')
        parent_batch=self.delete(scope='job',job_id='p')
        with self.assertRaises(HTTPException) as e:self.restore(child_batch)
        self.assertEqual(e.exception.status_code,409)
        self.restore(parent_batch);self.restore(child_batch)
        self.assertEqual(wb.get('job','c'),self.child)
        with self.assertRaises(HTTPException) as e:self.restore(child_batch)
        self.assertEqual(e.exception.status_code,409)

    def test_deleting_all_jobs_does_not_reseed_demo(self):
        self.delete(scope='workspace',workspace='one');self.delete(scope='workspace',workspace='two')
        with patch.dict('os.environ',{'DEMO_ENABLED':'true'}):wb.seed()
        self.assertEqual(wb.objects('job'),[])
        self.assertEqual(len(wb.objects('job',include_deleted=True)),3)
        self.assertEqual(wb.objects('profile'),[])

    def test_transaction_rolls_back_on_storage_failure(self):
        self.conn.execute("CREATE TRIGGER fail_child BEFORE UPDATE ON objects WHEN NEW.id='c' BEGIN SELECT RAISE(ABORT,'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.delete(scope='job',job_id='p')
        self.assertEqual(len(wb.objects('job')),3)
        self.assertEqual(wb.objects('deletion'),[])

    def test_submission_rechecks_deletion_after_async_preflight(self):
        async def preflight(_):
            request=wb.DeletionRequest(scope='job',job_id='p',expected_ids=['p','c'])
            await wb.delete_entries(request)
        with patch.object(wb,'preflight',side_effect=preflight):
            with self.assertRaises(HTTPException) as e:asyncio.run(wb.start('p'))
        self.assertEqual(e.exception.status_code,404)
        self.assertEqual([r['id'] for r in wb.objects('run')],['history'])

if __name__=='__main__':unittest.main(verbosity=2)
