import json
import runpy
import threading
import unittest
from pathlib import Path

module = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts' / 'ttl-load-generator.py'))
Config, run_burst = module['Config'], module['run_burst']

class Clock:
    def __init__(self): self.time=0; self.deadlines=[]
    def monotonic(self): return self.time
    def wait(self, seconds):
        self.time+=seconds; self.deadlines.append(self.time); return False

class Pipeline:
    def __init__(self, client): self.client=client; self.commands=[]
    def __enter__(self): return self
    def __exit__(self,*_): pass
    def set(self,key,value,**kwargs): self.commands.append((key,value,kwargs)); return self
    def execute(self):
        result=[]
        for key,value,kwargs in self.commands:
            if key in self.client.rows: result.append(False)
            else:
                self.client.rows[key]=(value,kwargs); result.append(True)
        self.client.batch_sizes.append(len(result)); return result

class Client:
    def __init__(self): self.rows={};self.batch_sizes=[]
    def pipeline(self,transaction):
        if transaction: raise AssertionError('No large MULTI transaction')
        return Pipeline(self)

class LoadTests(unittest.TestCase):
    def run_with(self, config, client=None, wait=None):
        client=client or Client();clock=Clock();progress=[]
        result=run_burst(client,config,threading.Event(),1,'test',progress.append,
                         monotonic=clock.monotonic,wait=wait or clock.wait)
        return client,clock,progress,result

    def test_10000_unique_keys_are_paced_over_five_seconds_with_atomic_ttl(self):
        client,clock,progress,result=self.run_with(Config())
        self.assertEqual(len(client.rows),10000)
        self.assertEqual(result['confirmed_keys'],10000)
        self.assertTrue(result['complete'])
        self.assertAlmostEqual(result['duration_seconds'],5)
        self.assertAlmostEqual(clock.deadlines[0],.05)
        self.assertEqual(client.batch_sizes,[100]*100)
        self.assertEqual(progress[-1],10000)
        for key,(value,options) in client.rows.items():
            self.assertTrue(key.startswith('workbench:ttl-load:test:000001:'))
            self.assertEqual(options,{'ex':60,'nx':True})
            data=json.loads(value);self.assertEqual(len(data['value']),128)
            self.assertEqual(data['cycle'],1)

    def test_partial_last_batch_keeps_total_count_and_window(self):
        client,clock,_,result=self.run_with(Config(keys=101,batch=40))
        self.assertEqual(client.batch_sizes,[40,40,21])
        self.assertEqual(result['confirmed_keys'],101)
        self.assertAlmostEqual(clock.time,5)

    def test_graceful_stop_does_not_write_a_batch(self):
        client,_,_,result=self.run_with(Config(),wait=lambda _:True)
        self.assertEqual(client.rows,{})
        self.assertFalse(result['complete'])

    def test_existing_key_is_never_overwritten(self):
        client=Client();key='workbench:ttl-load:test:000001:00000'
        client.rows[key]=('original',{'ex':100})
        with self.assertRaises(RuntimeError):self.run_with(Config(keys=1),client)
        self.assertEqual(client.rows[key],('original',{'ex':100}))

if __name__=='__main__':unittest.main(verbosity=2)
