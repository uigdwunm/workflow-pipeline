#!/usr/bin/env python3

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).parent))
import supervision_protocol as PROTOCOL


class WorktreeProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name).resolve()
        self.repository = self.root / "repository"
        subprocess.run(
            ["git", "init", "-q", "-b", "main", str(self.repository)],
            check=True,
        )
        self.git("config", "user.name", "Workflow Test")
        self.git("config", "user.email", "workflow@example.invalid")
        (self.repository / "spec.md").write_text("requirement v1\n", encoding="utf-8")
        (self.repository / "a.txt").write_text("a0\n", encoding="utf-8")
        (self.repository / "b.txt").write_text("b0\n", encoding="utf-8")
        self.git("add", "spec.md", "a.txt", "b.txt")
        self.git("commit", "-q", "-m", "initial")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def git(self, *arguments: str, cwd: Path | None = None) -> str:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=cwd or self.repository,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return completed.stdout.strip()

    def write_input(self, name: str, value: dict[str, object]) -> Path:
        path = self.root / name
        path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
        return path

    def start(
        self,
        name: str,
        *,
        allowed_paths: list[str] | None = None,
        source_paths: list[str] | None = None,
    ) -> dict[str, object]:
        request = self.write_input(
            f"start-{name}.json",
            {
                "allowed_paths": allowed_paths or ["a.txt", "b.txt"],
                "branch": f"codex/{name}",
                "repository": str(self.repository),
                "source_paths": source_paths or ["spec.md"],
                "target_branch": "main",
                "worktree": str(self.root / name),
            },
        )
        return PROTOCOL.start_worktree(request)["binding"]

    def commit(self, worktree: Path, path: str, contents: str, message: str) -> str:
        target = worktree / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
        self.git("add", "--", path, cwd=worktree)
        self.git("commit", "-q", "-m", message, cwd=worktree)
        return self.git("rev-parse", "HEAD", cwd=worktree)

    def complete_input(
        self,
        name: str,
        binding: dict[str, object],
        candidate: str,
        expected_target_head: str,
    ) -> Path:
        return self.write_input(
            f"complete-{name}.json",
            {
                "binding": binding,
                "candidate_commit": candidate,
                "expected_target_head": expected_target_head,
            },
        )

    def test_public_interface_contains_only_the_three_worktree_operations(self) -> None:
        self.assertEqual(
            PROTOCOL.COMMAND_REGISTRY.names,
            ("start-worktree", "verify-worktree", "complete-worktree"),
        )
        self.assertEqual(
            set(PROTOCOL.COMMAND_REGISTRY.names),
            set(PROTOCOL._build_parser()._subparsers._group_actions[0].choices),
        )

    def test_start_creates_one_worktree_from_the_committed_target(self) -> None:
        (self.repository / "untracked-notes.md").write_text(
            "not part of the run\n", encoding="utf-8"
        )

        binding = self.start("one")

        worktree = Path(str(binding["worktree"]))
        self.assertEqual(binding["base_commit"], self.git("rev-parse", "main"))
        self.assertEqual(self.git("branch", "--show-current", cwd=worktree), "codex/one")
        self.assertEqual((worktree / "spec.md").read_text(encoding="utf-8"), "requirement v1\n")
        self.assertFalse((worktree / "untracked-notes.md").exists())
        self.assertFalse(any(self.repository.glob(".git/*lease*")))
        self.assertFalse(any(self.repository.glob(".git/*claim*")))

    def test_start_rejects_uncommitted_tracked_files(self) -> None:
        (self.repository / "spec.md").write_text("uncommitted\n", encoding="utf-8")

        with self.assertRaises(PROTOCOL.ProtocolError) as raised:
            self.start("dirty")

        self.assertEqual(raised.exception.code, "checkout_not_clean")
        self.assertFalse((self.root / "dirty").exists())

    def test_start_requires_at_least_one_committed_source_path(self) -> None:
        request = self.write_input(
            "start-no-source.json",
            {
                "allowed_paths": ["a.txt"],
                "branch": "codex/no-source",
                "repository": str(self.repository),
                "source_paths": [],
                "target_branch": "main",
                "worktree": str(self.root / "no-source"),
            },
        )

        with self.assertRaises(PROTOCOL.ProtocolError) as raised:
            PROTOCOL.start_worktree(request)

        self.assertEqual(raised.exception.code, "invalid_input")

    def test_verify_uses_git_as_the_only_worktree_authority(self) -> None:
        binding = self.start("verify")
        verify_input = self.write_input(
            "verify.json",
            {"binding": binding, "platform_cwd": binding["worktree"]},
        )

        verified = PROTOCOL.verify_worktree(verify_input)

        self.assertTrue(verified["verified"])
        wrong_input = self.write_input(
            "verify-wrong.json",
            {"binding": binding, "platform_cwd": str(self.repository)},
        )
        with self.assertRaises(PROTOCOL.ProtocolError) as raised:
            PROTOCOL.verify_worktree(wrong_input)
        self.assertEqual(raised.exception.code, "worktree_mismatch")

    def test_complete_integrates_and_removes_the_worktree_and_branch(self) -> None:
        binding = self.start("complete", allowed_paths=["a.txt"])
        worktree = Path(str(binding["worktree"]))
        candidate = self.commit(worktree, "a.txt", "a1\n", "change a")
        complete_input = self.complete_input(
            "complete", binding, candidate, str(binding["base_commit"])
        )

        result = PROTOCOL.complete_worktree(complete_input)

        self.assertEqual(result["state"], "completed")
        self.assertEqual((self.repository / "a.txt").read_text(encoding="utf-8"), "a1\n")
        self.assertFalse(worktree.exists())
        self.assertEqual(
            subprocess.run(
                ["git", "show-ref", "--verify", "--quiet", "refs/heads/codex/complete"],
                cwd=self.repository,
            ).returncode,
            1,
        )
        parents = self.git("show", "-s", "--format=%P", result["merge_commit"]).split()
        self.assertEqual(parents, [binding["base_commit"], candidate])
        self.assertEqual(
            self.git("rev-parse", f"{result['merge_commit']}^{{tree}}"),
            self.git("rev-parse", f"{candidate}^{{tree}}"),
        )

    def test_complete_rejects_changes_outside_the_declared_paths(self) -> None:
        binding = self.start("scope", allowed_paths=["a.txt"])
        worktree = Path(str(binding["worktree"]))
        candidate = self.commit(worktree, "b.txt", "b1\n", "change b")

        with self.assertRaises(PROTOCOL.ProtocolError) as raised:
            PROTOCOL.complete_worktree(
                self.complete_input(
                    "scope", binding, candidate, str(binding["base_commit"])
                )
            )

        self.assertEqual(raised.exception.code, "path_outside_scope")
        self.assertTrue(worktree.exists())
        self.assertEqual(self.git("rev-parse", "main"), binding["base_commit"])

    def test_complete_rejects_dirty_or_uncommitted_worktree_changes(self) -> None:
        binding = self.start("dirty-worktree", allowed_paths=["a.txt"])
        worktree = Path(str(binding["worktree"]))
        candidate = self.commit(worktree, "a.txt", "a1\n", "change a")
        (worktree / "notes.tmp").write_text("uncommitted\n", encoding="utf-8")

        with self.assertRaises(PROTOCOL.ProtocolError) as raised:
            PROTOCOL.complete_worktree(
                self.complete_input(
                    "dirty-worktree", binding, candidate, str(binding["base_commit"])
                )
            )

        self.assertEqual(raised.exception.code, "worktree_not_clean")
        self.assertEqual(self.git("rev-parse", "main"), binding["base_commit"])

    def test_unrelated_target_change_can_be_integrated_and_retested_in_the_worktree(self) -> None:
        binding = self.start("advanced", allowed_paths=["a.txt"])
        worktree = Path(str(binding["worktree"]))
        (self.repository / "unrelated.txt").write_text("main change\n", encoding="utf-8")
        self.git("add", "unrelated.txt")
        self.git("commit", "-q", "-m", "advance main")
        advanced_head = self.git("rev-parse", "main")
        self.git("merge", "--no-edit", "main", cwd=worktree)
        candidate = self.commit(worktree, "a.txt", "a1\n", "change a")

        result = PROTOCOL.complete_worktree(
            self.complete_input("advanced", binding, candidate, advanced_head)
        )

        self.assertEqual(result["state"], "completed")
        self.assertEqual((self.repository / "unrelated.txt").read_text(encoding="utf-8"), "main change\n")
        self.assertEqual((self.repository / "a.txt").read_text(encoding="utf-8"), "a1\n")

    def test_source_change_blocks_integration(self) -> None:
        binding = self.start("source", allowed_paths=["a.txt"])
        worktree = Path(str(binding["worktree"]))
        (self.repository / "spec.md").write_text("requirement v2\n", encoding="utf-8")
        self.git("add", "spec.md")
        self.git("commit", "-q", "-m", "change source")
        advanced_head = self.git("rev-parse", "main")
        self.git("merge", "--no-edit", "main", cwd=worktree)
        candidate = self.commit(worktree, "a.txt", "a1\n", "change a")

        with self.assertRaises(PROTOCOL.ProtocolError) as raised:
            PROTOCOL.complete_worktree(
                self.complete_input("source", binding, candidate, advanced_head)
            )

        self.assertEqual(raised.exception.code, "source_changed")
        self.assertTrue(worktree.exists())
        self.assertEqual(self.git("rev-parse", "main"), advanced_head)

    def test_renaming_a_source_into_allowed_scope_still_blocks_integration(self) -> None:
        binding = self.start("rename-source", allowed_paths=["implementation"])
        worktree = Path(str(binding["worktree"]))
        (worktree / "implementation").mkdir()
        self.git("mv", "spec.md", "implementation/spec.md", cwd=worktree)
        self.git("commit", "-q", "-m", "move source", cwd=worktree)
        candidate = self.git("rev-parse", "HEAD", cwd=worktree)

        with self.assertRaises(PROTOCOL.ProtocolError) as raised:
            PROTOCOL.complete_worktree(
                self.complete_input(
                    "rename-source", binding, candidate, str(binding["base_commit"])
                )
            )

        self.assertEqual(raised.exception.code, "source_changed")
        self.assertTrue(worktree.exists())

    def test_two_candidates_from_one_base_serialize_and_the_loser_can_revalidate(self) -> None:
        first = self.start("first", allowed_paths=["a.txt"])
        second = self.start("second", allowed_paths=["b.txt"])
        first_worktree = Path(str(first["worktree"]))
        second_worktree = Path(str(second["worktree"]))
        first_candidate = self.commit(first_worktree, "a.txt", "a1\n", "change a")
        second_candidate = self.commit(second_worktree, "b.txt", "b1\n", "change b")
        base = str(first["base_commit"])
        first_input = self.complete_input("first", first, first_candidate, base)
        second_input = self.complete_input("second", second, second_candidate, base)

        def complete(path: Path) -> tuple[str, object]:
            try:
                return "ok", PROTOCOL.complete_worktree(path)
            except PROTOCOL.ProtocolError as error:
                return "error", error

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(complete, [first_input, second_input]))

        self.assertEqual([kind for kind, _ in outcomes].count("ok"), 1)
        failure = next(value for kind, value in outcomes if kind == "error")
        self.assertEqual(failure.code, "target_changed")

        if second_worktree.exists():
            remaining_binding = second
            remaining_worktree = second_worktree
        else:
            remaining_binding = first
            remaining_worktree = first_worktree
        self.git("merge", "--no-edit", "main", cwd=remaining_worktree)
        updated_candidate = self.git("rev-parse", "HEAD", cwd=remaining_worktree)
        current_target = self.git("rev-parse", "main")

        result = PROTOCOL.complete_worktree(
            self.complete_input(
                "revalidated", remaining_binding, updated_candidate, current_target
            )
        )

        self.assertEqual(result["state"], "completed")
        self.assertEqual((self.repository / "a.txt").read_text(encoding="utf-8"), "a1\n")
        self.assertEqual((self.repository / "b.txt").read_text(encoding="utf-8"), "b1\n")
        self.assertFalse(first_worktree.exists())
        self.assertFalse(second_worktree.exists())


if __name__ == "__main__":
    unittest.main()
