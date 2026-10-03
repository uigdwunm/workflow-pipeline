"""Observable workflow controller plans through its JSON process boundary."""
import json
import copy
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CLI = Path(__file__).resolve().parents[2] / "src/shared/scripts/workflow_control.py"
sys.path.insert(0, str(CLI.parent))
import workflow_control_git as control_git
import workflow_control as control
import execution_plan

def takeover_proof(ref, attempt, stage=0, model='supported', effort='high'):
    """Controlled host adapter evidence, never a claim of real tool authentication."""
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    provenance = {'call_ref': 'stop-call:' + ref, 'response_ref': 'stop-response:' + ref, 'action_id': 'stop-action:' + ref}
    receipt = {'adapter': 'fixture-host', 'receipt_ref': 'raw-stop:' + ref, 'request_digest': 'fixture-dispatch:' + attempt,
        'attempt': attempt, 'role': 'dedicated-discussion' if stage == 0 else 'dedicated-problem-framing',
        'event': 'lookup', 'status': 'stopped', 'ref': ref, 'pending_id': None,
        'configuration': {'model': model, 'effort': effort}, 'raw': {'threadId': ref, 'status': 'stopped'}}
    observation = {'event_id': 'observed:' + ref, 'receipt': receipt, 'action_id': provenance['action_id'],
        'provenance': provenance, 'action_resolution': None}
    calls = {'invocations': {digest([receipt['adapter'], provenance['call_ref']]): provenance['action_id']},
        'responses': {digest([receipt['adapter'], provenance['response_ref']]): digest({'provenance': provenance,
            'receipt': {key:value for key,value in receipt.items() if key != 'receipt_ref'}, 'action_resolution': None})},
        'unresolved': []}
    return {'ref': ref, 'attempt': attempt, 'adapter': receipt['adapter'], 'host_ref': 'fixture-host-instance',
        'invocation_id': provenance['call_ref'], 'response_id': provenance['response_ref'], 'stop_receipt': observation,
        'business_calls': calls, 'business_calls_digest': digest(calls)}

def plan_for(cwd):
    def item(identity, category):
        return {'id':identity, 'category':category, 'command':'python3 -m unittest '+identity,
                'cwd':str(cwd), 'pass_condition':'exit 0', 'allowed_skips':[], 'environment':None}
    return {'review_required':[item('focused','focused'),item('affected','affected')],
            'final_required':[item('full','full')], 'environment_not_applicable':'isolated test fixture'}


def checks_for(required, candidate, status='passed'):
    return [dict(item, status=status, exit_code=0 if status == 'passed' else 1,
                 start_commit=candidate, end_commit=candidate, environment_fingerprint=None,
                 output_ref='fixture:output:'+item['id'], output_digest='a'*64) for item in required]


def verification_fixture(candidate, dispatcher, cwd, target):
    # Only external command/reviewer observations are synthetic; consumers still validate them.
    import hashlib
    def digest(value):
        return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    plan = plan_for(cwd)
    current_plan = {'revision': 1, 'authority_digest': '1' * 64,
                    'plan_digest': '2' * 64, 'contract_digest': '3' * 64}
    review = {axis:{'candidate':candidate,'expected_target_head':target,'plan_digest':digest(plan),
              'plan_authority': current_plan, 'reviewer_ref':axis,'status':'accepted',
              'result_ref':'terminal-'+axis} for axis in ('standards','spec')}
    native = {axis:{'adapter':'fixture','call_ref':'review-'+axis,'response_ref':'terminal-'+axis,
                    'status':'stopped','ref':axis,'raw':{'result':'accepted'}} for axis in review}
    decision = {'controller_ref':'controller','reference':'fixture:convergence','review':review,'native_evidence':native}
    checks = checks_for(plan['final_required'],candidate)
    source = {'adapter':'fixture','call_ref':'command','response_ref':'command-result',
              'raw':{'attempt_id':'fixture-final','checks':checks,'source_unchanged':True,'stopped':True,'dispatcher_ref':dispatcher}}
    verification = {'candidate':candidate,'expected_target_head':target,'validation_plan':plan,'plan_digest':digest(plan),
                    'review_digest':digest(decision),'review_decision':decision,'dispatcher_ref':dispatcher,
                    'plan_authority':current_plan}
    verification['attempts'] = [{k:verification[k] for k in ('candidate','expected_target_head','plan_digest',
        'plan_authority','review_digest','dispatcher_ref')} |
        {'attempt_id':'fixture-final','carrier_attempt':'fixture-carrier','required':plan['final_required'],
         'state':'passed','checks':checks,'source':source,'source_snapshot':{}}]
    latest = verification['attempts'][-1]
    latest['result'] = {k:latest[k] for k in ('attempt_id','checks','source','source_snapshot')}
    return review, verification


