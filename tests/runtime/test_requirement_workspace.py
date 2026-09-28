"""Stage 2 freezes in its real isolated checkout and delivers only its snapshot."""
import copy
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from test_entry_prepare import EntrySupport
import entry_prepare as entry
import requirement_prepare as requirement
import requirement_delivery as delivery
import stage_handoff as handoff
import supervision_protocol as supervision
from test_stage_transfer import configuration


class ConversationWorkspaceTests(EntrySupport):
    def setUp(self):
        super().setUp()
        self.source = self.root / 'isolated'
        self.git('worktree', 'add', '-b', 'codex/source', str(self.source), 'HEAD')
        self.addCleanup(lambda: subprocess.run(['git', '-C', str(self.root), 'worktree', 'remove', '--force', str(self.source)], capture_output=True))
        entry.os.getcwd.return_value = str(self.source)
        self.path = 'docs/requirements/snapshot.md'
        self.request.update(stage=2, source={'kind': 'conversation', 'path': self.path})
        self.request['host']['project_path'] = str(self.source)

    def freeze(self):
        common = {'protocol': requirement.PROTOCOL, 'entry': self.request}
        prepared = requirement.handle({**common, 'operation': 'prepare', 'purpose': 'write', 'path': self.path,
            'version': 1, 'authorization': 'controller:confirmed', 'content': 'confirmed snapshot\n'})
        written = requirement.handle({**common, 'operation': 'write', 'intent': prepared})
        intent = requirement.handle({**common, 'operation': 'prepare', 'purpose': 'freeze', 'path': self.path,
            'version': 1, 'authorization': 'controller:confirmed', 'previous': written})
        return requirement.handle({**common, 'operation': 'freeze', 'intent': intent})

    def test_freeze_and_bounded_delivery_preserve_real_checkout_identities(self):
        frozen = self.freeze()
        self.assertEqual(frozen['entry']['repository']['root'], str(self.source))
        self.assertEqual(frozen['entry']['target']['repository'], str(self.root))
        (self.root / 'existing.txt').write_text('staged user content\n')
        self.git('add', 'existing.txt')
        (self.root / 'existing.txt').write_text('unstaged user content\n')
        before = requirement.unrelated(self.root, [self.path])
        request = {'protocol': delivery.PROTOCOL, 'operation': 'prepare', 'entry': self.request,
            'requirement': frozen, 'target': {'repository': str(self.root), 'branch': 'main'},
            'owned_paths': [self.path], 'authorization': 'controller:confirmed'}
        intent = delivery.handle(request)
        result = delivery.handle({'protocol': delivery.PROTOCOL, 'operation': 'deliver', 'entry': self.request, 'intent': intent})
        self.assertEqual(result['state'], 'verified')
        self.assertNotEqual(result['delivery_commit'], frozen['commit'])
        self.assertEqual(requirement.unrelated(self.root, [self.path]), before)
        self.assertEqual(self.git('diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD'), self.path)
        proof = handoff.delivery({'target': request['target'], 'delivery': result['delivery'], 'requirement': frozen},
            entry.resolve(self.request), frozen['requirement_identity'], frozen['commit'])
        self.assertEqual(proof['source_commit'], frozen['commit'])
        again = delivery.handle({'protocol': delivery.PROTOCOL, 'operation': 'reconcile', 'entry': self.request, 'intent': intent})
        self.assertEqual(again['delivery_commit'], result['delivery_commit'])
        subprocess.run(['git', '-C', str(self.source), 'merge', '--no-edit', 'main'], check=True, capture_output=True)
        binding = supervision.bind_worktree({'repository': str(self.root), 'target_branch': 'main',
            'worktree': str(self.source), 'branch': 'codex/source', 'base_commit': result['target_head'],
            'allowed_paths': [self.path], 'lifecycle': {'origin': 'reused', 'manager': 'host',
                'disposition': 'retain', 'evidence_ref': 'fixture:source-workspace', 'creation_receipt': None}})['binding']
        current = copy.deepcopy(self.request)
        current.update(source={'kind': 'frozen', 'path': self.path}, target={'kind': 'flow', 'binding': binding})
        resolved = entry.resolve(current)
        scope = {'baseline': resolved['repository']['head'], 'owned_paths': ['docs/spec.md'],
                 'protected_paths': [self.path], 'implementation_paths': None, 'closure_paths': None,
                 'downstream': {'state': 'unresolved', 'revision': 0, 'implementation_required': None,
                                'manifest': None, 'proposal_ref': None, 'seal_ref': None}}
        stage_request = {'protocol': handoff.PROTOCOL, 'operation': 'prepare', 'entry': current,
            'expected_entry': resolved, 'stage': 2, 'role': 'solution-designer', 'requirement': frozen,
            'predecessor': None, 'target': request['target'], 'delivery': result['delivery'], 'binding': binding,
            'scope': scope, 'authorization': {'reference': 'controller:confirmed', 'flow_mode': 'stepwise',
                'scope_digest': entry.digest(scope)}, 'configuration': configuration('solution-designer'),
            'semantic': {'objective': 'design the frozen requirement', 'testing_basis': 'real Git',
                         'completion_criteria': ['verified design'], 'constraints': ['planning only']}}
        self.assert_code('source_history_conflict', lambda: handoff.handle(stage_request))
        self.assertEqual(requirement.unrelated(self.root, [self.path]), before)

    def test_delivered_requirement_publishes_from_a_target_based_flow(self):
        frozen = self.freeze()
        target = {'repository': str(self.root), 'branch': 'main'}
        intent = delivery.handle({'protocol': delivery.PROTOCOL, 'operation': 'prepare',
            'entry': self.request, 'requirement': frozen, 'target': target,
            'owned_paths': [self.path], 'authorization': 'controller:confirmed'})
        delivered = delivery.handle({'protocol': delivery.PROTOCOL, 'operation': 'deliver',
            'entry': self.request, 'intent': intent})
        old_head = subprocess.run(['git', '-C', str(self.source), 'rev-parse', 'HEAD'],
            check=True, capture_output=True, text=True).stdout.strip()
        old_status = subprocess.run(['git', '-C', str(self.source), 'status', '--porcelain'],
            check=True, capture_output=True, text=True).stdout
        self.assertEqual(old_head, frozen['commit'])
        with self.assertRaises(supervision.ProtocolError) as caught:
            supervision.bind_worktree({'repository': str(self.root), 'target_branch': 'main',
                'worktree': str(self.source), 'branch': 'codex/source',
                'base_commit': delivered['target_head'], 'allowed_paths': ['docs/spec.md'],
                'lifecycle': {'origin': 'reused', 'manager': 'host', 'disposition': 'retain',
                    'evidence_ref': 'fixture:source-workspace', 'creation_receipt': None}})
        self.assertEqual(caught.exception.code, 'worktree_mismatch')

        flow = self.root / 'planning'
        self.git('worktree', 'add', '-b', 'codex/planning', str(flow), delivered['target_head'])
        self.addCleanup(lambda: subprocess.run(['git', '-C', str(self.root), 'worktree',
            'remove', '--force', str(flow)], capture_output=True))
        binding = supervision.bind_worktree({'repository': str(self.root), 'target_branch': 'main',
            'worktree': str(flow), 'branch': 'codex/planning',
            'base_commit': delivered['target_head'], 'allowed_paths': ['docs/spec.md'],
            'lifecycle': {'origin': 'reused', 'manager': 'host', 'disposition': 'retain',
                'evidence_ref': 'fixture:planning-workspace', 'creation_receipt': None}})['binding']
        entry.os.getcwd.return_value = str(flow)
        current = copy.deepcopy(self.request)
        current['host']['project_path'] = str(flow)
        current.update(source={'kind': 'frozen', 'path': self.path},
            target={'kind': 'flow', 'binding': binding})
        current = self.with_registration(current, project=self.root, receipt='host:flow-registry')
        resolved = entry.resolve(current)
        scope = {'baseline': resolved['repository']['head'], 'owned_paths': ['docs/spec.md'],
                 'protected_paths': [self.path], 'implementation_paths': None,
                 'closure_paths': None, 'downstream': {'state': 'unresolved', 'revision': 0,
                 'implementation_required': None, 'manifest': None, 'proposal_ref': None,
                 'seal_ref': None}}
        prepared = handoff.handle({'protocol': handoff.PROTOCOL, 'operation': 'prepare',
            'entry': current, 'expected_entry': resolved, 'stage': 2,
            'role': 'solution-designer', 'requirement': frozen, 'predecessor': None,
            'target': target, 'delivery': delivered['delivery'], 'binding': binding,
            'scope': scope, 'authorization': {'reference': 'controller:confirmed',
                'flow_mode': 'stepwise', 'scope_digest': entry.digest(scope)},
            'configuration': configuration('solution-designer'),
            'semantic': {'objective': 'design the frozen requirement',
                'testing_basis': 'real Git', 'completion_criteria': ['verified design'],
                'constraints': ['planning only']}})
        self.assertEqual(prepared['binding']['base_commit'], delivered['target_head'])
        (flow / 'docs/spec.md').write_text('accepted plan\n')
        subprocess.run(['git', '-C', str(flow), 'add', 'docs/spec.md'], check=True)
        subprocess.run(['git', '-C', str(flow), 'commit', '-qm', 'add planning'], check=True)
        planning = subprocess.run(['git', '-C', str(flow), 'rev-parse', 'HEAD'],
            check=True, capture_output=True, text=True).stdout.strip()
        published = supervision.publish_planning({'binding': binding, 'planning_commit': planning,
            'allowed_paths': ['docs/spec.md'], 'protected_paths': [self.path]})
        self.assertEqual(published['state'], 'planning_published')
        self.assertEqual(self.git('rev-parse', 'main'), published['merge_commit'])
        self.assertEqual(self.git('show', f"main:{self.path}"), 'confirmed snapshot')
        self.assertEqual(subprocess.run(['git', '-C', str(self.source), 'rev-parse', 'HEAD'],
            check=True, capture_output=True, text=True).stdout.strip(), old_head)
        self.assertEqual(subprocess.run(['git', '-C', str(self.source), 'status', '--porcelain'],
            check=True, capture_output=True, text=True).stdout, old_status)

    def test_requirement_already_on_target_reuses_the_source_workspace(self):
        frozen = self.freeze()
        self.git('merge', '--ff-only', frozen['commit'])
        target_head = self.git('rev-parse', 'HEAD')
        target = {'repository': str(self.root), 'branch': 'main'}
        intent = delivery.handle({'protocol': delivery.PROTOCOL, 'operation': 'prepare',
            'entry': self.request, 'requirement': frozen, 'target': target,
            'owned_paths': [self.path], 'authorization': 'controller:confirmed'})
        delivered = delivery.handle({'protocol': delivery.PROTOCOL, 'operation': 'deliver',
            'entry': self.request, 'intent': intent})
        self.assertIsNone(delivered['delivery'])
        self.assertEqual(delivered['target_head'], target_head)
        binding = supervision.bind_worktree({'repository': str(self.root), 'target_branch': 'main',
            'worktree': str(self.source), 'branch': 'codex/source',
            'base_commit': target_head, 'allowed_paths': ['docs/spec.md'],
            'lifecycle': {'origin': 'reused', 'manager': 'host', 'disposition': 'retain',
                'evidence_ref': 'fixture:source-workspace', 'creation_receipt': None}})['binding']
        current = copy.deepcopy(self.request)
        current.update(source={'kind': 'frozen', 'path': self.path},
            target={'kind': 'flow', 'binding': binding})
        resolved = entry.resolve(current)
        scope = {'baseline': target_head, 'owned_paths': ['docs/spec.md'],
                 'protected_paths': [self.path], 'implementation_paths': None,
                 'closure_paths': None, 'downstream': {'state': 'unresolved', 'revision': 0,
                 'implementation_required': None, 'manifest': None, 'proposal_ref': None,
                 'seal_ref': None}}
        prepared = handoff.handle({'protocol': handoff.PROTOCOL, 'operation': 'prepare',
            'entry': current, 'expected_entry': resolved, 'stage': 2,
            'role': 'solution-designer', 'requirement': frozen, 'predecessor': None,
            'target': target, 'delivery': None, 'binding': binding, 'scope': scope,
            'authorization': {'reference': 'controller:confirmed', 'flow_mode': 'stepwise',
                'scope_digest': entry.digest(scope)},
            'configuration': configuration('solution-designer'),
            'semantic': {'objective': 'design the frozen requirement',
                'testing_basis': 'real Git', 'completion_criteria': ['verified design'],
                'constraints': ['planning only']}})
        self.assertEqual(prepared['binding']['worktree'], str(self.source))
        (self.source / 'docs/spec.md').write_text('accepted plan\n')
        subprocess.run(['git', '-C', str(self.source), 'add', 'docs/spec.md'], check=True)
        subprocess.run(['git', '-C', str(self.source), 'commit', '-qm', 'add planning'], check=True)
        planning = subprocess.run(['git', '-C', str(self.source), 'rev-parse', 'HEAD'],
            check=True, capture_output=True, text=True).stdout.strip()
        published = supervision.publish_planning({'binding': binding, 'planning_commit': planning,
            'allowed_paths': ['docs/spec.md'], 'protected_paths': [self.path]})
        self.assertEqual(published['state'], 'planning_published')
        self.assertEqual(self.git('show', f"main:{self.path}"), 'confirmed snapshot')

    def test_other_stage_and_carrier_cannot_use_cross_checkout_freeze(self):
        for update in ({'stage': 1, 'source': {'kind': 'stage1', 'path': self.path}},
                       {'host': {**self.request['host'], 'role': 'scripted-carrier', 'controller_ref': 'other', 'source_ref': 'runner'}}):
            request = copy.deepcopy(self.request)
            request.update(update)
            if 'host' in update:
                request = self.with_registration(request, project=self.root, receipt='host:carrier-registry')
            with self.subTest(update=update):
                self.assert_code('target_mismatch', lambda: entry.resolve(request))
        self.assertFalse((self.source / self.path).exists())

    def test_stage2_delivery_rejects_expanded_paths_and_changed_target(self):
        frozen = self.freeze()
        request = {'protocol': delivery.PROTOCOL, 'operation': 'prepare', 'entry': self.request,
            'requirement': frozen, 'target': {'repository': str(self.root), 'branch': 'main'},
            'owned_paths': [self.path], 'authorization': 'controller:confirmed'}
        expanded = {**request, 'owned_paths': [self.path, 'other.md']}
        self.assert_code('invalid_paths', lambda: delivery.handle(expanded))
        moved = {**request, 'target': {'repository': str(self.source), 'branch': 'codex/source'}}
        self.assert_code('target_mismatch', lambda: delivery.handle(moved))
        self.assertFalse((self.root / self.path).exists())

    def test_unrelated_source_history_is_not_delivered(self):
        (self.source / 'source-only.md').write_text('unrelated prior work\n')
        subprocess.run(['git', '-C', str(self.source), 'add', 'source-only.md'], check=True)
        subprocess.run(['git', '-C', str(self.source), 'commit', '-qm', 'prior work'], check=True)
        frozen = self.freeze()
        intent = delivery.handle({'protocol': delivery.PROTOCOL, 'operation': 'prepare', 'entry': self.request,
            'requirement': frozen, 'target': {'repository': str(self.root), 'branch': 'main'},
            'owned_paths': [self.path], 'authorization': 'controller:confirmed'})
        delivery.handle({'protocol': delivery.PROTOCOL, 'operation': 'deliver', 'entry': self.request, 'intent': intent})
        self.assertFalse((self.root / 'source-only.md').exists())
        self.assertEqual((self.root / self.path).read_text(), 'confirmed snapshot\n')
