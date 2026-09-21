"""Runner and rendered native provenance enter the real packaged A/B/Git chain."""
import copy
import json
import sys
from pathlib import Path
import subprocess
import unittest
sys.path.insert(0, str(Path(__file__).parent))
import test_registration_context as fixture
import test_workflow_progress as progress_fixture


class EntryChainTests(fixture.RegistrationContextTests):
    def test_runner_transfer_native_chain_keeps_original_host_evidence(self):
        thread = self.request['host']['thread_id']
        self.context['controller_ref'] = thread
        self.registry['entries'] = [{'name':name,'entry':str(fixture.ROOT/'skills'/name/'SKILL.md'),
            'source':'fixture-host','enabled':True} for name in
            ('problem-framing','solution-design','guided-implementation','change-closure')]
        self.context['registry_digest'] = fixture.entry.digest(self.registry); self.save()
        host = {**self.request['host'],'project_path':str(self.repo),'controller_ref':thread,
                'role':'controller','source_ref':None}
        original = {'protocol':fixture.entry.PROTOCOL,'operation':'resolve','stage':1,'action':'entry',
            'host':host,'source':{'kind':'stage1'},'registry_input':str(self.input),
            'target':{'kind':'planning','repository':str(self.repo),'branch':'main'}}
        def call(name, script, request, cwd):
            result = subprocess.run([sys.executable,str(fixture.ROOT/'skills'/name/'scripts'/script)],cwd=cwd,
                env=self.env,input=json.dumps(request),text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            return json.loads(result.stdout)['result']
        def requirement(operation, **kwargs):
            return call('problem-framing','requirement_prepare.py',{'protocol':'requirement-freeze-v2',
                'operation':operation,'entry':original,**kwargs},self.repo)
        pending = requirement('prepare',purpose='write',path='docs/requirement.md',version=1,
                              authorization='controller:write',content='Approved narrow implementation.\n')
        document = requirement('write',intent=pending)
        frozen = requirement('freeze',intent=requirement('prepare',purpose='freeze',path=document['path'],
            version=1,authorization='controller:freeze',previous=document))
        subprocess.run(['git','-C',str(self.flow),'merge','--ff-only','main'],check=True,capture_output=True)
        current = {**original,'stage':2,'host':{**host,'project_path':str(self.flow)},
            'source':{'kind':'frozen','path':document['path']},'target':{'kind':'flow','binding':self.binding}}
        expected = call('solution-design','entry_prepare.py',current,self.flow)
        scope = {'baseline':expected['repository']['head'],'owned_paths':['spec.md'],'protected_paths':['docs/requirement.md'],
                 'implementation_paths':['impl.py'],'closure_paths':['closure.md']}
        configuration = {'role':'solution-designer','required_capability':None,'supported':[
            {'model':'gpt-5.6-sol','effort':'high','capability':None,'cost':None,'permission':'same','visible_identity':'same'}],
            'user':None,'frozen':None,'previous':None,'receipt':'fixture:configuration','can_override':False,
            'inherited':{'model':'gpt-5.6-sol','effort':'high'},'upgrade_attempted':False}
        body = {'protocol':'workflow-stage-transfer-v4','operation':'prepare','entry':current,'expected_entry':expected,
            'stage':2,'role':'solution-designer','requirement':frozen,'predecessor':None,
            'target':{'repository':str(self.repo),'branch':'main'},'delivery':None,'binding':self.binding,'scope':scope,
            'authorization':{'reference':'controller:approved','flow_mode':'continuous','scope_digest':fixture.entry.digest(scope)},
            'configuration':configuration,'semantic':{'objective':'implement','testing_basis':'CLI',
            'completion_criteria':['passed'],'constraints':['local only']}}
        saved = call('solution-design','stage_handoff.py',body,self.flow)
        rendered = call('solution-design','stage_handoff.py',{'protocol':body['protocol'],'operation':'render','handoff':saved},self.flow)
        native = {**current,**rendered['payload']['registration_input'],
            'host':{**current['host'],'role':'solution-designer','source_ref':thread}}
        observed = call('solution-design','entry_prepare.py',native,self.flow)
        self.assertEqual(observed['registration_context']['project_path'],str(self.repo))
        self.assertEqual(observed['repository']['cwd'],str(self.flow))
        runner = progress_fixture.runner
        packages = {name:runner.package_identity(str(fixture.ROOT/'skills'/skill/'SKILL.md'),skill)
            for name,skill in [('runner','guided-implementation'),('stage2','solution-design'),
                              ('stage3','guided-implementation'),('stage4','change-closure')]}
        confirmed = {'registry_input':str(self.input),'registration_context':observed['registration_context'],
            'repository':str(self.repo),'git_common_dir':self.binding['git_common_dir'],'packages':packages}
        runner._check_current_registry(confirmed,[2,3,4])
        self.context['project_id'] = 'foreign'; self.save()
        with self.assertRaises(runner.WorkflowError): runner._check_current_registry(confirmed,[2,3,4])


class RecoveryPriorityTests(unittest.TestCase):
    def setUp(self):
        self.f = progress_fixture.ProgressTests(methodName='runTest'); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.f.begin('continuous'); self.f.invoke('observe',self.f.observation())

    def test_stopped_current_resumability_continues_the_original_identity(self):
        f = self.f
        original = copy.deepcopy(f.state()['dispatch']['request'])
        f.invoke('pause')
        f.invoke('observe',f.observation('stopped','result'))
        f.invoke('resume')
        stopped = f.observation('stopped','result')
        stopped['receipt']['raw']['resumable'] = True
        result = f.invoke('observe',stopped)
        self.assertEqual(result['next_action']['operation'],'continue-host',result)
        self.assertEqual(result['next_action']['payload']['ref'],'native:designer')
        self.assertEqual(original,f.state()['dispatch']['request'])

    def test_host_loss_names_original_identities_and_missing_evidence(self):
        f = self.f; progress = progress_fixture.progress
        outer = progress.read_record(f.checkpoint)
        outer['transport'] = {'instance':'fixture-host','state':'lost','native_event_sequence':0}
        outer['sessions'] = {'stage2':'task'}
        progress.atomic_save(f.checkpoint,outer)
        result = f.invoke('transport-lost',{'instance':'fixture-host','reason':'external owner exited'})
        action = result['next_action']
        self.assertEqual(action['operation'],'await-host-recovery')
        self.assertEqual(action['host_instance'],'fixture-host')
        self.assertEqual(action['carrier_thread'],'task')
        self.assertEqual(action['native_refs'],['native:designer'])
        self.assertIn('same-identity-resumability-or-complete-stop-proof',action['missing'])
        self.assertEqual(f.invoke('resume')['next_action'],action)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite([*(EntryChainTests(name) for name in loader.getTestCaseNames(EntryChainTests)
                              if name in EntryChainTests.__dict__), loader.loadTestsFromTestCase(RecoveryPriorityTests)])
