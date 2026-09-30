"""Committed document reuse keeps current files, index and object views live."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import test_workflow_progress as existing

progress = existing.progress
transfer = existing.transfer


class HandoffDocumentReadsTests(existing.ProgressTests):
    def git_with_index(self, *args, index=None):
        environment = dict(os.environ)
        if index is not None:
            environment['GIT_INDEX_FILE'] = str(index)
        process = subprocess.Popen(['git', '-C', str(self.root), *args], env=environment,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate()
        self.assertEqual(process.returncode, 0, stderr.decode())
        return stdout.decode().strip()

    def replacement(self, *, executable=False):
        with tempfile.TemporaryDirectory() as directory:
            index = Path(directory) / 'index'
            self.git_with_index('read-tree', self.frozen['commit'], index=index)
            if executable:
                self.git_with_index('update-index', '--chmod=+x', self.requirement_path, index=index)
            tree = self.git_with_index('write-tree', index=index)
        parents = self.git_with_index('rev-list', '--parents', '-n', '1', self.frozen['commit']).split()[1:]
        arguments = ['commit-tree', tree, '-m', 'replacement fixture']
        for parent in parents:
            arguments += ['-p', parent]
        return self.git_with_index(*arguments)

    def assert_change_during_final_delivery_is_rejected(self, change):
        in_launch = changed = False
        original_launch = progress.Progress.verify_launch
        original_documents = transfer.requirement.verify_target_documents

        def launch(owner):
            nonlocal in_launch
            in_launch = True
            try:
                return original_launch(owner)
            finally:
                in_launch = False

        def documents(*args, **kwargs):
            nonlocal changed
            if in_launch:
                self.assertFalse(changed)
                changed = True
                change()
            return original_documents(*args, **kwargs)

        with patch.object(progress.Progress, 'verify_launch', launch), \
                patch.object(transfer.requirement, 'verify_target_documents', documents), \
                patch.object(progress.Progress, 'effect', autospec=True,
                             side_effect=progress.Progress.effect) as effect:
            with self.assertRaises((transfer.entry.PreparationError, transfer.control.ControlError)):
                self.invoke('start', {'handoff': self.input,
                    'control': {'context': self.context, 'discussion': None}})
            self.assertTrue(changed)
            self.assertNotIn('invoke-host', [call.args[1] for call in effect.call_args_list])
        self.assertNotEqual((self.state().get('action') or {}).get('operation'), 'invoke-host')

    def test_replacement_added_during_final_delivery_is_rejected(self):
        replacement = self.replacement(executable=True)
        self.assert_change_during_final_delivery_is_rejected(
            lambda: self.git_with_index('replace', self.frozen['commit'], replacement))

    def test_custom_replacement_namespace_is_rechecked(self):
        replacement = self.replacement(executable=True)
        with patch.dict(os.environ, {'GIT_REPLACE_REF_BASE': 'refs/test-replacements/'}):
            self.assert_change_during_final_delivery_is_rejected(
                lambda: self.git_with_index('replace', self.frozen['commit'], replacement))

    def test_replacement_configuration_change_is_rejected(self):
        self.git_with_index('replace', self.frozen['commit'], self.replacement(executable=True))
        self.git_with_index('config', 'core.useReplaceRefs', 'false')
        self.assert_change_during_final_delivery_is_rejected(
            lambda: self.git_with_index('config', 'core.useReplaceRefs', 'true'))

    def test_stable_replacement_with_identical_document_is_accepted(self):
        self.git_with_index('replace', self.frozen['commit'], self.replacement())
        self.assertEqual(self.begin()['next_action']['operation'], 'invoke-host')

    def test_primary_working_bytes_are_read_after_committed_bytes(self):
        self.assert_change_during_final_delivery_is_rejected(
            lambda: (self.root / self.requirement_path).write_text('concurrent primary edit\n'))

    def test_primary_index_is_read_after_committed_bytes(self):
        self.assert_change_during_final_delivery_is_rejected(
            lambda: self.git_with_index('update-index', '--chmod=+x', self.requirement_path))


def load_tests(loader, tests, pattern):
    names = sorted(name for name in vars(HandoffDocumentReadsTests) if name.startswith('test_'))
    return unittest.TestSuite(HandoffDocumentReadsTests(name) for name in names)
