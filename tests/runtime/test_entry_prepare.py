"""Real Git entry evidence with only the host/runtime boundary substituted."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src/shared/scripts"))
import entry_prepare as entry


class EntrySupport(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.root / "existing.txt").write_text("baseline\n")
        self.git("add", "existing.txt")
        self.git("commit", "-qm", "baseline")
        self.settings = {"protocol": "thread-settings-v5", "source": "fixture-runtime", "thread_id": "task",
                         "model": "fixture-model", "reasoning_effort": "high", "turn_id": "turn-1"}
        self.package = {"root": str(self.root / "package"), "compatibility_key": {"preparation": "v1"}}
        for mocked in (patch.object(entry.os, "getcwd", return_value=str(self.root)),
                       patch.object(entry.thread_settings, "resolve_current_thread_settings", side_effect=lambda: dict(self.settings)),
                       patch.object(entry.skill_preflight, "preflight", return_value={"packages": {"problem-framing": self.package}, "external": {}}),
                       patch.object(entry.skill_preflight, "verify_identity", side_effect=lambda identity: identity)):
            mocked.start()
            self.addCleanup(mocked.stop)
        self.request = {"protocol": entry.PROTOCOL, "operation": "resolve", "stage": 1, "action": "entry",
                        "host": {"project_path": str(self.root), "project_id": "project", "thread_id": "task",
                                 "controller_ref": "task", "role": "controller", "source_ref": None, "receipt": "tool:project"},
                        "source": {"kind": "stage1"},
                        "target": {"kind": "planning", "repository": str(self.root), "branch": "main"}}

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, check=True).stdout.decode().strip()

    def assert_code(self, code, callback):
        with self.assertRaises(entry.PreparationError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)


class EntryTests(EntrySupport):
    def test_collects_actual_repository_and_rechecks_settings_without_freezing_turn(self):
        result = entry.resolve(self.request)
        self.assertEqual(result["repository"]["head"], self.git("rev-parse", "HEAD"))
        self.settings["turn_id"] = "turn-2"
        verify = {**self.request, "operation": "verify", "expected": result}
        self.assertEqual(entry.resolve(verify)["configuration"]["turn_id"], "turn-2")
        self.settings["model"] = "changed"
        self.assert_code("configuration_changed", lambda: entry.resolve(verify))

    def test_wrong_host_project_task_role_and_source_fail(self):
        for key, value, code in (("thread_id", "another", "identity_mismatch"),
                                 ("project_path", str(self.root.parent), "project_mismatch"),
                                 ("role", "closure-agent", "role_mismatch")):
            request = copy.deepcopy(self.request)
            request["host"][key] = value
            self.assert_code(code, lambda: entry.resolve(request))
        request = {**self.request, "source": {"kind": "conversation"}}
        self.assert_code("invalid_source", lambda: entry.resolve(request))

    def test_pinned_package_survives_registry_switch_but_must_still_verify(self):
        expected = entry.resolve(self.request)
        entry.skill_preflight.preflight.return_value = {"packages": {"problem-framing": {**self.package, "root": "new-root"}}, "external": {}}
        observed = entry.resolve({**self.request, "operation": "verify", "expected": expected})
        self.assertEqual(observed["packages"], expected["packages"])
        entry.skill_preflight.verify_identity.assert_called_with(expected["packages"]["problem-framing"])

    def test_registry_query_uses_actual_cwd_and_explicit_snapshot_is_preserved(self):
        entry.resolve(self.request)
        self.assertEqual(entry.skill_preflight.preflight.call_args.args[0]["registry_query"], {"cwd": str(self.root)})
        snapshot = {"source": "host-current-skills", "entries": []}
        entry.resolve({**self.request, "registry": snapshot})
        self.assertEqual(entry.skill_preflight.preflight.call_args.args[0]["registry"], snapshot)

    def test_duplicate_json_and_redirected_git_environment_fail(self):
        self.assert_code("invalid_request", lambda: entry.decode(b'{"operation":1,"operation":2}'))
        with patch.dict(os.environ, {"GIT_INDEX_FILE": "elsewhere"}):
            self.assert_code("git_environment", lambda: entry.resolve(self.request))

    def test_symlink_document_and_target_mismatch_fail(self):
        (self.root / "linked").symlink_to(self.root.parent, target_is_directory=True)
        self.assert_code("invalid_path", lambda: entry.resolve({**self.request, "source": {"kind": "stage1", "path": "linked/a.md"}}))
        request = copy.deepcopy(self.request)
        request["target"]["branch"] = "other"
        self.assert_code("target_mismatch", lambda: entry.resolve(request))
