"""Exact documentation commits, dirty index preservation and same-intent retry."""
import copy
import hashlib
import io
import json
import os
import uuid
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
from contextlib import redirect_stdout

sys.path.insert(0, str(Path(__file__).parent))
import test_entry_prepare
import requirement_prepare as requirement
import entry_prepare as entry
import discussion_protocol


class RequirementTests(test_entry_prepare.EntrySupport):
    def cli_call(self, operation, **arguments):
        request = {'protocol': requirement.PROTOCOL, 'operation': operation, 'entry': self.request, **arguments}
        output = io.StringIO()
        with patch.object(sys, 'stdin', io.TextIOWrapper(io.BytesIO(json.dumps(request).encode()))), redirect_stdout(output):
            code = entry.cli(requirement.handle)
        return code, json.loads(output.getvalue())

    def test_changed_explicit_source_rejects_old_write_before_side_effects(self):
        self.request['source']['path'] = 'docs/a.md'
        intent = self.call('prepare', purpose='write', path='docs/a.md', version=1, authorization='confirmed', content='A')
        self.request['source']['path'] = 'docs/b.md'
        head = self.git('rev-parse', 'HEAD')
        index = self.git('ls-files', '--stage')
        for operation in ('write', 'reconcile'):
            with self.subTest(operation=operation):
                self.assert_code('source_changed', lambda: self.call(operation, intent=intent))
                self.assertFalse((self.root / 'docs/a.md').exists())
                self.assertFalse((self.root / 'docs/b.md').exists())
                self.assertEqual(self.git('rev-parse', 'HEAD'), head)
                self.assertEqual(self.git('ls-files', '--stage'), index)
        self.request['source']['path'] = 'docs/a.md'
        self.assertEqual(self.call('reconcile', intent=intent)['path'], 'docs/a.md')

    def test_changed_explicit_source_rejects_old_freeze_and_reconcile(self):
        # Initial entry may omit path; later explicit selection must match A.
        _, receipt = self.create()
        intent = self.freeze_intent(receipt)
        self.request['source']['path'] = 'docs/b.md'
        before = (self.git('rev-parse', 'HEAD'), self.git('ls-files', '--stage'), (self.root / receipt['path']).read_bytes())
        for operation in ('freeze', 'reconcile'):
            with self.subTest(operation=operation):
                self.assert_code('source_changed', lambda: self.call(operation, intent=intent))
                self.assertEqual((self.git('rev-parse', 'HEAD'), self.git('ls-files', '--stage'),
                                  (self.root / receipt['path']).read_bytes()), before)
        self.request['source']['path'] = receipt['path']
        frozen = self.call('freeze', intent=intent)
        self.assertEqual(self.call('reconcile', intent=intent)['commit'], frozen['commit'])

    def test_cli_precommit_failure_has_no_verified_commit_evidence(self):
        _, receipt = self.create()
        intent = self.freeze_intent(receipt)
        hook = self.root / '.git/hooks/pre-commit'
        hook.write_text('#!/bin/sh\necho SECRET_HOOK_TEXT >&2\nexit 1\n')
        hook.chmod(0o755)
        code, response = self.cli_call('freeze', intent=intent)
        self.assertEqual(code, 1)
        self.assertFalse(response['ok'])
        self.assertNotIn('result', response)
        self.assertEqual(response['error']['completed_evidence'], [])
        self.assertNotIn('SECRET_HOOK_TEXT', json.dumps(response))
        self.assertEqual(self.git('rev-list', '--count', 'HEAD'), '1')

    def test_cli_postcommit_failure_reports_verified_partial_commit_and_recovers(self):
        _, receipt = self.create()
        original = (self.root / receipt['path']).read_bytes()
        intent = self.freeze_intent(receipt)
        hook = self.root / '.git/hooks/post-commit'
        hook.write_text('#!/bin/sh\nprintf changed > docs/requirements/a.md\n')
        hook.chmod(0o755)
        for operation in ('freeze', 'reconcile'):
            code, response = self.cli_call(operation, intent=intent)
            self.assertEqual(code, 1)
            self.assertFalse(response['ok'])
            self.assertNotIn('result', response)
            error = response['error']
            self.assertEqual(error['operation'], operation)
            self.assertEqual(error['code'], 'document_changed')
            self.assertEqual(len(error['completed_evidence']), 1)
            partial = error['completed_evidence'][0]
            self.assertEqual(partial['kind'], 'verified-requirement-commit')
            self.assertEqual(partial['commit'], self.git('rev-parse', 'HEAD'))
            self.assertEqual(partial['intent_digest'], intent['digest'])
            self.assertEqual(partial['sha256'], hashlib.sha256(original).hexdigest())
            self.assertFalse(partial['downstream_ready'])
            self.assertNotIn('requirement_identity', partial)
            self.assertNotIn('digest', partial)
            self.assertEqual(self.git('rev-list', '--count', 'HEAD'), '2')
        (self.root / receipt['path']).write_bytes(original)
        code, response = self.cli_call('reconcile', intent=intent)
        self.assertEqual(code, 0)
        self.assertEqual(response['result']['commit'], partial['commit'])
        self.assertEqual(self.git('rev-list', '--count', 'HEAD'), '2')

    def test_cli_does_not_expose_arbitrary_exception_text(self):
        with patch.object(requirement, 'handle', side_effect=OSError('SECRET_EXTERNAL_ERROR')):
            code, response = self.cli_call('freeze', intent={})
        self.assertEqual(code, 1)
        self.assertNotIn('SECRET_EXTERNAL_ERROR', json.dumps(response))

    def call(self, operation, **arguments):
        return requirement.handle({"protocol": requirement.PROTOCOL, "operation": operation,
                                   "entry": self.request, **arguments})

    def create(self, path="docs/requirements/a.md", content="confirmed requirement\n"):
        intent = self.call("prepare", purpose="write", path=path, version=1,
                           authorization="confirmed:requirement", content=content)
        receipt = self.call("write", intent=intent)
        return intent, receipt

    def freeze_intent(self, receipt):
        return self.call("prepare", purpose="freeze", path=receipt["path"], version=receipt["version"],
                         authorization="confirmed:commit", previous=receipt)

    def test_dirty_workspace_and_index_preserved_exact_commit_and_retry(self):
        (self.root / "existing.txt").write_text("staged user work\n")
        self.git("add", "existing.txt")
        (self.root / "existing.txt").write_text("unstaged user work\n")
        (self.root / "untracked.txt").write_text("untracked\n")
        original_index = self.git("show", ":existing.txt")
        _, receipt = self.create()
        intent = self.freeze_intent(receipt)
        frozen = self.call("freeze", intent=intent)
        self.assertEqual(frozen["changed_paths"], [receipt["path"]])
        self.assertEqual(self.git("show", ":existing.txt"), original_index)
        self.assertEqual((self.root / "existing.txt").read_text(), "unstaged user work\n")
        self.assertTrue((self.root / "untracked.txt").exists())
        self.assertEqual(frozen["sha256"], hashlib.sha256((self.root / receipt["path"]).read_bytes()).hexdigest())
        self.assertEqual(self.call("reconcile", intent=intent)["commit"], frozen["commit"])
        self.assertEqual(self.call("freeze", intent=intent)["commit"], frozen["commit"])
        self.assertEqual(self.git("rev-list", "--count", "HEAD"), "2")

    def test_existing_unknown_document_and_partial_staging_refused(self):
        (self.root / "unknown.md").write_text("user work")
        self.assert_code("ownership_required", lambda: self.call("prepare", purpose="write", path="unknown.md", version=1,
                                                                 authorization="confirmed", content="replacement"))
        _, receipt = self.create()
        self.git("add", receipt["path"])
        updated = self.call("prepare", purpose="write", path=receipt["path"], version=2,
                            authorization="confirmed", content="updated", previous=receipt)
        new_receipt = self.call("write", intent=updated)
        self.assert_code("partial_staging", lambda: self.freeze_intent(new_receipt))

    def test_write_response_loss_reuses_same_path_and_rejects_changed_bytes(self):
        intent, receipt = self.create()
        self.assertEqual(self.call("reconcile", intent=intent)["sha256"], receipt["sha256"])
        (self.root / receipt["path"]).write_text("concurrent user edit")
        self.assert_code("document_changed", lambda: self.call("write", intent=intent))

    def test_failed_hook_preserves_intent_and_retry_commits_once(self):
        _, receipt = self.create()
        intent = self.freeze_intent(receipt)
        hook = self.root / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        self.assert_code("commit_unverified", lambda: self.call("freeze", intent=intent))
        self.assertEqual(self.call("reconcile", intent=intent)["state"], "prepared")
        hook.unlink()
        self.call("freeze", intent=intent)
        self.assertEqual(self.git("rev-list", "--count", "HEAD"), "2")

    def test_post_commit_document_drift_fails_without_duplicate_commit(self):
        _, receipt = self.create()
        intent = self.freeze_intent(receipt)
        hook = self.root / ".git/hooks/post-commit"
        hook.write_text("#!/bin/sh\nprintf changed > docs/requirements/a.md\n")
        hook.chmod(0o755)
        self.assert_code("document_changed", lambda: self.call("freeze", intent=intent))
        self.assert_code("document_changed", lambda: self.call("reconcile", intent=intent))
        self.assertEqual(self.git("rev-list", "--count", "HEAD"), "2")

    def test_head_drift_and_wrong_owner_refused_before_write(self):
        intent = self.call("prepare", purpose="write", path="docs/a.md", version=1,
                           authorization="confirmed", content="draft")
        self.git("commit", "--allow-empty", "-qm", "other work")
        self.assert_code("head_changed", lambda: self.call("write", intent=intent))
        self.assertFalse((self.root / "docs/a.md").exists())

    def test_unchanged_frozen_source_reuses_commit(self):
        _, receipt = self.create()
        frozen = self.call("freeze", intent=self.freeze_intent(receipt))
        reused = self.call("freeze", intent=self.freeze_intent(frozen))
        self.assertEqual(reused["commit"], frozen["commit"])
        self.assertEqual(reused["changed_paths"], [])

    def test_literal_path_and_symlink_components(self):
        _, receipt = self.create(path="docs/[literal]*.md")
        (self.root / "docs/other.md").write_text("other")
        frozen = self.call("freeze", intent=self.freeze_intent(receipt))
        self.assertEqual(frozen["changed_paths"], [receipt["path"]])
        (self.root / "linked").symlink_to(self.root / "docs", target_is_directory=True)
        self.assert_code("invalid_path", lambda: self.create(path="linked/b.md"))

    def test_freeze_receipt_verifies_commit_bytes_and_ancestry(self):
        _, receipt = self.create()
        frozen = self.call("freeze", intent=self.freeze_intent(receipt))
        self.request["source"] = {"kind": "frozen", "path": receipt["path"]}
        self.assertEqual(self.call("verify", evidence=frozen)["commit"], frozen["commit"])
        (self.root / receipt["path"]).write_text("drift")
        self.assert_code("source_changed", lambda: self.call("verify", evidence=frozen))

    def test_original_checkpoint_reuses_document_without_recreating_it(self):
        path = 'docs/existing.md'
        (self.root / 'docs').mkdir()
        (self.root / path).write_text('existing confirmed draft')
        self.request['source']['path'] = path
        previous = {'path': path, 'sha256': hashlib.sha256((self.root / path).read_bytes()).hexdigest(),
                    'version': 1, 'write_owner': 'task', 'repository': str(self.root), 'receipt': 'original:checkpoint'}
        intent = self.call('prepare', purpose='freeze', path=path, version=1, authorization='confirmed', previous=previous)
        frozen = self.call('freeze', intent=intent)
        self.request['source'] = {'kind': 'frozen', 'path': path}
        old_handoff = {k: frozen[k] for k in ('path', 'commit', 'sha256', 'version')}
        old_handoff.update(owner_ref='source-owner', receipt='trusted:handoff')
        self.assertEqual(self.call('verify', evidence=old_handoff)['blob'], frozen['blob'])

    def test_lost_receipt_then_head_reset_does_not_duplicate_commit(self):
        _, receipt = self.create()
        intent = self.freeze_intent(receipt)
        frozen = self.call('freeze', intent=intent)
        # Simulate an external HEAD move; the adapter itself never resets refs.
        self.git('reset', '--soft', intent['baseline'])
        self.assert_code('commit_detached', lambda: self.call('freeze', intent=intent))
        self.assertTrue(self.git('cat-file', '-t', frozen['commit']) == 'commit')

    def test_large_document_and_filtered_bytes_boundary(self):
        _, receipt = self.create(content='confirmed\n' * 1024)
        self.git('config', 'filter.fixture.clean', 'tr a-z A-Z')
        (self.root / '.gitattributes').write_text('*.md filter=fixture\n')
        intent = self.freeze_intent(receipt)
        self.assert_code('filtered_bytes_changed', lambda: self.call('freeze', intent=intent))
        self.assertEqual(self.git('rev-list', '--count', 'HEAD'), '1')