class WorkflowControlTests(unittest.TestCase):
    def test_revision_carries_unchanged_step_with_current_flat_ref(self):
        authority, old_plan, refs = self.execution_plan_fixture(['src/a.py'])
        revised = copy.deepcopy(authority)
        revised['revision'] = 2
        catalog = revised['execution_plan']['catalog']
        ticket = catalog['tickets'][0]
        ticket['plan_version'] = 2
        ticket['source'].update(plan_version=2, sha256='5' * 64)
        catalog['plan_digest'] = control.authority_digest({
            key: value for key, value in catalog.items() if key != 'plan_digest'})
        revised['execution_plan'] = execution_plan.authority_envelope(catalog, 1,
            input_digest=authority['execution_plan']['sources'][0]['input_digest'])
        original = {'state': 'accepted', 'stopped': True, 'plan_authority': old_plan,
            'plan_refs': refs, 'paths': ['src/a.py'], 'agent_ref': 'native:agent',
            'task_id': 'original', 'allocation_digest': 'allocation',
            'file_hashes': {'src/a.py': 'f' * 64},
            'git_result_snapshot': {'src/a.py': 'f' * 64}}
        retained = execution_plan.classify_retained_results(authority, revised,
            [original], [], None, {'files': {'src/a.py': 'f' * 64}},
            {'src/a.py': None}, [])
        self.assertEqual(len(retained['carried_results']), 1)
        carried = retained['carried_results'][0]
        self.assertEqual(carried['plan_refs'], [execution_plan.step_ref(
            ticket, ticket['plan']['steps'][0])])
        self.assertEqual(carried['plan_authority']['revision'], 2)
        self.assertEqual(original['plan_refs'], refs)
        forged = copy.deepcopy(original)
        forged['plan_refs'][0]['source_sha256'] = '0' * 64
        with self.assertRaises(execution_plan.entry.PreparationError):
            execution_plan.classify_retained_results(authority, revised,
                [forged], [], None, {'files': {'src/a.py': 'f' * 64}},
                {'src/a.py': None}, [])

    def execution_plan_fixture(self, allowed_paths, verification_command='CLI test'):
        step = {'id': 'step-a', 'target': {'path': 'src/a.py', 'symbol': 'run', 'operation': 'add'},
                'capability': 'implement behavior a', 'approach': ['implement run'],
                'inputs': ['approved brief'], 'outputs': ['observable result'],
                'failures': ['invalid input is reported'], 'depends_on': [],
                'verification_refs': ['V-a'], 'read_only': []}
        ticket_source = {'kind': 'inline', 'commit': None, 'sha256': '4' * 64,
                         'plan_version': 1}
        ticket = {'ticket_id': 'INLINE-TEST', 'plan_version': 1, 'source': ticket_source,
                  'plan': {'blocked_by': [], 'steps': [step], 'artifact_steps': [],
                           'verifications': [{'id': 'V-a', 'behavior': 'behavior a',
                                              'seam': 'pure control JSON boundary',
                                              'commands': [verification_command]}]},
                  'covered_paths': ['src/a.py']}
        catalog = {'schema_version': 1, 'commit': None, 'tickets': [ticket],
                   'ordered_step_ids': ['step-a'], 'contract_digest': '3' * 64}
        catalog['plan_digest'] = control.authority_digest(catalog)
        authority = {'state': 'sealed', 'revision': 1,
                     'execution_plan': execution_plan.authority_envelope(catalog, 1,
                         input_digest='9' * 64),
                     'scope': {'implementation_paths': list(allowed_paths),
                               'protected_paths': ['docs/draft.md'],
                               'downstream': {'manifest': []}}}
        summary = {'revision': 1, 'authority_digest': control.authority_digest(authority),
                   'plan_digest': catalog['plan_digest'], 'contract_digest': catalog['contract_digest']}
        refs = [execution_plan.step_ref(ticket, step)]
        return authority, summary, refs

    def test_planning_commit_preserves_nonplanning_index_flags(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            def git(*arguments):
                return subprocess.run(['git', '-C', str(root), *arguments],
                    check=True, capture_output=True).stdout.decode().strip()
            git('init', '-q')
            git('config', 'user.email', 'fixture@example.test')
            git('config', 'user.name', 'Fixture')
            (root / 'impl.py').write_text('print("fixed")\n')
            (root / 'planning.md').write_text('old plan\n')
            git('add', 'impl.py', 'planning.md')
            git('commit', '-qm', 'baseline')
            baseline = git('rev-parse', 'HEAD')
            authority = {'kind': 'staged', 'candidate_commit': baseline,
                'scope': {'implementation_paths': ['impl.py'],
                          'closure_paths': ['planning.md']}}
            stopped = control_git.plan_revision_snapshot(root, authority,
                ['planning.md'], ['planning.md'])
            (root / 'planning.md').write_text('revised plan\n')
            git('add', 'planning.md')
            git('commit', '-qm', 'controller planning only')
            planning_commit = git('rev-parse', 'HEAD')
            for flag, inverse in (('--assume-unchanged', '--no-assume-unchanged'),
                                  ('--skip-worktree', '--no-skip-worktree')):
                with self.subTest(flag=flag):
                    git('update-index', flag, 'impl.py')
                    with self.assertRaises(control_git.ControlError):
                        control_git.verify_plan_revision(root, stopped, planning_commit)
                    git('update-index', inverse, 'impl.py')
            verified = control_git.verify_plan_revision(root, stopped, planning_commit)
            self.assertEqual(verified['changed_paths'], ['planning.md'])

    def test_planning_snapshot_rejects_unmerged_index_stages(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            def git(*arguments, check=True):
                return subprocess.run(['git', '-C', str(root), *arguments],
                    check=check, capture_output=True).stdout.decode().strip()
            git('init', '-q')
            git('config', 'user.email', 'fixture@example.test')
            git('config', 'user.name', 'Fixture')
            (root / 'impl.py').write_text('base\n')
            (root / 'planning.md').write_text('plan\n')
            git('add', 'impl.py', 'planning.md')
            git('commit', '-qm', 'baseline')
            git('branch', 'other')
            (root / 'impl.py').write_text('main\n')
            git('commit', '-qam', 'main edit')
            baseline = git('rev-parse', 'HEAD')
            git('checkout', '-q', 'other')
            (root / 'impl.py').write_text('other\n')
            git('commit', '-qam', 'other edit')
            git('checkout', '-q', '-')
            git('merge', 'other', check=False)
            self.assertTrue(git('ls-files', '-u', '-z'))
            authority = {'kind': 'staged', 'candidate_commit': baseline,
                'scope': {'implementation_paths': ['impl.py'],
                          'closure_paths': ['planning.md']}}
            with self.assertRaises(control_git.ControlError):
                control_git.plan_revision_snapshot(root, authority,
                    ['planning.md'], ['planning.md'])

    def call(self, action, evidence=None, context=None, schema_version=6):
        request = {'schema_version': schema_version, 'action': action,
                   'actor_ref': 'controller', 'context': context or self.context(),
                   'evidence': evidence or {}}
        result = subprocess.run([sys.executable, str(CLI)], input=json.dumps(request),
                                text=True, capture_output=True)
        self.assertTrue(result.stdout, result.stderr)
        return json.loads(result.stdout)

    def binding(self):
        return {'repository': '/tmp/repository', 'worktree': '/tmp/flow', 'git_common_dir': '/tmp/repository/.git',
                'branch': 'codex/flow', 'target_branch': 'main', 'base_commit': 'a' * 40,
                'lifecycle': {'origin': 'created', 'manager': 'git', 'disposition': 'delete',
                    'evidence_ref': 'fixture:script-created', 'creation_receipt': {
                        'worktree': [1, 2], 'gitdir': '/tmp/repository/.git/worktrees/flow',
                        'gitdir_identity': [1, 3], 'branch': 'codex/flow', 'base_commit': 'a' * 40}}}

    def configuration(self, role='dedicated-discussion'):
        return {'role': role, 'required_capability': 2,
                'supported': [{'model': 'supported', 'effort': 'high', 'capability': 3,
                               'cost': None, 'permission': 'same', 'visible_identity': 'same'}],
                'user': {'model': 'supported', 'effort': 'high'}, 'frozen': None,
                'previous': None, 'receipt': 'current tool', 'can_override': True,
                'inherited': None, 'upgrade_attempted': False}

    def context(self):
        return {'schema_version': 6, 'controller_ref': 'controller', 'topic_ref': None,
                'stage': 0, 'carrier': None,
                'preference': {'topic_current': False, 'stage_current': False},
                'flow_authority': None, 'requirement_identity': {'path': 'docs/draft.md',
                'sha256': 'a' * 64, 'version': 1}, 'handoff_progress': None}

    def test_previous_control_schema_requires_its_original_runtime(self):
        current = self.context()
        previous = copy.deepcopy(current)
        previous['schema_version'] = 5
        rejected_context = self.call('select-configuration', {}, previous)
        rejected_request = self.call('select-configuration', {}, current, schema_version=5)
        self.assertIn('legacy_run_requires_original_runtime', rejected_context['error'])
        self.assertIn('legacy_run_requires_original_runtime', rejected_request['error'])
        self.assertEqual(previous['schema_version'], 5)

    def test_preparation_freezes_one_plan_without_creating_a_task(self):
        result = self.call('prepare', {'target': 'local', 'project': 'project',
            'title': 'Discuss', 'missing_context': [],
            'configuration': self.configuration(),
            'next_step': 'discuss', 'archive_ref': None, 'gate_open': True})
        self.assertTrue(result['ok'])
        self.assertEqual(result['plan']['task_count'], 1)
        self.assertEqual(result['plan']['configuration']['model'], 'supported')
        self.assertEqual(result['effects'], [])
        self.assertEqual(result['context']['handoff_progress']['state'], 'prepared')

    def test_confirm_is_once_and_cancelled_attempt_cannot_bind(self):
        evidence = {'target': 'local', 'project': 'project', 'title': 'Discuss',
            'missing_context': [], 'configuration': self.configuration(),
            'next_step': 'discuss', 'archive_ref': None, 'gate_open': True}
        prepared = self.call('prepare', evidence)
        ctx = prepared['context']
        confirmation = {'plan_id': prepared['plan']['plan_id'], 'intent': 'confirm'}
        created = self.call('decide', confirmation, ctx)
        self.assertTrue(created['ok'])
        self.assertEqual(created['effects'][0]['operation'], 'create_thread')
        replay = self.call('decide', confirmation, created['context'])
        self.assertEqual(replay['effects'], [])
        cancelled = self.call('cancel', {}, created['context'])
        late = self.call('creation-result', {'status': 'ready', 'ref': 'late',
            'attempt': created['context']['carrier']['attempt']}, cancelled['context'])
        self.assertFalse(late['ok'])

    def bound(self):
        prepared = self.call('prepare', {'target': 'local', 'project': 'project', 'title': 'Discuss',
            'missing_context': [], 'configuration': self.configuration(),
            'next_step': 'stage2', 'archive_ref': None, 'gate_open': True})
        confirmed = self.call('decide', {'plan_id': prepared['plan']['plan_id'], 'intent': 'confirm'}, prepared['context'])
        return self.call('creation-result', {'status': 'ready', 'ref': 'old',
            'attempt': confirmed['context']['carrier']['attempt']}, confirmed['context'])['context']

    def test_accept_then_successor_ready_then_archive_and_reconcile(self):
        ctx = self.bound()
        delivery = {'delivery_id': 'delivery', 'source_ref': 'old', 'attempt': ctx['carrier']['attempt'],
                    'requirement_identity': {**ctx['requirement_identity'], 'version': 2, 'sha256': 'f' * 64}, 'commit': 'b' * 40,
                    'verified_commit_hash': 'f' * 64}
        received = self.call('receive', delivery, ctx)
        self.assertTrue(received['ok'])
        self.assertFalse(self.call('archive', {}, received['context'])['ok'])
        accepted = self.call('accept', {'delivery_digest': received['delivery_digest']}, received['context'])
        self.assertTrue(accepted['ok'])
        replay = self.call('receive', delivery, accepted['context'])
        self.assertTrue(replay['acknowledged'])
        takeover = {'ref': 'successor', 'stage': 2,
            'input_digest': received['delivery_digest'], 'role': 'solution-designer',
            'binding_verified': True, 'activated': False, 'confirmed': True, 'archive_ref': 'old',
            'takeover_proof': takeover_proof('old', ctx['carrier']['attempt'])}
        missing_proof = {k:v for k,v in takeover.items() if k != 'takeover_proof'}
        self.assertFalse(self.call('successor-ready', missing_proof, accepted['context'])['ok'])
        wrong_proof = copy.deepcopy(takeover)
        wrong_proof['takeover_proof']['ref'] = 'other-task'
        self.assertFalse(self.call('successor-ready', wrong_proof, accepted['context'])['ok'])
        for obstruction in ('boolean-only', 'no-raw', 'running', 'unresolved', 'wrong-cause'):
            invalid = copy.deepcopy(takeover)
            proof = invalid['takeover_proof']
            if obstruction == 'boolean-only':
                proof['stop_receipt'] = {'stopped': True}
            elif obstruction == 'no-raw':
                proof['stop_receipt']['receipt']['raw'] = {}
            elif obstruction == 'running':
                proof['stop_receipt']['receipt']['status'] = 'running'
            elif obstruction == 'unresolved':
                proof['business_calls']['unresolved'] = [{'action_id': 'pending-write'}]
            else:
                proof['stop_receipt']['provenance']['call_ref'] = 'unrelated-call'
            self.assertFalse(self.call('successor-ready', invalid, accepted['context'])['ok'], obstruction)
        ready = self.call('successor-ready', takeover, accepted['context'])
        self.assertTrue(ready['ok'], ready)
        self.assertEqual(ready['context']['handoff_progress']['state'], 'handoff-complete')
        self.assertTrue(self.call('successor-ready', takeover, ready['context'])['acknowledged'])
        conflicting = {**takeover, 'ref': 'another-successor'}
        self.assertFalse(self.call('successor-ready', conflicting, ready['context'])['ok'])
        selector = {'handoff_id': ready['handoff_id']}
        archive = self.call('archive', selector, ready['context'])
        self.assertEqual(archive['effects'][0]['operation'], 'archive')
        self.assertEqual(self.call('archive', selector, archive['context'])['effects'], [])
        self.assertFalse(self.call('archive', {'handoff_id': 'unknown'}, archive['context'])['ok'])
        self.assertFalse(self.call('archive', {**selector, 'control_plan_id': 'wrong'}, archive['context'])['ok'])
        def receipt(intent, status):
            return {**selector, 'operation_id': intent['effects'][0]['operation_id'], 'ref': 'old', 'status': status,
                'receipt': {'adapter': 'fixture', 'invocation_id': 'call-' + intent['effects'][0]['operation_id'],
                    'response_id': 'response-' + intent['effects'][0]['operation_id'], 'ref': 'old',
                    'raw': {'status': status}, 'no_write': False}}
        def issue(intent):
            sent = receipt(intent, 'issued')
            sent['receipt']['raw'] = {key: intent['effects'][0][key] for key in ('operation_id', 'operation', 'ref')}
            return self.call('archive-result', sent, intent['context'])['context']
        self.assertFalse(self.call('archive-result', receipt(archive, 'archived'), archive['context'])['ok'])
        unknown = self.call('archive-result', receipt(archive, 'unknown'), issue(archive))
        query = self.call('archive', selector, unknown['context'])
        self.assertEqual(query['effects'][0]['operation'], 'read-archive-state')
        self.assertEqual(self.call('archive', selector, query['context'])['effects'], [])
        issued_query = issue(query)
        delayed = self.call('archive-result', receipt(archive, 'archived'), issued_query)
        stale_read = self.call('archive-result', receipt(query, 'not-archived'), delayed['context'])
        self.assertEqual(stale_read['context']['retired_handoffs'][ready['handoff_id']]['archive_status'], 'unknown')
        self.assertEqual(self.call('archive', selector, stale_read['context'])['effects'][0]['operation'], 'read-archive-state')
        done = self.call('archive-result', receipt(query, 'archived'), issued_query)
        self.assertEqual(done['context']['retired_handoffs'][ready['handoff_id']]['archive_status'], 'archived')
        self.assertEqual(self.call('archive', selector, done['context'])['effects'], [])
        self.assertTrue(self.call('archive-result', receipt(query, 'archived'), done['context'])['acknowledged'])
        conflict = self.call('archive-result', receipt(archive, 'archived'), done['context'])
        self.assertFalse(conflict['ok'])
        self.assertEqual(conflict['context']['retired_handoffs'][ready['handoff_id']]['archive_status'], 'archived')
        self.assertEqual(len(conflict['context']['retired_handoffs'][ready['handoff_id']]['conflicts']), 1)

    def test_failed_archive_readback_cannot_authorize_another_write(self):
        ctx = self.bound()
        delivery = {'delivery_id': 'delivery', 'source_ref': 'old', 'attempt': ctx['carrier']['attempt'],
                    'requirement_identity': {**ctx['requirement_identity'], 'version': 2, 'sha256': 'f' * 64},
                    'commit': 'b' * 40, 'verified_commit_hash': 'f' * 64}
        received = self.call('receive', delivery, ctx)
        self.assertTrue(received['ok'], received)
        accepted = self.call('accept', {'delivery_digest': received['delivery_digest']}, received['context'])
        self.assertTrue(accepted['ok'], accepted)
        ready = self.call('successor-ready', {
            'ref': 'successor', 'stage': 2, 'input_digest': received['delivery_digest'],
            'role': 'solution-designer', 'binding_verified': True, 'activated': False,
            'confirmed': True, 'archive_ref': 'old',
            'takeover_proof': takeover_proof('old', ctx['carrier']['attempt']),
        }, accepted['context'])
        self.assertTrue(ready['ok'], ready)
        selector = {'handoff_id': ready['handoff_id']}

        def finish(intent, status, no_write=False):
            effect = intent['effects'][0]
            receipt = {'adapter': 'fixture', 'invocation_id': 'call-' + effect['operation_id'],
                       'response_id': 'response-' + effect['operation_id'], 'ref': 'old',
                       'raw': {key: effect[key] for key in ('operation_id', 'operation', 'ref')},
                       'no_write': False}
            evidence = {**selector, 'operation_id': effect['operation_id'], 'ref': 'old',
                        'status': 'issued', 'receipt': receipt}
            issued = self.call('archive-result', evidence, intent['context'])
            self.assertTrue(issued['ok'], issued)
            result = self.call('archive-result', {**evidence, 'status': status,
                'receipt': {**receipt, 'raw': {'status': status}, 'no_write': no_write}}, issued['context'])
            self.assertTrue(result['ok'], result)
            return result

        archive = self.call('archive', selector, ready['context'])
        unknown = finish(archive, 'unknown')
        query = self.call('archive', selector, unknown['context'])
        self.assertEqual(query['effects'][0]['operation'], 'read-archive-state')
        for no_write in (True, False):
            with self.subTest(read_no_write=no_write):
                failed = finish(query, 'failed', no_write)
                self.assertEqual(failed['context']['retired_handoffs'][ready['handoff_id']]['archive_status'], 'unknown')
                retry = self.call('archive', selector, failed['context'])
                self.assertEqual(retry['effects'][0]['operation'], 'read-archive-state')
                readback = finish(retry, 'not-archived', True)
                permitted = self.call('archive', selector, readback['context'])
                self.assertEqual(permitted['effects'][0]['operation'], 'archive')
        for no_write, operation in ((True, 'archive'), (False, 'read-archive-state')):
            with self.subTest(archive_no_write=no_write):
                failed = finish(archive, 'failed', no_write)
                retry = self.call('archive', selector, failed['context'])
                self.assertEqual(retry['effects'][0]['operation'], operation)

    def test_single_dispatcher_exact_files_and_stopped_writer_recovery(self):
        ctx = self.context()
        ctx['stage'] = 3
        authority, current_plan, refs = self.execution_plan_fixture(['src/a.py', 'src/b.py'])
        started = self.call('start-dispatch', {'binding': self.binding(),
            'binding_verified': True, 'allowed_paths': ['src/a.py', 'src/b.py'], 'protected_paths': ['docs/draft.md'],
            'authority_digest': 'c' * 64, 'testing_basis': 'real CLI',
            'validation_plan': plan_for(self.binding()['worktree']),
            'execution_authority': authority, 'plan_authority': current_plan,
            'configuration': self.configuration('implementation-dispatcher')}, ctx)
        self.assertTrue(started['ok'])
        self.assertFalse(self.call('start-dispatch', {}, started['context'])['ok'])
        bound = self.call('dispatcher-bound', {'ref': 'dispatcher', 'attempt': started['attempt']}, started['context'])
        envelope = {'task_id': 'a', 'paths': ['src/a.py'],
                    'read_only': ['src/b.py'], 'behavior': 'behavior a', 'tests': ['CLI test'], 'git_operations': [],
                    'configuration': self.configuration('execution-agent'),
                    'plan_refs': refs, 'plan_authority': current_plan,
                    'group_digest': control.digest([current_plan, refs])}
        planned = self.call('plan-execution', envelope, bound['context'])
        self.assertTrue(planned['ok'])
        premature = self.call('execution-result', {'agent_ref': None, 'stopped': True,
            'changed_paths': [], 'file_hashes': {}, 'tests': ['passed'], 'git_unchanged': True}, planned['context'])
        self.assertFalse(premature['ok'])
        unknown = self.call('execution-dispatch-result', {'task_id': 'a', 'allocation_digest': planned['allocation_digest'], 'status': 'unknown'}, planned['context'])
        self.assertTrue(unknown['ok'])
        self.assertEqual(unknown['effects'][0]['operation'], 'read-native-state')
        cancelled = self.call('execution-dispatch-result', {'task_id': 'a', 'allocation_digest': planned['allocation_digest'], 'status': 'not-created'}, unknown['context'])
        self.assertTrue(cancelled['ok'])
        self.assertTrue(cancelled['context']['handoff_progress']['executions'][0]['stopped'])
        self.assertFalse(self.call('assign', {'agent_ref': 'late', 'task_id': 'a', 'allocation_digest': planned['allocation_digest']}, cancelled['context'])['ok'])
        assigned = self.call('assign', {'agent_ref': 'executor-a', 'task_id': 'a',
            'allocation_digest': planned['allocation_digest'], 'write_release': 'release-a'},
            planned['context'])
        self.assertTrue(assigned['ok'])
        envelope['task_id'] = 'b'
        self.assertFalse(self.call('plan-execution', envelope, assigned['context'])['ok'])
        self.assertFalse(self.call('recover-dispatch', {'stopped_refs': ['dispatcher'], 'file_hashes': {},
            'replacement_ref': 'replacement'}, assigned['context'])['ok'])
        stopped = self.call('execution-result', {'agent_ref': 'executor-a', 'stopped': True,
            'changed_paths': ['src/a.py'], 'file_hashes': {'src/a.py': 'd' * 64}, 'tests': ['CLI passed'],
            'git_unchanged': True, 'plan_refs': refs, 'plan_authority': current_plan,
            'write_release': 'release-a'}, assigned['context'])
        self.assertTrue(stopped['ok'])
        accepted = self.call('accept-execution', {'agent_ref': 'executor-a',
            'file_hashes': {'src/a.py': 'd' * 64}, 'plan_refs': refs,
            'plan_authority': current_plan, 'write_release': 'release-a'}, stopped['context'])
        recovered = self.call('recover-dispatch', {'stopped_refs': ['dispatcher', 'executor-a'],
            'file_hashes': {'src/a.py': 'd' * 64}, 'replacement_ref': 'replacement'}, accepted['context'])
        # Bare stopped_refs no longer authorize a replacement; C must prepare
        # the exact snapshot and persist the controlled creation first.
        self.assertFalse(recovered['ok'])

    def test_candidate_requires_accepted_execution_bytes_for_every_changed_path(self):
        ctx = self.context(); ctx['stage'] = 3
        plan = plan_for(self.binding()['worktree'])
        authority, current_plan, refs = self.execution_plan_fixture(
            ['src/a.py', 'src/b.py'], 'focused test')
        started = self.call('start-dispatch', {'binding': self.binding(),
            'binding_verified': True, 'allowed_paths': ['src/a.py', 'src/b.py'],
            'protected_paths': ['docs/draft.md'], 'authority_digest': 'c' * 64,
            'testing_basis': 'real CLI', 'validation_plan': plan,
            'execution_authority': authority, 'plan_authority': current_plan,
            'configuration': self.configuration('implementation-dispatcher')}, ctx)
        bound = self.call('dispatcher-bound', {'ref': 'dispatcher', 'attempt': started['attempt']}, started['context'])
        candidate = 'b' * 40
        checks = checks_for(plan['review_required'], candidate)
        evidence = {'dispatcher_ref': 'dispatcher', 'attempt': started['attempt'],
            'commit': candidate, 'expected_target_head': 'a' * 40,
            'binding': self.binding(), 'plan_authority': current_plan,
            'plan_digest': __import__('hashlib').sha256(
                json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
            'checks': checks, 'source': {'adapter': 'fixture', 'call_ref': 'focused',
                'response_ref': 'focused-result', 'raw': {'checks': checks,
                'source_unchanged': True, 'stopped': True, 'dispatcher_ref': 'dispatcher'}},
            'clean': True, 'changed_paths': ['src/a.py'], 'file_hashes': {'src/a.py': 'd' * 64}}
        self.assertFalse(self.call('candidate-ready', evidence, bound['context'])['ok'])
        allocation = self.call('plan-execution', {'task_id': 'slice', 'paths': ['src/a.py'],
            'read_only': ['docs/draft.md'], 'behavior': 'real caller behavior',
            'tests': ['focused test'], 'git_operations': [],
            'configuration': self.configuration('execution-agent'),
            'plan_refs': refs, 'plan_authority': current_plan,
            'group_digest': control.digest([current_plan, refs])}, bound['context'])
        assigned = self.call('assign', {'agent_ref': 'executor', 'task_id': 'slice',
            'allocation_digest': allocation['allocation_digest'], 'write_release': 'release-slice'},
            allocation['context'])
        received = self.call('execution-result', {'agent_ref': 'executor', 'stopped': True,
            'changed_paths': ['src/a.py'], 'file_hashes': {'src/a.py': 'd' * 64},
            'tests': ['focused test passed'], 'git_unchanged': True,
            'plan_refs': refs, 'plan_authority': current_plan,
            'write_release': 'release-slice'}, assigned['context'])
        accepted = self.call('accept-execution', {'agent_ref': 'executor',
            'file_hashes': {'src/a.py': 'd' * 64}, 'plan_refs': refs,
            'plan_authority': current_plan, 'write_release': 'release-slice'}, received['context'])
        self.assertTrue(self.call('candidate-ready', evidence, accepted['context'])['ok'])
        stale_plan = {**evidence, 'plan_authority': {**current_plan, 'revision': 0}}
        self.assertFalse(self.call('candidate-ready', stale_plan, accepted['context'])['ok'])
        extra = {**evidence, 'changed_paths': ['src/a.py', 'src/b.py'],
                 'file_hashes': {'src/a.py': 'd' * 64, 'src/b.py': 'e' * 64}}
        self.assertFalse(self.call('candidate-ready', extra, accepted['context'])['ok'])
        changed_bytes = {**evidence, 'file_hashes': {'src/a.py': 'e' * 64}}
        self.assertFalse(self.call('candidate-ready', changed_bytes, accepted['context'])['ok'])

    def test_standalone_entry_requires_complete_explicit_unattached_brief(self):
        brief = {'goal': 'Small fix', 'complexity': 'low', 'implementation_basis': 'exact requirement',
                 'allowed_paths': ['src/a.py'], 'failure_semantics': 'fail explicitly',
                 'acceptance': ['known behavior'], 'testing_seam': 'CLI', 'skip_stages_1_2': True,
                 'explicit_standalone': True, 'claimed_attached': False, 'gate_open': True}
        ctx = self.context()
        ctx['topic_ref'] = 'topic'
        result = self.call('standalone-entry', brief, ctx)
        self.assertTrue(result['ok'])
        self.assertIsNone(result['context']['topic_ref'])
        self.assertEqual(result['context']['controller_ref'], 'controller')
        self.assertEqual(result['effects'], [])
        brief['explicit_standalone'] = False
        self.assertFalse(self.call('standalone-entry', brief, ctx)['ok'])

    def test_closure_requires_reviewed_candidate_and_recovers_only_cleanup(self):
        ctx = self.context(); ctx['stage'] = 4
        candidate = 'b' * 40
        evidence = {'candidate': candidate, 'dispatcher_ref': 'dispatcher',
            'review': {axis: {'candidate': candidate, 'reviewer_ref': axis, 'status': 'accepted'} for axis in ('standards', 'spec')},
            'verification': {'candidate': candidate, 'checks': ['full suite']},
            'binding': self.binding(), 'implementation_paths': ['src/a.py'],
            'closure_paths': ['README.md'], 'protected_paths': ['docs/draft.md'], 'configuration': self.configuration('closure-agent')}
        evidence['review'], evidence['verification'] = verification_fixture(candidate, 'dispatcher', self.binding()['worktree'], self.binding()['base_commit'])
        bad = {**evidence, 'closure_paths': ['src/repair.py']}
        self.assertFalse(self.call('start-closure', bad, ctx)['ok'])
        started = self.call('start-closure', evidence, ctx)
        self.assertTrue(started['ok'])
        self.assertEqual(started['effects'][0]['role'], 'closure-agent')
        bound = self.call('closure-bound', {'ref': 'closure', 'attempt': started['attempt']}, started['context'])
        result = {'ref': 'closure', 'candidate': candidate, 'merge': 'c' * 40, 'ancestor_verified': True,
            'changed_paths': ['src/a.py', 'README.md'], 'binding': evidence['binding'],
            'checks': ['full suite'], 'disposition': 'deleted', 'disposition_verified': False, 'worktree_removed': False, 'branch_removed': False,
            'implementation_problem': None}
        partial = self.call('closure-result', result, bound['context'])
        self.assertTrue(partial['ok'])
        self.assertEqual(partial['context']['handoff_progress']['state'], 'cleanup-pending')
        self.assertEqual(partial['effects'][0]['operation'], 'cleanup-only')
        result['worktree_removed'] = result['branch_removed'] = result['disposition_verified'] = True
        complete = self.call('closure-result', result, partial['context'])
        self.assertEqual(complete['context']['handoff_progress']['state'], 'completed')

    def test_preference_rejects_creation_without_reprompt_and_closed_gate(self):
        ctx = self.bound()
        ctx['carrier'] = None
        ctx['handoff_progress'] = None
        ctx['preference']['topic_current'] = True
        evidence = {'target': 'local', 'project': 'project', 'title': 'Discuss',
            'missing_context': [], 'configuration': self.configuration(),
            'next_step': 'discuss', 'archive_ref': None, 'gate_open': True}
        result = self.call('prepare', evidence, ctx)
        self.assertTrue(result['ok'])
        self.assertIsNone(result['plan'])
        self.assertEqual(result['effects'], [])
        chosen = self.call('choose-dedicated', {'intent': 'explicit-dedicated'}, ctx)
        self.assertTrue(chosen['ok'])
        self.assertEqual(self.call('prepare', evidence, chosen['context'])['plan']['task_count'], 1)
        evidence['gate_open'] = False
        self.assertFalse(self.call('prepare', evidence, ctx)['ok'])

    def test_nested_unknown_fields_and_malformed_roles_are_rejected(self):
        import copy
        ctx = self.bound()
        cases = []
        for container in ('carrier', 'requirement_identity', 'preference', 'handoff_progress'):
            changed = copy.deepcopy(ctx); changed[container]['unexpected'] = True; cases.append(changed)
        changed = copy.deepcopy(ctx); changed['carrier']['kind'] = 'self-appointed-controller'; cases.append(changed)
        for invalid in cases:
            with self.subTest(context=invalid):
                self.assertFalse(self.call('cancel', {}, invalid)['ok'])

    def test_role_selection_preserves_confirmed_and_bounds_upgrade(self):
        cheap = {'model': 'small', 'effort': 'high', 'capability': 1, 'cost': 1, 'permission': 'same', 'visible_identity': 'same'}
        strong = {'model': 'large', 'effort': 'high', 'capability': 3, 'cost': None, 'permission': 'same', 'visible_identity': 'same'}
        evidence = {'role': 'execution-agent', 'required_capability': 1, 'supported': [cheap, strong],
            'user': None, 'frozen': {'model': 'small', 'effort': 'high'}, 'previous': cheap,
            'receipt': 'adapter-current', 'can_override': True, 'inherited': None, 'upgrade_attempted': False}
        selected = self.call('select-configuration', evidence)
        self.assertTrue(selected['ok'])
        self.assertEqual(selected['selection']['model'], 'small')
        self.assertFalse(selected['selection']['disclose'])
        evidence['previous'] = {**cheap, 'permission': 'old-permission'}
        self.assertTrue(self.call('select-configuration', evidence)['selection']['needs_decision'])
        evidence['previous'] = cheap
        evidence['supported'] = [strong]
        upgraded = self.call('select-configuration', evidence)
        self.assertFalse(upgraded['ok'])
        evidence['supported'][0]['cost'] = 2
        evidence['preferred'] = {'model': 'large', 'effort': 'high'}
        evidence['preference_reason'] = 'only available configuration meeting the role requirement'
        upgraded = self.call('select-configuration', evidence)
        self.assertTrue(upgraded['selection']['needs_decision'])
        evidence['upgrade_attempted'] = True
        self.assertFalse(self.call('select-configuration', evidence)['ok'])

    def test_stage3_default_pool_and_available_model_fallback(self):
        default = {'model': 'gpt-6-sol', 'effort': 'high', 'capability': None,
                   'cost': None, 'permission': 'same', 'visible_identity': 'executor'}
        alternative = {**default, 'model': 'available-other'}
        evidence = {'role': 'execution-agent', 'required_capability': None,
            'supported': [alternative, default], 'user': None, 'frozen': None,
            'previous': None, 'receipt': 'current native adapter', 'can_override': True,
            'inherited': {'model': 'available-other', 'effort': 'high'},
            'upgrade_attempted': False, 'preferred': {'model': 'gpt-6-sol', 'effort': 'high'},
            'preference_reason': 'implementation needs the stronger available reasoning setting'}
        selected = self.call('select-configuration', evidence)
        self.assertEqual((selected['selection']['model'], selected['selection']['effort']), ('gpt-6-sol', 'high'))
        self.assertIn('default pool', selected['selection']['reason'])
        evidence['preferred'] = {'model': 'available-other', 'effort': 'high'}
        self.assertFalse(self.call('select-configuration', evidence)['ok'])
        evidence['supported'] = [alternative]
        fallback = self.call('select-configuration', evidence)
        self.assertEqual(fallback['selection']['model'], 'available-other')
        self.assertIn('fallback', fallback['selection']['reason'])
        evidence['supported'] = [{**default, 'capability': 1}, {**alternative, 'capability': 3}]
        evidence['required_capability'] = 2
        capable_fallback = self.call('select-configuration', evidence)
        self.assertEqual(capable_fallback['selection']['model'], 'available-other')
        self.assertIn('fallback', capable_fallback['selection']['reason'])
        evidence['supported'].append({**default, 'model': 'gpt-6-luna', 'effort': 'xhigh',
                                      'capability': 3})
        evidence['preferred'] = {'model': 'gpt-6-luna', 'effort': 'xhigh'}
        capable_default = self.call('select-configuration', evidence)
        self.assertEqual(capable_default['selection']['model'], 'gpt-6-luna')
        self.assertIn('default pool', capable_default['selection']['reason'])
        evidence.pop('preferred')
        self.assertFalse(self.call('select-configuration', evidence)['ok'])
        evidence['user'] = {'model': 'available-other', 'effort': 'high'}
        self.assertEqual(self.call('select-configuration', evidence)['selection']['source'], 'user')

    def test_json_duplicate_keys_and_invalid_previous_configuration_fail_closed(self):
        raw = '{"schema_version":1,"schema_version":1,"action":"cancel","actor_ref":"controller","context":' + json.dumps(self.bound()) + ',"evidence":{}}'
        result = subprocess.run([sys.executable, str(CLI)], input=raw, text=True, capture_output=True)
        self.assertFalse(json.loads(result.stdout)['ok'])
        for previous in ([], {'model': 'supported'}, {**self.configuration()['supported'][0], 'extra': True}):
            evidence = self.configuration(); evidence['previous'] = previous
            self.assertFalse(self.call('select-configuration', evidence)['ok'])

    def test_text_only_adapter_evidence_never_invents_scores_or_prices(self):
        evidence = self.configuration()
        evidence['required_capability'] = None
        evidence['supported'][0]['capability'] = None
        evidence['supported'][0]['cost'] = None
        selected = self.call('select-configuration', evidence)
        self.assertTrue(selected['ok'])
        self.assertEqual(selected['selection']['source'], 'user')
        evidence['user'] = None
        evidence['inherited'] = {'model': 'supported', 'effort': 'high'}
        selected = self.call('select-configuration', evidence)
        self.assertTrue(selected['ok'])
        self.assertEqual(selected['selection']['source'], 'inherited')
        evidence['inherited'] = None
        self.assertFalse(self.call('select-configuration', evidence)['ok'])

    def successor_controls(self):
        context = self.context()
        context['topic_ref'] = 'topic'
        authority = {'kind': 'dedicated-stage', 'project_id': 'project', 'tree_id': 'tree',
            'topic_id': 'topic', 'controller_ref': 'controller', 'source_phase': 0, 'stage': 0,
            'run_id': 'handoff', 'attempt_id': 'handoff-attempt'}
        evidence = {'target': 'local', 'project': 'project', 'title': 'Discuss',
            'missing_context': [], 'configuration': self.configuration(), 'next_step': 'frame',
            'archive_ref': None, 'gate_open': True, 'entry_authority': authority}
        prepared = self.call('prepare', evidence, context)
        decided = self.call('decide', {'plan_id': prepared['plan']['plan_id'], 'intent': 'confirm'}, prepared['context'])
        bound = self.call('creation-result', {'status': 'ready', 'ref': 'previous',
            'attempt': prepared['plan']['plan_id']}, decided['context'])
        delivery = {'delivery_id': 'previous-result', 'source_ref': 'previous', 'attempt': prepared['plan']['plan_id'],
            'requirement_identity': context['requirement_identity'], 'commit': 'a' * 40, 'verified_commit_hash': 'a' * 64}
        received = self.call('receive', delivery, bound['context'])
        accepted = self.call('accept', {'delivery_digest': received['delivery_digest']}, received['context'])
        successor_evidence = {**evidence, 'configuration': self.configuration('dedicated-problem-framing'),
            'entry_authority': {**authority, 'kind': 'wrapper-phase-run', 'stage': 1,
                'run_id': 'phase-run', 'attempt_id': 'phase-attempt'}}
        successor = self.call('prepare', successor_evidence, accepted['context'])
        self.assertTrue(successor['ok'], successor)
        return successor, successor_evidence

    def test_successor_slot_is_bounded_and_cancel_preserves_predecessor(self):
        import copy
        result, evidence = self.successor_controls()
        context = result['context']
        predecessor = copy.deepcopy(context['handoff_progress'])
        self.assertFalse(self.call('prepare', evidence, context)['ok'])
        self.assertFalse(self.call('cancel', {}, context)['ok'])
        cancelled = self.call('cancel', {'control_plan_id': result['plan']['plan_id']}, context)
        self.assertTrue(cancelled['ok'], cancelled)
        self.assertEqual(cancelled['context']['handoff_progress'], predecessor)
        self.assertEqual(cancelled['context']['successor_control']['handoff_progress']['state'], 'cancelled')
        malformed = copy.deepcopy(context)
        malformed['successor_control']['successor_control'] = self.context()
        self.assertFalse(self.call('cancel', {'control_plan_id': result['plan']['plan_id']}, malformed)['ok'])
        wrong = self.call('decide', {'plan_id': 'wrong', 'intent': 'confirm'}, context)
        self.assertFalse(wrong['ok'])

    def test_entry_authority_is_typed_and_part_of_plan_digest(self):
        import copy
        result, _ = self.successor_controls()
        changed = copy.deepcopy(result['context'])
        changed['successor_control']['handoff_progress']['plan']['entry_authority']['attempt_id'] = 'replacement'
        self.assertFalse(self.call('decide', {'plan_id': result['plan']['plan_id'], 'intent': 'confirm'}, changed)['ok'])
        changed = copy.deepcopy(result['context'])
        changed['successor_control']['handoff_progress']['plan']['entry_authority']['stage'] = True
        self.assertFalse(self.call('decide', {'plan_id': result['plan']['plan_id'], 'intent': 'confirm'}, changed)['ok'])

    def test_schema_six_plan_activation_advances_one_current_authority(self):
        context = self.context(); context['stage'] = 3
        authority, old, _ = self.execution_plan_fixture(['src/a.py'])
        started = self.call('start-dispatch', {'binding': self.binding(),
            'binding_verified': True, 'allowed_paths': ['src/a.py'],
            'protected_paths': ['docs/draft.md'], 'authority_digest': 'c' * 64,
            'testing_basis': 'real CLI', 'validation_plan': plan_for(self.binding()['worktree']),
            'execution_authority': authority, 'plan_authority': old,
            'configuration': self.configuration('implementation-dispatcher')}, context)
        self.assertTrue(started['ok'], started)
        bound = self.call('dispatcher-bound', {'ref': 'dispatcher', 'attempt': started['attempt']},
                          started['context'])
        context = bound['context']
        revised = copy.deepcopy(authority)
        revised['revision'] = 2
        catalog = revised['execution_plan']['catalog']
        ticket = catalog['tickets'][0]
        ticket['plan_version'] = 2
        ticket['source'] = {'kind': 'inline', 'commit': None, 'sha256': '5' * 64,
                            'plan_version': 2}
        catalog['plan_digest'] = control.authority_digest({
            key: value for key, value in catalog.items()
            if key != 'plan_digest'})
        revised['execution_plan'] = execution_plan.authority_envelope(catalog, 1,
            input_digest=authority['execution_plan']['sources'][0]['input_digest'])
        new = {'revision': 2, 'authority_digest': control.authority_digest(revised),
               'plan_digest': revised['execution_plan']['plan_digest'],
               'contract_digest': revised['execution_plan']['contract_digest']}
        snapshot = {'head': 'b' * 40, 'planning_paths': ['docs/draft.md'],
                    'files': {'src/a.py': None, 'docs/draft.md': '6' * 64}}
        retention = execution_plan.classify_retained_results(authority, revised, [], [],
            None, snapshot, {'src/a.py': None}, ['docs/draft.md'])
        evidence = {'revision_id': 'private-1', 'old_authority': old, 'new_authority': new,
            'new_scope': {'implementation_paths': ['src/a.py'],
                          'protected_paths': ['docs/draft.md']},
            'planning_commit': 'a' * 40, 'planning_paths': ['docs/draft.md'],
            'snapshot': snapshot, 'baseline_fingerprints': {'src/a.py': None},
            'retention': retention, 'new_execution_authority': revised,
            'classification': {'kind': 'internal', 'reference': 'controller:private'},
            'git_facts': {'commit': 'a' * 40, 'changed_paths': ['docs/draft.md']}}
        activated = self.call('plan-revision-activate', evidence, context, schema_version=6)
        self.assertTrue(activated['ok'], activated)
        self.assertEqual(activated['context']['schema_version'], 6)
        self.assertEqual(activated['context']['handoff_progress']['plan_authority'], new)
        self.assertEqual(len(activated['context']['handoff_progress']['plan_revisions']), 1)
        replay = self.call('plan-revision-activate', evidence, activated['context'], schema_version=6)
        self.assertTrue(replay['acknowledged'], replay)
        changed = copy.deepcopy(evidence)
        changed['new_authority']['contract_digest'] = '6' * 64
        self.assertFalse(self.call('plan-revision-activate', changed, activated['context'], schema_version=6)['ok'])

if __name__ == '__main__':
    unittest.main()
