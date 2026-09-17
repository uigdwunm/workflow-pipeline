"""Different installed processes share the existing ledger and Git locks."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]


class CrossPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.packages = self.root/'packages'
        subprocess.run([sys.executable,str(ROOT/'scripts/build_skills.py'),'--output',str(self.packages)],check=True)
        manifest=self.packages/'change-closure/package.json'
        data=json.loads(manifest.read_text());data['release']='1.0.1';manifest.write_text(json.dumps(data))
        self.project=self.root/'project';self.project.mkdir()
        self.git('init','-q','-b','main')
        self.git('config','user.name','Test');self.git('config','user.email','test@example.com')
        (self.project/'base').write_text('base\n');self.git('add','.');self.git('commit','-qm','base')

    def git(self,*args,cwd=None):
        return subprocess.check_output(['git','-C',str(cwd or self.project),*args],text=True).strip()

    def call(self,name,script,request,operation=None):
        result=subprocess.run([sys.executable,'-I',str(self.packages/name/'scripts'/script),*([operation] if operation else [])],
            input=json.dumps(request),capture_output=True,text=True,cwd=self.project,
            env={key:value for key,value in os.environ.items() if key!='PYTHONPATH'})
        self.assertNotIn('Traceback',result.stderr)
        return json.loads(result.stdout)

    def test_two_package_writers_share_one_ledger_and_reject_stale_revision(self):
        topic=self.call('design-discussion','discussion_protocol.py',{'protocol_version':1,'operation':'bootstrap',
            'project_path':str(self.project),'entry_mode':'explicit-skill','conversation_ref':'owner',
            'idempotency_key':str(uuid.uuid4()),'root_slug':'shared'})
        envelope={'protocol_version':1,'project_path':str(self.project),'project_id':topic['project_id'],
            'tree_id':topic['tree_id'],'actor_topic_id':topic['topic_id'],'actor_conversation_ref':'owner'}
        request={**envelope,'operation':'prepare-topic-update','expected_ledger_revision':1,'expected_topic_revision':1,
            'mutation':{'type':'confirm-decision','summary':'One concurrent decision','rationale':'Same ledger authority'}}
        def writer(name):
            return self.call(name,'discussion_protocol.py',{**request,'idempotency_key':str(uuid.uuid4())})
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(writer,['solution-design','change-closure']))
        self.assertEqual(sum(r['ok'] for r in results),1,results)
        failure=next(r for r in results if not r['ok'])
        self.assertIn(failure['error']['code'],{'stale_revision','ledger_revision_conflict','pending_document_write','document_write_pending'})
        prepared=next(r for r in results if r['ok'])
        applied=self.call('guided-implementation','discussion_protocol.py',{**envelope,'operation':'apply-document-write',
            'expected_ledger_revision':2,'expected_topic_revision':2,'idempotency_key':str(uuid.uuid4()),
            'document_write_id':prepared['document_write_id']})
        self.assertTrue(applied['document_verified'])
        read=self.call('problem-framing','discussion_protocol.py',{**envelope,'operation':'read-topic'})
        self.assertTrue(read['ok'])
        self.assertEqual(read['ledger_revision'],3,read)

    def test_planning_preserves_flow_and_two_package_publications_do_not_double_merge(self):
        def call(name,operation,data):return self.call(name,'supervision_protocol.py',data,operation)
        flow=self.root/'flow'
        binding=call('solution-design','start-worktree',{'repository':str(self.project),'worktree':str(flow),
            'target_branch':'main','branch':'codex/flow'})['binding']
        (flow/'plan.md').write_text('Frozen plan\n');self.git('add','plan.md',cwd=flow);self.git('commit','-qm','plan',cwd=flow)
        plan=self.git('rev-parse','HEAD',cwd=flow)
        published=call('solution-design','publish-planning',{'binding':binding,'planning_commit':plan,'allowed_paths':['plan.md'],'protected_paths':[]})
        self.assertTrue(published['ok'],published);self.assertTrue(flow.is_dir())
        expected=self.git('rev-parse','HEAD')
        (flow/'implementation.py').write_text('enabled = True\n');self.git('add','implementation.py',cwd=flow);self.git('commit','-qm','candidate',cwd=flow)
        candidate=self.git('rev-parse','HEAD',cwd=flow)
        self.assertEqual(self.git('rev-parse','HEAD'),expected)
        request={'binding':binding,'candidate_commit':candidate,'expected_target_head':expected,'scope_base_commit':expected,
            'allowed_paths':['implementation.py'],'protected_paths':['plan.md']}
        other=self.root/'other-flow'
        other_binding=call('solution-design','start-worktree',{'repository':str(self.project),'worktree':str(other),
            'target_branch':'main','branch':'codex/other-flow'})['binding']
        (other/'implementation.py').write_text('enabled = True\n');self.git('add','implementation.py',cwd=other);self.git('commit','-qm','other candidate',cwd=other)
        second={**request,'binding':other_binding,'candidate_commit':self.git('rev-parse','HEAD',cwd=other)}
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda pair:call(pair[0],'complete-worktree',pair[1]),[('change-closure',request),('solution-design',second)]))
        self.assertEqual(sum(r['ok'] for r in results),1,results)
        failed=next(r for r in results if not r['ok'])
        self.assertEqual(failed['error']['code'],'target_changed')
        self.assertEqual(sum(p.exists() for p in (flow,other)),1)
        self.assertEqual(self.git('rev-list','--count','--merges',expected+'..HEAD'),'1')
        self.assertEqual((self.project/'implementation.py').read_text(),'enabled = True\n')
        self.assertEqual(len(self.git('branch','--list','codex/*').splitlines()),1)


if __name__=='__main__':unittest.main()
