"""Deployment command safety checks; stubs never start containers or call AWS."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class DeployScriptsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for directory in ['scripts/deploy','deploy/docker','infra/ecr','infra/ecs','bin']:
            (self.root/directory).mkdir(parents=True,exist_ok=True)
        for name in ['docker.sh','ecs.sh']:
            shutil.copy2(ROOT/'scripts/deploy'/name,self.root/'scripts/deploy'/name)
        shutil.copy2(ROOT/'deploy/docker/.env.example',self.root/'deploy/docker/.env.example')
        self.log=self.root/'calls.jsonl'
        self.env=os.environ|{'PATH':str(self.root/'bin')+os.pathsep+os.environ['PATH'],'DEPLOY_CALL_LOG':str(self.log)}
        self.stub('docker','')
        self.stub('terraform','')
        self.stub('aws','')

    def tearDown(self):self.tmp.cleanup()

    def stub(self,name,extra):
        p=self.root/'bin'/name
        p.write_text('#!/usr/bin/env python3\nimport json,os,sys\nwith open(os.environ["DEPLOY_CALL_LOG"],"a") as f:f.write(json.dumps([sys.argv[0].split("/")[-1],*sys.argv[1:]])+"\\n")\n'+extra+'\n')
        p.chmod(0o755)

    def run_script(self,name,*args,**env):
        return subprocess.run(['bash',str(self.root/'scripts/deploy'/name),*args],env=self.env|env,text=True,capture_output=True)

    def calls(self):return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_init_generates_private_token_without_overwriting_existing_config(self):
        result=self.run_script('docker.sh','init');self.assertEqual(result.returncode,0,result.stderr)
        path=self.root/'deploy/docker/.env';original=path.read_text()
        self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
        self.assertRegex(original,r'WORKBENCH_TOKEN=[0-9a-f]{64}\n')
        self.assertEqual(self.run_script('docker.sh','init').returncode,0)
        self.assertEqual(path.read_text(),original)
        self.assertEqual(self.calls(),[])

    def test_stop_retains_named_volumes(self):
        (self.root/'deploy/docker/.env').write_text('WORKBENCH_TOKEN='+'a'*64+'\n')
        result=self.run_script('docker.sh','stop');self.assertEqual(result.returncode,0,result.stderr)
        args=self.calls()[0];self.assertEqual(args[-1],'down');self.assertNotIn('-v',args);self.assertNotIn('--volumes',args)

    def test_up_rejects_short_token_before_any_docker_call(self):
        (self.root/'deploy/docker/.env').write_text('WORKBENCH_TOKEN=CHANGE_ME\n')
        self.assertNotEqual(self.run_script('docker.sh','up').returncode,0)
        self.assertEqual(self.calls(),[])

    def test_apply_requires_saved_plan(self):
        self.assertNotEqual(self.run_script('ecs.sh','apply').returncode,0)
        self.assertEqual(self.calls(),[])

    def test_failed_validation_never_reaches_plan_or_apply(self):
        (self.root/'infra/ecs/terraform.tfvars').write_text('')
        old_plan=self.root/'infra/ecs/deployment.tfplan';old_plan.write_text('stale plan')
        self.stub('terraform','if "validate" in sys.argv:sys.exit(1)')
        result=self.run_script('ecs.sh','plan');self.assertNotEqual(result.returncode,0)
        self.assertFalse(any('plan' in x or 'apply' in x for x in self.calls()))
        self.assertFalse(old_plan.exists())

    def test_saved_plan_is_the_only_apply_input(self):
        (self.root/'infra/ecs/deployment.tfplan').write_text('test stub')
        result=self.run_script('ecs.sh','apply');self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.calls()[0][-2:],['apply','deployment.tfplan'])

    def test_failed_image_push_preserves_previous_deployment_digest(self):
        p=self.root/'infra/ecs/image.auto.tfvars.json';p.write_text('{"container_image":"previous-digest"}')
        self.stub('terraform','if "repository_url" in sys.argv:print("123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/test")\nelif "region" in sys.argv:print("ap-northeast-2")')
        self.stub('aws','print("stub-test-password")')
        self.stub('docker','if "login" in sys.argv:sys.stdin.read()\nif "buildx" in sys.argv:sys.exit(2)')
        result=self.run_script('ecs.sh','image',IMAGE_TAG='test-1')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(json.loads(p.read_text())['container_image'],'previous-digest')
        self.assertFalse(any('describe-images' in x for x in self.calls()))

if __name__=='__main__':unittest.main()
