import sys
import unittest
from copy import deepcopy
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.inheritance import effective_job, comparison_parent

SUPPORTED = ['--key-pattern', '--key-include', '--key-exclude', '--key-type', '--key-slot',
             '--ttl-tolerance', '--key-proc', '--no-ttl', '--read-threads', '--batch',
             '--threads', '--quick', '--dry-run', '--metrics', '--progress']

class InheritanceTests(unittest.TestCase):
    def setUp(self):
        self.parent = dict(id='parent', name='LIVE', kind='replicate', workspace='demo',
                           source_id='source', target_id='target', revision=3,
                           options={'mode':'LIVE','struct':True,'compare':'FULL','key-pattern':'orders:*',
                                    'key-exclude':['orders:private:*'],'ttl-tolerance':'2s',
                                    'read-threads':4,'key-proc':'mapped:#{key}'})
        self.child = dict(id='child', name='comparison', kind='compare', workspace='demo',
                          parent_job_id='parent', source_id='old',target_id='other', revision=1,
                          options={'key-pattern':'WRONG:*','quick':True}, arguments=['ignored'])

    def resolve(self):
        return effective_job(self.child,[self.parent,self.child],SUPPORTED)

    def test_parent_settings_override_child_and_preserve_saved_objects(self):
        before = deepcopy([self.parent,self.child])
        resolved = self.resolve()
        self.assertEqual(resolved['source_id'],'source')
        self.assertEqual(resolved['target_id'],'target')
        self.assertEqual(resolved['options']['key-pattern'],'orders:*')
        self.assertEqual(resolved['options']['key-exclude'],['orders:private:*'])
        self.assertEqual(resolved['options']['ttl-tolerance'],'2s')
        self.assertEqual(resolved['options']['key-proc'],'mapped:#{key}')
        self.assertFalse(resolved['options']['quick'])
        self.assertEqual(resolved['arguments'],[])
        self.assertEqual([self.parent,self.child],before)

    def test_new_submission_reflects_parent_edits_without_mutating_old_snapshot(self):
        old = self.resolve()
        self.parent.update(source_id='new-source', workspace='moved', revision=4)
        self.parent['options'].update({'key-pattern':'new:*','compare':'QUICK'})
        new = self.resolve()
        self.assertEqual(new['source_id'],'new-source')
        self.assertEqual(new['workspace'],'moved')
        self.assertEqual(new['options']['key-pattern'],'new:*')
        self.assertTrue(new['options']['quick'])
        self.assertEqual(new['inherited_from']['revision'],4)
        self.assertEqual(old['options']['key-pattern'],'orders:*')
        self.assertEqual(old['inherited_from']['revision'],3)

    def test_replication_and_runtime_only_flags_not_forwarded(self):
        self.parent['options'].update({'metrics':True,'progress':'ASCII','dry-run':True})
        result = self.resolve()['options']
        for flag in ['mode','struct','compare','metrics','progress','dry-run']:
            self.assertNotIn(flag,result)

    def test_default_quick_and_none_disabled(self):
        self.parent['options'].pop('compare')
        self.assertTrue(self.resolve()['options']['quick'])
        self.parent['options']['compare']='NONE'
        self.assertTrue(self.resolve()['comparison_disabled'])

    def test_removed_parent_does_not_fall_back_to_stale_child_settings(self):
        result=effective_job(self.child,[],SUPPORTED)
        self.assertIn('inheritance_error',result)

    def test_legacy_unique_matching_and_ambiguity(self):
        self.child.pop('parent_job_id')
        self.child.update(source_id='source',target_id='target')
        self.assertEqual(comparison_parent(self.child,[self.parent]),self.parent)
        second={**self.parent,'id':'second'}
        self.assertIsNone(comparison_parent(self.child,[self.parent,second]))

    def test_unlinked_standalone_compare_preserves_own_settings(self):
        self.child.pop('parent_job_id')
        self.assertEqual(effective_job(self.child,[],SUPPORTED),self.child)

if __name__ == '__main__':
    unittest.main()
