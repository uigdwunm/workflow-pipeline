"""Persistent discussion ownership across disposable Git execution worktrees."""
import copy
import tempfile
import uuid
from pathlib import Path

from test_stage_transfer import AttachedTransferTests, entry, handoff


class DiscussionProjectTests(AttachedTransferTests):
    def flow_entry(self):
        self.git('add', 'docs/discussions')
        self.git('commit', '-qm', 'discussion documents')
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        flow = Path(temporary.name).resolve() / 'flow'
        self.git('worktree', 'add', '-q', '-b', 'identity-flow', str(flow), 'HEAD')
        request = copy.deepcopy(self.request)
        request.pop('target')
        request['host']['project_path'] = str(flow)
        entry.os.getcwd.return_value = str(flow)
        return flow, request

    def test_flow_entry_retains_original_discussion_project(self):
        flow, request = self.flow_entry()
        before = self.ledger.read_bytes()
        result = entry.resolve(request)
        self.assertEqual(result['repository']['root'], str(flow))
        self.assertEqual(result['discussion_project']['root'], str(self.root))
        self.assertEqual(result['discussion_project']['ledger_path'], str(self.ledger))
        self.assertEqual(before, self.ledger.read_bytes())

    def test_deleted_flow_does_not_prevent_original_topic_readback(self):
        flow, request = self.flow_entry()
        result = entry.resolve(request)
        self.git('worktree', 'remove', str(flow))
        saved = {**self.input, 'entry': request, 'expected_entry': result}
        self.assertIsNone(handoff.phase_evidence(saved, receiving=True))
        self.assertFalse(flow.exists())

    def test_attachment_and_pinned_identity_drift_are_rejected(self):
        flow, request = self.flow_entry()
        current = entry.resolve(request)
        for key, value in (('root', str(flow)), ('actor_topic_id', 'topic-' + 'a' * 32),
                           ('git_common_dir', str(flow / '.git'))):
            changed = copy.deepcopy(current)
            changed['discussion_project'][key] = value
            with self.subTest(key=key):
                self.assert_code('discussion_identity_conflict', lambda: entry.discussion_root(changed))
        wrong = copy.deepcopy(request)
        wrong['source']['attachment']['actor_conversation_ref'] = 'another-task'
        self.assert_code('identity_mismatch', lambda: entry.resolve(wrong))
        wrong = copy.deepcopy(request)
        wrong['source']['attachment']['project_id'] = '../../outside'
        self.assert_code('discussion_identity_conflict', lambda: entry.resolve(wrong))

    def test_control_envelope_project_is_not_rewritten(self):
        import test_stage_transfer as transfer
        flow, request = self.flow_entry()
        current = entry.resolve(request)
        saved = {**self.input, 'entry': request, 'expected_entry': current, 'controller_ref': 'task'}
        port = self.port()
        port['discussion']['project_path'] = str(flow)
        before = copy.deepcopy(port), self.ledger.read_bytes()
        self.assert_code('discussion_identity_conflict',
            lambda: transfer.dispatch.checkpoint(port, saved, 'reserve-launch', {}))
        self.assertEqual(before, (port, self.ledger.read_bytes()))

    def test_old_entry_protocol_and_missing_project_evidence_are_rejected(self):
        _, request = self.flow_entry()
        self.assert_code('unsupported_protocol', lambda: entry.resolve({**request, 'protocol': 'workflow-entry-v1'}))
        current = entry.resolve(request)
        current.pop('discussion_project')
        self.assert_code('invalid_request', lambda: entry.discussion_root(current))

    def test_missing_source_document_is_not_replaced_by_flow(self):
        flow, request = self.flow_entry()
        current = entry.resolve(request)
        source_document = Path(current['requirement']['topic']['topic_document_path'])
        self.assertTrue((flow / source_document.relative_to(self.root)).exists())
        original = source_document.read_bytes()
        try:
            source_document.unlink()
            with self.assertRaises(entry.discussion_protocol.ProtocolError):
                entry.discussion_root(current)
        finally:
            source_document.write_bytes(original)

    def test_linked_source_owner_is_distinct_from_primary_and_flow(self):
        # The initial discussion is untracked, so these checkouts start without it.
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        owner = Path(temporary.name).resolve() / 'source'
        flow = Path(temporary.name).resolve() / 'execution'
        self.git('worktree', 'add', '-q', '-b', 'linked-source', str(owner), 'HEAD')
        topic = entry.discussion_protocol.handle({'protocol_version': 1, 'operation': 'bootstrap',
            'project_path': str(owner), 'entry_mode': 'explicit-skill', 'conversation_ref': 'task',
            'idempotency_key': str(uuid.uuid4()), 'root_slug': 'linked-topic'})
        self.git('worktree', 'add', '-q', '-b', 'linked-execution', str(flow), 'HEAD')
        request = copy.deepcopy(self.request)
        request.pop('target')
        request['host']['project_path'] = str(flow)
        request['source']['attachment'] = {'project_id': topic['project_id'], 'tree_id': topic['tree_id'],
            'actor_topic_id': topic['topic_id'], 'actor_conversation_ref': 'task'}
        entry.os.getcwd.return_value = str(flow)
        current = entry.resolve(request)
        self.assertEqual(current['discussion_project']['root'], str(owner))
        handoff.verify_discussion_binding(current, {'repository': str(self.root),
            'git_common_dir': current['repository']['git_common_dir'], 'worktree': str(flow)})
        self.assert_code('discussion_identity_conflict', lambda: handoff.verify_discussion_binding(current,
            {'repository': str(self.root), 'git_common_dir': current['repository']['git_common_dir'], 'worktree': str(owner)}))
        # Another Git store cannot use a copied attachment to reach the owner.
        foreign = Path(temporary.name).resolve() / 'foreign'
        foreign.mkdir()
        entry.git(foreign, 'init', '-q')
        foreign_facts = {'root': str(foreign), 'kind': 'git', 'git_common_dir': str(foreign / '.git')}
        with self.assertRaises(entry.discussion_protocol.ProtocolError):
            entry.resolve_discussion_project(foreign_facts, request['source']['attachment'])


# Reuse fixture helpers without rerunning their inherited test inventory.
def load_tests(loader, tests, pattern):
    import unittest
    return unittest.TestSuite(DiscussionProjectTests(name) for name in
        loader.getTestCaseNames(DiscussionProjectTests) if name in DiscussionProjectTests.__dict__)
