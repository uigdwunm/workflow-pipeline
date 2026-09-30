"""Real Git identity reads retain their boundaries while reducing subprocesses."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src/shared/scripts"))
import entry_prepare as entry
import supervision_protocol as supervision


class GitIdentityReadsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repository = self.make_repository("repository")

    def git(self, repository, *arguments):
        return subprocess.run(["git", "-C", str(repository), *arguments],
                              check=True, capture_output=True).stdout.decode().removesuffix("\n")

    def make_repository(self, name, *, commit=True):
        repository = self.root / name
        self.git(self.root, "init", "-q", "-b", "main", str(repository))
        self.git(repository, "config", "user.name", "Fixture")
        self.git(repository, "config", "user.email", "fixture@example.invalid")
        if commit:
            (repository / "baseline.txt").write_text("baseline\n")
            self.git(repository, "add", "baseline.txt")
            self.git(repository, "commit", "-qm", "baseline")
        return repository

    def binding(self, repository=None):
        repository = repository or self.repository
        worktree = self.root / "flow"
        head = self.git(repository, "rev-parse", "HEAD")
        self.git(repository, "worktree", "add", "-q", "-b", "codex/flow", str(worktree), head)
        return {"repository": str(repository), "git_common_dir": str(repository / ".git"),
                "worktree": str(worktree), "branch": "codex/flow", "target_branch": "main",
                "base_commit": head, "lifecycle": {
                    "origin": "reused", "manager": "git", "disposition": "retain",
                    "evidence_ref": "fixture:selection", "creation_receipt": None}}

    def assert_error(self, error_type, code, callback):
        with self.assertRaises(error_type) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def test_entry_batches_common_and_head_after_the_separate_git_probe(self):
        subdirectory = self.repository / "nested"
        subdirectory.mkdir()
        head = self.git(self.repository, "rev-parse", "HEAD")
        with mock.patch.object(entry, "git", wraps=entry.git) as observed:
            facts = entry.repository_facts(str(subdirectory))
        self.assertEqual(facts, {"cwd": str(subdirectory), "root": str(self.repository),
                                 "kind": "git", "git_common_dir": str(self.repository / ".git"),
                                 "branch": "main", "head": head})
        self.assertEqual(observed.call_count, 3)
        self.assertEqual(observed.call_args_list[0].args[1:], ("rev-parse", "--show-toplevel"))
        self.assertFalse(observed.call_args_list[0].kwargs["check"])
        self.assertEqual(observed.call_args_list[1].args[1:],
                         ("rev-parse", "--path-format=absolute", "--git-common-dir", "--verify", "HEAD"))

    def test_entry_detached_head_keeps_none_branch_and_the_commit(self):
        head = self.git(self.repository, "rev-parse", "HEAD")
        self.git(self.repository, "checkout", "-q", "--detach")
        facts = entry.repository_facts(str(self.repository))
        self.assertIsNone(facts["branch"])
        self.assertEqual(facts["head"], head)

    def test_entry_preserves_path_whitespace_and_newlines(self):
        for name in ("space 'quote'\t ", "newline\nending\n"):
            with self.subTest(name=name):
                repository = self.make_repository(name)
                facts = entry.repository_facts(str(repository))
                self.assertEqual(facts["cwd"], str(repository))
                self.assertEqual(facts["root"], str(repository))
                self.assertEqual(facts["git_common_dir"], str(repository / ".git"))
                self.assertEqual(facts["head"], self.git(repository, "rev-parse", "HEAD"))

    def test_non_git_directory_stops_after_probe(self):
        directory = self.root / "non-git"
        directory.mkdir()
        with mock.patch.object(entry, "git", wraps=entry.git) as observed:
            self.assertEqual(entry.repository_facts(str(directory)),
                             {"cwd": str(directory), "root": str(directory), "kind": "non-git"})
        self.assertEqual(observed.call_count, 1)

    def test_detected_unborn_and_invalid_git_head_fail(self):
        unborn = self.make_repository("unborn", commit=False)
        (self.repository / ".git/refs/heads/main").write_text("invalid-object\n")
        for repository in (unborn, self.repository):
            with self.subTest(repository=repository):
                self.assert_error(entry.PreparationError, "git_failed",
                                  lambda: entry.repository_facts(str(repository)))

    def test_entry_rejects_all_redirecting_git_environment_before_running_git(self):
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                     "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
            with self.subTest(name=name), mock.patch.dict(os.environ, {name: "elsewhere"}), \
                    mock.patch.object(entry.subprocess, "run") as run:
                self.assert_error(entry.PreparationError, "git_environment",
                                  lambda: entry.repository_facts(str(self.repository)))
                run.assert_not_called()

    def test_primary_repository_location_uses_one_read(self):
        repository = self.make_repository("space 'quote'\t ")
        with mock.patch.object(supervision, "_run_git", wraps=supervision._run_git) as observed:
            self.assertEqual(supervision._canonical_repository(str(repository)),
                             (repository, repository / ".git"))
        self.assertEqual(observed.call_count, 1)
        self.assertEqual(observed.call_args.args[1],
                         ["rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir"])

    def test_primary_repository_newline_path_uses_separate_location_reads(self):
        repository = self.make_repository("repository\nending\n")
        with mock.patch.object(supervision, "_run_git", wraps=supervision._run_git) as observed:
            self.assertEqual(supervision._canonical_repository(str(repository)),
                             (repository, repository / ".git"))
        self.assertEqual(observed.call_count, 2)

    def test_subdirectory_is_still_rejected_as_primary_repository(self):
        directory = self.repository / "nested"
        directory.mkdir()
        self.assert_error(supervision.ProtocolError, "invalid_repository",
                          lambda: supervision._canonical_repository(str(directory)))

    def test_binding_batches_locations_without_changing_the_result(self):
        binding = self.binding()
        with mock.patch.object(supervision, "_run_git", wraps=supervision._run_git) as observed:
            report = supervision._verify_binding(binding, platform_cwd=Path(binding["worktree"]))
        self.assertEqual(report, {"current_commit": binding["base_commit"], "verified": True})
        location_reads = [call.args[1] for call in observed.call_args_list
                          if call.args[1][:2] == ["rev-parse", "--path-format=absolute"]]
        self.assertEqual(location_reads,
                         [["rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir"]])

    def test_binding_newline_common_directory_uses_separate_location_reads(self):
        repository = self.make_repository("repository\nending\n")
        binding = self.binding(repository)
        with mock.patch.object(supervision, "_run_git", wraps=supervision._run_git) as observed:
            report = supervision._verify_binding(binding, platform_cwd=Path(binding["worktree"]))
        self.assertEqual(report, {"current_commit": binding["base_commit"], "verified": True})
        location_reads = [call.args[1] for call in observed.call_args_list
                          if call.args[1][:2] == ["rev-parse", "--path-format=absolute"]]
        self.assertEqual(location_reads, [["rev-parse", "--path-format=absolute", "--show-toplevel"],
                                         ["rev-parse", "--path-format=absolute", "--git-common-dir"]])

    def test_binding_still_rejects_foreign_common_directory_and_detached_head(self):
        binding = self.binding()
        self.assert_error(supervision.ProtocolError, "worktree_mismatch",
                          lambda: supervision._verify_binding({**binding, "git_common_dir": str(self.root)},
                                                               platform_cwd=Path(binding["worktree"])))
        self.git(Path(binding["worktree"]), "checkout", "-q", "--detach")
        self.assert_error(supervision.ProtocolError, "detached_checkout",
                          lambda: supervision._verify_binding(binding, platform_cwd=Path(binding["worktree"])))

    def test_verify_worktree_cli_keeps_its_original_json_fields(self):
        binding = self.binding()
        result = subprocess.run([sys.executable, str(Path(supervision.__file__)), "verify-worktree"],
                                input=json.dumps({"binding": binding, "platform_cwd": binding["worktree"]}).encode(),
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout),
                         {"binding": binding, "ok": True, "current_commit": binding["base_commit"], "verified": True})


if __name__ == "__main__":
    unittest.main()
