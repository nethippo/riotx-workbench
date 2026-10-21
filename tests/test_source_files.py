import asyncio
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import main as wb
from backend.source_files import metadata, inspect_file, count_records
from starlette.datastructures import UploadFile


class FileSourceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root/'files').mkdir()
        self.path = self.root/'files'/'file'
        self.path.write_text('id,name\n1,one\n2,two\n')
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('CREATE TABLE objects(kind TEXT,id TEXT,body TEXT,PRIMARY KEY(kind,id)); CREATE TABLE logs(seq INTEGER PRIMARY KEY,run TEXT,body TEXT);')
        self.patches = [patch.object(wb,'db',self.conn),patch.object(wb,'DATA',self.root),patch.object(wb,'file_locks',wb.defaultdict(asyncio.Lock))]
        for p in self.patches:p.start()
        self.job = dict(id='job',kind='file-import',name='file job',file_id='file',options={'type':'csv','header':True},revision=1,watch_file=True,target_id='target')
        wb.put('job', self.job)
        wb.put('file',dict(id='file',name='orders.csv',size=self.path.stat().st_size,created_at=wb.now()))

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.conn.close();self.temp.cleanup()

    async def stable(self):
        await wb.refresh_file(self.job)
        return await wb.refresh_file(self.job)

    async def test_unchanged_poll_never_opens_content_even_after_restart(self):
        result = await self.stable()
        self.assertEqual(result['records'],2)
        with patch.object(wb,'inspect_file',side_effect=AssertionError('reread')):
            for _ in range(5):await wb.refresh_file(self.job)
        self.assertEqual(wb.file_status(self.job)['records'],2)

    async def test_same_size_same_mtime_atomic_replacement_is_detected_by_inode(self):
        old = await self.stable(); before=metadata(self.path)
        replacement=self.path.with_name('replacement')
        replacement.write_text('id,name\n3,new\n4,new\n')
        os.utime(replacement,ns=(before['mtime_ns'],before['mtime_ns']))
        self.assertEqual(replacement.stat().st_size,before['size'])
        os.replace(replacement,self.path)
        await wb.refresh_file(self.job)
        self.assertIsNone(wb.file_status(self.job)['records'])
        new=await wb.refresh_file(self.job)
        self.assertNotEqual(old['fingerprint'],new['fingerprint'])
        self.assertEqual((self.path.parent/old['snapshot_file']).read_text(),'id,name\n1,one\n2,two\n')

    async def test_poll_arms_after_run_and_only_submits_changed_file_once(self):
        await self.stable()
        with patch.object(wb,'capabilities',{'file-import':{'available':True}}),patch.object(wb,'queue_run',new_callable=AsyncMock) as submit:
            self.path.write_text('id,name\n9,new\n')
            await wb.check_files_once();await wb.check_files_once()
            submit.assert_not_awaited() # no first manual run
            w=wb.get_watch(self.job);wb.put('file-watch',w|{'armed':True})
            async def submitted(*args,**kwargs):
                w=wb.get_watch(self.job);wb.put('file-watch',w|{'attempted':w['fingerprint']})
            submit.side_effect=submitted
            await wb.check_files_once();await wb.check_files_once()
            submit.assert_awaited_once_with('job',automatic=True)

    async def test_disabled_watch_and_active_run_defer_replacement(self):
        await self.stable()
        wb.put('file-watch',wb.get_watch(self.job)|{'armed':True})
        self.path.write_text('id,name\n9,new\n')
        wb.put('job',self.job|{'watch_file':False})
        with patch.object(wb,'capabilities',{'file-import':{'available':True}}),patch.object(wb,'queue_run',new_callable=AsyncMock) as submit:
            await wb.check_files_once();await wb.check_files_once()
            submit.assert_not_awaited()
            wb.put('job',self.job)
            wb.put('run',dict(id='busy',job_id='job',status='RUNNING',snapshot=self.job))
            await wb.check_files_once();submit.assert_not_awaited()
            wb.put('run',dict(id='busy',job_id='job',status='SUCCEEDED',snapshot=self.job))
            await wb.check_files_once();submit.assert_awaited_once()

    async def test_deleted_job_does_not_auto_run(self):
        await self.stable()
        wb.put('file-watch',wb.get_watch(self.job)|{'armed':True})
        wb.put('job',self.job|{'deleted_at':wb.now()})
        self.path.write_text('id,name\n9,new\n')
        with patch.object(wb,'queue_run',new_callable=AsyncMock) as submit:
            await wb.check_files_once();await wb.check_files_once();submit.assert_not_awaited()

    async def test_missing_file_recovers_without_old_count(self):
        await self.stable()
        original=self.path.read_text();self.path.unlink()
        await wb.refresh_file(self.job)
        self.assertIsNone(wb.file_status(self.job)['records'])
        self.assertTrue(wb.file_status(self.job)['error'])
        self.path.write_text(original)
        await self.stable();self.assertEqual(wb.file_status(self.job)['records'],2)

    async def test_invalid_count_is_cached_instead_of_reparsed_each_poll(self):
        self.job['options']={'type':'json'};self.path.write_text('{invalid')
        result=await self.stable();self.assertIsNone(result['records']);self.assertTrue(result['count_note'])
        with patch.object(wb,'inspect_file',side_effect=AssertionError('reread')):await wb.refresh_file(self.job)

    async def test_changed_parser_configuration_recounts(self):
        await self.stable();self.job['options']['header']=False
        self.assertEqual((await wb.refresh_file(self.job))['records'],3)

    async def test_api_replacement_is_atomic_and_preserves_queued_input(self):
        old=await self.stable()
        await wb.replace_file('file',UploadFile(io.BytesIO(b'id,name\n7,replaced\n'),filename='next.csv'))
        self.assertEqual(self.path.read_text(),'id,name\n7,replaced\n')
        self.assertIn('1,one',(self.path.parent/old['snapshot_file']).read_text())
        self.assertEqual(wb.get('file','file')['name'],'next.csv')

    async def test_json_jsonl_and_multiline_csv_record_counts(self):
        for text,options,total in [('[{"id":1},{"id":2}]',{'type':'json'},2),('{"id":1}',{'type':'json'},1),
            ('{"id":1}\n\n{"id":2}\n',{'type':'jsonl'},2),('id,name\n1,"two\nlines"\n',{'type':'csv','header':True},1)]:
            self.path.write_text(text);self.assertEqual(count_records(self.path,options,'input')[0],total)

    async def test_copy_aborted_if_source_changes_during_read(self):
        original=__import__('shutil').copyfile
        def changed(source,target):
            original(source,target);self.path.write_text('changed')
        with patch('backend.source_files.shutil.copyfile',side_effect=changed):
            with self.assertRaises(ValueError):inspect_file(self.path,metadata(self.path),self.job['options'],'orders.csv')
        self.assertEqual(list((self.path.parent/'versions').iterdir()),[])

    async def test_symlink_rejected(self):
        real=self.path.with_name('real');self.path.rename(real);self.path.symlink_to(real)
        with self.assertRaises(ValueError):metadata(self.path)

if __name__=='__main__':unittest.main()
