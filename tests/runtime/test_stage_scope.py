"""Exact stage-scope evidence against a real, temporary Git repository."""
from __future__ import annotations

import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src/shared/scripts"))
import entry_prepare as entry
import stage_scope as scope


class StageScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "scope@example.test")
        self.git("config", "user.name", "Scope Test")
        (self.root / "docs").mkdir()
        (self.root / "docs/requirement.md").write_text("# Requirement\n- Deliver feature\n")
        (self.root / "app.py").write_text("old = True\n")
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.baseline = self.git("rev-parse", "HEAD")
        (self.root / "docs/spec.md").write_text("# Design\n## D1\nImplement app.py and test_app.py.\n")
        self.git("add", "docs/spec.md")
        self.git("commit", "-qm", "plan")
        self.candidate = self.git("rev-parse", "HEAD")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], capture_output=True,
                              text=True, check=True).stdout.strip()

    def item(self, path, *, owner="implementation", kind="source", operation="add", sources=None):
        result = {"path": path, "owner": owner, "kind": kind, "operation": operation,
                  "basis": {"commit": self.candidate, "path": "docs/spec.md", "section": "## D1"}}
        if kind == "generated":
            result["source_paths"] = sources or ["app.py"]
        return result

    def manifest(self):
        return [self.item("app.py", operation="modify"),
                self.item("generated/app.py", kind="generated", sources=["app.py"]),
                self.item("test_app.py", kind="test")]

    def sealed(self, manifest=None):
        manifest = self.manifest() if manifest is None else manifest
        projected = scope.manifest_projection(manifest)
        return {"baseline": self.baseline, "owned_paths": ["docs/spec.md"],
                "protected_paths": ["docs/requirement.md"], **projected,
                "downstream": {"state": "sealed", "revision": 1,
                               "implementation_required": bool(projected["implementation_paths"]),
                               "manifest": manifest, "proposal_ref": "proposal-1", "seal_ref": "seal-1"}}

    def test_unresolved_is_distinct_from_explicit_empty_planning_only(self):
        unresolved = {"baseline": self.baseline, "owned_paths": ["docs/spec.md"],
                      "protected_paths": ["docs/requirement.md"],
                      "implementation_paths": None, "closure_paths": None,
                      "downstream": {"state": "unresolved", "revision": 0,
                                     "implementation_required": None, "manifest": None,
                                     "proposal_ref": None, "seal_ref": None}}
        self.assertEqual(scope.validate_scope(unresolved, self.root, stage=2), unresolved)
        with self.assertRaises(entry.PreparationError):
            scope.validate_scope(unresolved, self.root, stage=3)
        invalid = copy.deepcopy(unresolved)
        invalid["implementation_paths"] = []
        self.assert_error("invalid_scope", lambda: scope.validate_scope(invalid, self.root, stage=2))
        empty = self.sealed([])
        empty["downstream"]["implementation_required"] = False
        self.assertEqual(scope.validate_scope(empty, self.root, stage=2,
                         requirement_path="docs/requirement.md", candidate_commit=self.candidate), empty)

    def test_manifest_projection_basis_and_generated_closure(self):
        sealed = self.sealed()
        self.assertEqual(scope.validate_scope(sealed, self.root, stage=3,
                         requirement_path="docs/requirement.md", planning_paths=["docs/spec.md"],
                         candidate_commit=self.candidate,
                         generated_outputs={"app.py": ["generated/app.py"]}), sealed)
        self.assert_error("invalid_scope", lambda: scope.validate_scope(sealed, self.root, stage=3,
                          requirement_path="docs/requirement.md", planning_paths=["docs/spec.md"],
                          candidate_commit=self.candidate, generated_outputs={}))
        self.assertEqual(scope.validate_manifest(self.manifest(), self.root, self.candidate,
                         baseline=self.baseline, protected_paths=["docs/requirement.md"],
                         planning_paths=["docs/spec.md"], generated_outputs={"app.py": ["generated/app.py"]}),
                         {"implementation_paths": ["app.py", "generated/app.py", "test_app.py"],
                          "closure_paths": []})
        missing = [self.item("app.py", operation="modify"), self.item("test_app.py", kind="test")]
        self.assert_error("scope_incomplete", lambda: scope.validate_manifest(missing, self.root,
                          self.candidate, baseline=self.baseline, planning_paths=["docs/spec.md"],
                          generated_outputs={"app.py": ["generated/app.py"]}))
        self.assert_error("scope_incomplete", lambda: scope.validate_manifest(self.manifest(), self.root,
                          self.candidate, baseline=self.baseline, planning_paths=["docs/spec.md"],
                          generated_outputs={"app.py": ["generated/app.py", "generated/missing.py"]}))
        self.assert_error("invalid_scope", lambda: scope.validate_manifest(self.manifest(), self.root,
                          self.candidate, baseline=self.baseline, planning_paths=["docs/spec.md"],
                          generated_outputs={"test_app.py": ["generated/app.py"]}))
        existing_source = [self.item("generated/requirement-copy.md", kind="generated",
                                     sources=["docs/requirement.md"])]
        self.assertEqual(scope.validate_manifest(existing_source, self.root, self.candidate,
                         baseline=self.baseline, planning_paths=["docs/spec.md"])["implementation_paths"],
                         ["generated/requirement-copy.md"])
        existing_source[0]["source_paths"] = ["missing-source.py"]
        self.assert_error("invalid_scope", lambda: scope.validate_manifest(existing_source, self.root,
                          self.candidate, baseline=self.baseline, planning_paths=["docs/spec.md"]))

    def test_rejects_basis_not_in_candidate_or_missing_section(self):
        item = self.item("app.py", operation="modify")
        item["basis"]["section"] = "missing decision"
        self.assert_error("invalid_scope", lambda: scope.validate_manifest([item], self.root,
                          self.candidate, baseline=self.baseline, planning_paths=["docs/spec.md"]))
        item["basis"]["section"] = "## D1"
        self.assert_error("invalid_scope", lambda: scope.validate_manifest([item], self.root,
                          self.baseline, baseline=self.baseline, planning_paths=["docs/spec.md"]))

    def test_rejects_aliases_links_directories_and_conflicts(self):
        for relative in ("/tmp/x", "../escape", "docs//bad", "docs/./bad", "foo/*.py", ".git/config",
                         "docs/.GIT/config", "C:/temp/file"):
            with self.subTest(relative=relative):
                self.assert_error("invalid_scope", lambda p=relative: scope.validate_path(p, self.root))
        (self.root / "link").symlink_to(self.root / "docs", target_is_directory=True)
        self.assert_error("invalid_scope", lambda: scope.validate_path("link/file.md", self.root))
        self.assert_error("invalid_scope", lambda: scope.validate_path("docs", self.root))
        self.assert_error("invalid_scope", lambda: scope.validate_path("APP.py", self.root))
        item = self.item("docs/spec.md", owner="closure", kind="document", operation="add")
        self.assertEqual(scope.validate_manifest([item], self.root, self.candidate,
                         baseline=self.baseline, planning_paths=["docs/spec.md"])["closure_paths"], ["docs/spec.md"])
        self.assertEqual(scope.validate_manifest([item], self.root, self.candidate,
                         baseline=self.baseline, planning_paths=["docs/spec.md"],
                         protected_paths=["docs/requirement.md", "docs/spec.md"],
                         requirement_path="docs/requirement.md")["closure_paths"], ["docs/spec.md"])
        self.assert_error("invalid_scope", lambda: scope.validate_manifest([item], self.root,
                          self.candidate, baseline=self.baseline, protected_paths=["docs/spec.md"]))

    def test_rejects_aliases_in_absent_directory_components_across_scope_roles(self):
        self.assertEqual(scope.validate_paths(["Foo/a.py", "Foo/b.py"], self.root),
                         ["Foo/a.py", "Foo/b.py"])
        self.assert_error("invalid_scope", lambda: scope.validate_paths(
            ["Foo/a.py", "foo/b.py"], self.root))
        manifest = [self.item("Foo/a.py"), self.item("foo/b.py", kind="test")]
        self.assert_error("invalid_scope", lambda: scope.validate_manifest(
            manifest, self.root, self.candidate, baseline=self.baseline,
            planning_paths=["docs/spec.md"]))
        self.assert_error("invalid_scope", lambda: scope.validate_manifest(
            [self.item("foo/b.py")], self.root, self.candidate, baseline=self.baseline,
            protected_paths=["Foo/frozen.md"], planning_paths=["docs/spec.md"]))
        unresolved = {"baseline": self.baseline, "owned_paths": ["Foo/plan.md"],
            "protected_paths": ["foo/frozen.md"], "implementation_paths": None,
            "closure_paths": None, "downstream": {"state": "unresolved", "revision": 0,
                "implementation_required": None, "manifest": None,
                "proposal_ref": None, "seal_ref": None}}
        self.assert_error("invalid_scope", lambda: scope.validate_scope(unresolved, self.root, stage=2))
        self.assert_error("invalid_scope", lambda: scope.validate_paths(
            ["new-file", "new-file/child.py"], self.root))

    def test_successor_uses_original_manifest_baseline_for_planning_addition(self):
        manifest = [self.item("app.py", operation="modify"),
                    self.item("docs/spec.md", owner="closure", kind="document"),
                    self.item("test_app.py", kind="test")]
        successor = self.sealed(manifest)
        successor["baseline"] = self.candidate
        successor["owned_paths"] = ["app.py"]
        successor["protected_paths"] = ["docs/requirement.md", "docs/spec.md"]
        self.assert_error("invalid_scope", lambda: scope.validate_scope(successor, self.root, stage=3,
                          requirement_path="docs/requirement.md", planning_paths=["docs/spec.md"],
                          candidate_commit=self.candidate))
        self.assertEqual(scope.validate_scope(successor, self.root, stage=3,
                         requirement_path="docs/requirement.md", planning_paths=["docs/spec.md"],
                         candidate_commit=self.candidate, manifest_baseline=self.baseline), successor)

    def test_standalone_implementation_uses_requirement_basis_without_planning(self):
        item = self.item("app.py", operation="modify")
        item["basis"] = {"commit": self.baseline, "path": "docs/requirement.md",
                         "section": "Deliver feature"}
        direct = self.sealed([item])
        direct["owned_paths"] = ["app.py"]
        self.assertEqual(scope.validate_scope(direct, self.root, stage=3,
                         requirement_path="docs/requirement.md"), direct)
        direct["downstream"]["manifest"][0]["basis"]["commit"] = self.candidate
        self.assert_error("invalid_scope", lambda: scope.validate_scope(direct, self.root,
                          stage=3, requirement_path="docs/requirement.md"))

    def test_operation_projection_diff_and_snapshot(self):
        wrong = self.item("app.py", operation="add")
        self.assert_error("invalid_scope", lambda: scope.validate_manifest([wrong], self.root,
                          self.candidate, baseline=self.baseline, planning_paths=["docs/spec.md"]))
        manifest = self.manifest()
        changed = copy.deepcopy(manifest)
        changed[0]["basis"]["section"] = "# Design"
        diff = scope.manifest_diff(manifest, changed)
        self.assertEqual([item["before"]["path"] for item in diff["changed"]], ["app.py"])
        self.assertEqual(diff["added"], [])
        self.assertEqual(diff["removed"], [])
        snapshot = {"head": self.candidate, "index_hash": "abc", "files": {"app.py": "hash"}}
        self.assertEqual(scope.validate_snapshot(snapshot, copy.deepcopy(snapshot)), snapshot)
        changed_snapshot = copy.deepcopy(snapshot)
        changed_snapshot["files"]["app.py"] = "other"
        self.assert_error("scope_changed", lambda: scope.validate_snapshot(snapshot, changed_snapshot))

    def test_large_manifest_batches_tree_reads_and_rechecks_each_new_call(self):
        manifest = [self.item(f"new/file-{index:03d}.py") for index in range(146)]
        with patch.object(entry, "git", wraps=entry.git) as git:
            projected = scope.validate_manifest(manifest, self.root, self.candidate,
                baseline=self.baseline, planning_paths=["docs/spec.md"])
        self.assertEqual(len(projected["implementation_paths"]), 146)
        self.assertLess(git.call_count, 20)
        self.assertEqual(sum(call.args[1] == "ls-tree" for call in git.call_args_list), 3)
        changed_basis = copy.deepcopy(manifest)
        changed_basis[-1]["basis"]["section"] = "missing decision"
        with patch.object(entry, "git", wraps=entry.git) as checked_again:
            self.assert_error("invalid_scope", lambda: scope.validate_manifest(changed_basis,
                self.root, self.candidate, baseline=self.baseline,
                planning_paths=["docs/spec.md"]))
        self.assertGreater(checked_again.call_count, 0)
        (self.root / "new").symlink_to(self.root / "docs", target_is_directory=True)
        self.assert_error("invalid_scope", lambda: scope.validate_manifest(manifest,
            self.root, self.candidate, baseline=self.baseline,
            planning_paths=["docs/spec.md"]))

    def assert_error(self, code, action):
        with self.assertRaises(entry.PreparationError) as raised:
            action()
        self.assertEqual(raised.exception.code, code)


if __name__ == "__main__":
    unittest.main()