class AttachedRequirementTests(test_entry_prepare.EntrySupport):
    def setUp(self):
        super().setUp()
        initial = discussion_protocol.handle({"protocol_version": 1, "operation": "bootstrap", "project_path": str(self.root),
                    "entry_mode": "explicit-skill", "conversation_ref": "task", "idempotency_key": str(uuid.uuid4()), "root_slug": "topic"})
        self.request["stage"] = 0
        self.request["source"] = {"kind": "discussion", "attachment": {"project_id": initial["project_id"],
                    "tree_id": initial["tree_id"], "actor_topic_id": initial["topic_id"], "actor_conversation_ref": "task"}}
        self.topic_path = Path(initial["topic_document_path"])
        self.ledger_path = Path(initial["ledger_path"])
        self.original_documents = set(self.root.glob("docs/**/*.md"))

    def call(self, operation, **arguments):
        return requirement.handle({"protocol": requirement.PROTOCOL, "operation": operation,
                                   "entry": self.request, **arguments})

    def test_attached_prepare_is_read_only_and_freeze_reuses_authoritative_cp(self):
        head = self.git("rev-parse", "HEAD")
        ledger = self.ledger_path.read_bytes()
        intent = self.call("prepare", purpose="freeze", authorization="confirmed", base_ref="HEAD")
        self.assertEqual(self.ledger_path.read_bytes(), ledger)
        frozen = self.call("freeze", intent=intent)
        self.assertEqual(frozen["source_kind"], "discussion")
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        again = self.call("freeze", intent=intent)
        self.assertEqual(again["commit"], frozen["commit"])
        self.assertEqual(set(self.root.glob("docs/**/*.md")), self.original_documents)
        self.assertEqual(self.call("verify", checkpoint_id=frozen["checkpoint"]["checkpoint_id"])["commit"], frozen["commit"])

    def test_existing_discussion_binding_cannot_be_mislabeled_standalone(self):
        self.request['stage'] = 1
        self.request['source'] = {'kind': 'stage1'}
        self.assert_code('source_changed', lambda: self.call('prepare', purpose='write', path='docs/new.md',
                         version=1, authorization='confirmed', content='second authority'))
        self.assertFalse((self.root / 'docs/new.md').exists())

    def test_dedicated_carrier_freezes_without_controller_recovery_permission(self):
        attachment = self.request['source']['attachment']
        def mutate(operation, owner='task', **parameters):
            topic = entry.topic_read(str(self.root), attachment)
            return discussion_protocol.handle({'protocol_version': 1, 'operation': operation, 'project_path': str(self.root),
                **attachment, 'actor_conversation_ref': owner, 'expected_ledger_revision': topic['ledger_revision'],
                'expected_topic_revision': topic['record_revision'], 'idempotency_key': str(uuid.uuid4()), **parameters})
        handoff = mutate('prepare-handoff', handoff_kind='dedicated-stage', target_slug='topic', scope=['requirements'],
                         work_snapshot={'goal': 'Discuss'}, authoritative_references=[], stage=0)
        mutate('bind-handoff', handoff_id=handoff['handoff_id'], attempt_id=handoff['attempt_id'], conversation_ref='dedicated',
               verified_identity={'project_id': attachment['project_id'], 'tree_id': attachment['tree_id'],
                   'topic_id': attachment['actor_topic_id'], 'handoff_id': handoff['handoff_id'],
                   'attempt_id': handoff['attempt_id'], 'payload_sha256': handoff['payload_sha256']})
        mutate('accept-handoff', owner='dedicated', handoff_id=handoff['handoff_id'], attempt_id=handoff['attempt_id'],
               payload_sha256=handoff['payload_sha256'], source_reference_sha256=handoff['authoritative_references_sha256'], turn_number=1)
        self.settings['thread_id'] = 'dedicated'
        self.request['host'].update(thread_id='dedicated', role='dedicated-discussion', source_ref='task')
        self.request['source']['attachment']['actor_conversation_ref'] = 'dedicated'
        intent = self.call('prepare', purpose='freeze', authorization='confirmed', base_ref='HEAD')
        frozen = self.call('freeze', intent=intent)
        self.assertEqual(frozen['checkpoint']['state'], 'completed')

    def test_attached_lost_commit_result_reconciles_same_cp(self):
        intent = self.call("prepare", purpose="freeze", authorization="confirmed", base_ref="HEAD")
        with patch.dict(os.environ, {"CODEX_DISCUSSION_TEST_FAILPOINT": "git-after-commit-before-result-record"}):
            with self.assertRaises(discussion_protocol.ProtocolError):
                self.call("freeze", intent=intent)
        result = self.call("reconcile", intent=intent)
        self.assertEqual(result["checkpoint"]["state"], "completed")
        self.assertEqual(entry.topic_read(str(self.root), self.request["source"]["attachment"])["checkpoint_count"], 1)

    def test_attached_writes_use_dw_and_stage2_cannot_rewrite_source(self):
        mutation = {"type": "refresh-requirement-narrative", "goal": "confirmed goal", "background": [], "scope": [],
                    "non_goals": [], "scenarios": [], "tentative_assumptions": [], "facts": [], "constraints": [],
                    "acceptance_conditions": [], "direction_change_summary": []}
        intent = self.call("prepare", purpose="write", authorization="confirmed", mutation=mutation)
        self.call("write", intent=intent)
        self.assertIn("confirmed goal", self.topic_path.read_text())
        self.call("reconcile", intent=intent)
        self.request["stage"] = 2
        self.assert_code("invalid_operation", lambda: self.call("prepare", purpose="write", authorization="confirmed", mutation=mutation))

    def test_non_git_discussion_keeps_snapshot_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            initial = discussion_protocol.handle({'protocol_version': 1, 'operation': 'bootstrap', 'project_path': str(root),
                'entry_mode': 'explicit-skill', 'conversation_ref': 'task', 'idempotency_key': str(uuid.uuid4()), 'root_slug': 'non-git'})
            self.request['host']['project_path'] = str(root)
            self.request.pop('target')
            self.request['source']['attachment'].update(project_id=initial['project_id'], tree_id=initial['tree_id'], actor_topic_id=initial['topic_id'])
            self.request = self.with_registration(self.request, project=root, receipt='host:non-git-registry')
            with patch.object(entry.os, 'getcwd', return_value=str(root)):
                intent = self.call('prepare', purpose='freeze', authorization='confirmed', base_ref='non-git')
                result = self.call('freeze', intent=intent)
                self.assertIsNone(result['commit'])
                self.assertIsNone(result['blob'])
                self.assertEqual(result['checkpoint']['storage_kind'], 'non-git')
                self.assertEqual(self.call('reconcile', intent=intent)['checkpoint']['checkpoint_id'], result['checkpoint']['checkpoint_id'])
