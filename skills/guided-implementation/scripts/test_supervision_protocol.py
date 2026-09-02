#!/usr/bin/env python3

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

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
    ) -> dict[str, object]:
        request = self.write_input(
            f"start-{name}.json",
            {
                "branch": f"codex/{name}",
                "repository": str(self.repository),
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
        *,
        allowed_paths: list[str] | None = None,
        protected_paths: list[str] | None = None,
        scope_base_commit: str | None = None,
    ) -> Path:
        return self.write_input(
            f"complete-{name}.json",
            {
                "allowed_paths": ["a.txt", "b.txt"] if allowed_paths is None else allowed_paths,
                "binding": binding,
                "candidate_commit": candidate,
                "expected_target_head": expected_target_head,
                "protected_paths": ["spec.md"] if protected_paths is None else protected_paths,
                "scope_base_commit": scope_base_commit or str(binding["base_commit"]),
            },
        )

    def publish_input(
        self,
        name: str,
        binding: dict[str, object],
        planning_commit: str,
        *,
        allowed_paths: list[str],
        protected_paths: list[str] | None = None,
    ) -> Path:
        return self.write_input(
            f"publish-{name}.json",
            {
                "allowed_paths": allowed_paths,
                "binding": binding,
                "planning_commit": planning_commit,
                "protected_paths": ["spec.md"] if protected_paths is None else protected_paths,
            },
        )

    def test_public_interface_contains_only_the_four_worktree_operations(self) -> None:
        self.assertEqual(
            PROTOCOL.COMMAND_REGISTRY.names,
            (
                "start-worktree",
                "verify-worktree",
                "publish-planning",
                "complete-worktree",
            ),
        )
        self.assertEqual(
            set(PROTOCOL.COMMAND_REGISTRY.names),
            set(PROTOCOL._build_parser()._subparsers._group_actions[0].choices),
        )

    def test_publication_lock_timeout_reports_busy(self) -> None:
        with tempfile.TemporaryFile() as lock_stream:
            with (
                mock.patch.object(
                    PROTOCOL.fcntl,
                    "flock",
                    side_effect=BlockingIOError,
                ),
                mock.patch.object(
                    PROTOCOL.time,
                    "monotonic",
                    side_effect=[10.0, 15.0],
                ),
            ):
                with self.assertRaises(PROTOCOL.ProtocolError) as raised:
                    PROTOCOL._flock_with_timeout(lock_stream)

        self.assertEqual(raised.exception.code, "publication_busy")

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

    def test_start_ignores_uncommitted_tracked_files_in_the_primary_checkout(self) -> None:
        (self.repository / "spec.md").write_text("uncommitted\n", encoding="utf-8")

        binding = self.start("dirty")

        worktree = self.root / "dirty"
        self.assertTrue(worktree.is_dir())
        self.assertEqual(
            (worktree / "spec.md").read_text(encoding="utf-8"),
            "requirement v1\n",
        )
        self.assertEqual(
            (self.repository / "spec.md").read_text(encoding="utf-8"),
            "uncommitted\n",
        )
        self.assertEqual(binding["base_commit"], self.git("rev-parse", "main"))

    def test_complete_supports_empty_protected_paths(self) -> None:
        binding = self.start("closure-docs")
        worktree = Path(str(binding["worktree"]))
        candidate = self.commit(
            worktree,
            "spec.md",
            "requirement archived\n",
            "archive requirement",
        )

        result = PROTOCOL.complete_worktree(
            self.complete_input(
                "closure-docs",
                binding,
                candidate,
                str(binding["base_commit"]),
                allowed_paths=["spec.md"],
                protected_paths=[],
            )
        )

        self.assertEqual(result["state"], "completed")
        self.assertEqual(
            (self.repository / "spec.md").read_text(encoding="utf-8"),
            "requirement archived\n",
        )
        self.assertFalse(worktree.exists())

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
        binding = self.start("complete")
        worktree = Path(str(binding["worktree"]))
        candidate = self.commit(worktree, "a.txt", "a1\n", "change a")
        complete_input = self.complete_input(
            "complete",
            binding,
            candidate,
            str(binding["base_commit"]),
            allowed_paths=["a.txt"],
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

    def test_complete_releases_publication_lock_before_cleanup(self) -> None:
        binding = self.start("lock-scope")
        worktree = Path(str(binding["worktree"]))
        candidate = self.commit(worktree, "a.txt", "a1\n", "change a")
        complete_input = self.complete_input(
            "lock-scope",
            binding,
            candidate,
            str(binding["base_commit"]),
            allowed_paths=["a.txt"],
        )
        original_run_git = PROTOCOL._run_git
        cleanup_lock_probes: list[subprocess.CompletedProcess[str]] = []

        def observe_cleanup(
            repository: Path,
            arguments: list[str],
            *,
            check: bool = True,
        ) -> subprocess.CompletedProcess[str]:
            if arguments[:2] == ["worktree", "remove"]:
                lock_path = repository / ".git" / PROTOCOL.PUBLICATION_LOCK_FILENAME
                cleanup_lock_probes.append(
                    subprocess.run(
                        [
                            sys.executable,
                            "-c",
                            (
                                "import fcntl, os, sys; "
                                "fd = os.open(sys.argv[1], os.O_RDWR); "
                                "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); "
                                "os.close(fd)"
                            ),
                            str(lock_path),
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                )
            return original_run_git(repository, arguments, check=check)

        with mock.patch.object(PROTOCOL, "_run_git", side_effect=observe_cleanup):
            result = PROTOCOL.complete_worktree(complete_input)

        self.assertEqual(result["state"], "completed")
        self.assertEqual(len(cleanup_lock_probes), 1)
        self.assertEqual(cleanup_lock_probes[0].returncode, 0, cleanup_lock_probes[0].stderr)

    def test_complete_rejects_changes_outside_the_declared_paths(self) -> None:
        binding = self.start("scope")
        worktree = Path(str(binding["worktree"]))
        candidate = self.commit(worktree, "b.txt", "b1\n", "change b")

        with self.assertRaises(PROTOCOL.ProtocolError) as raised:
            PROTOCOL.complete_worktree(
                self.complete_input(
                    "scope",
                    binding,
                    candidate,
                    str(binding["base_commit"]),
                    allowed_paths=["a.txt"],
                )
            )

        self.assertEqual(raised.exception.code, "path_outside_scope")
        self.assertTrue(worktree.exists())
        self.assertEqual(self.git("rev-parse", "main"), binding["base_commit"])

    def test_complete_rejects_dirty_or_uncommitted_worktree_changes(self) -> None:
        binding = self.start("dirty-worktree")
        worktree = Path(str(binding["worktree"]))
        candidate = self.commit(worktree, "a.txt", "a1\n", "change a")
        (worktree / "notes.tmp").write_text("uncommitted\n", encoding="utf-8")

        with self.assertRaises(PROTOCOL.ProtocolError) as raised:
            PROTOCOL.complete_worktree(
                self.complete_input(
                    "dirty-worktree",
                    binding,
                    candidate,
                    str(binding["base_commit"]),
                    allowed_paths=["a.txt"],
                )
            )

        self.assertEqual(raised.exception.code, "worktree_not_clean")
        self.assertEqual(self.git("rev-parse", "main"), binding["base_commit"])

    def test_unrelated_target_change_can_be_integrated_and_retested_in_the_worktree(self) -> None:
        binding = self.start("advanced")
        worktree = Path(str(binding["worktree"]))
        (self.repository / "unrelated.txt").write_text("main change\n", encoding="utf-8")
        self.git("add", "unrelated.txt")
        self.git("commit", "-q", "-m", "advance main")
        advanced_head = self.git("rev-parse", "main")
        self.git("merge", "--no-edit", "main", cwd=worktree)
        candidate = self.commit(worktree, "a.txt", "a1\n", "change a")

        result = PROTOCOL.complete_worktree(
            self.complete_input(
                "advanced", binding, candidate, advanced_head, allowed_paths=["a.txt"]
            )
        )

        self.assertEqual(result["state"], "completed")
        self.assertEqual((self.repository / "unrelated.txt").read_text(encoding="utf-8"), "main change\n")
        self.assertEqual((self.repository / "a.txt").read_text(encoding="utf-8"), "a1\n")

    def test_source_change_blocks_integration(self) -> None:
        binding = self.start("source")
        worktree = Path(str(binding["worktree"]))
        (self.repository / "spec.md").write_text("requirement v2\n", encoding="utf-8")
        self.git("add", "spec.md")
        self.git("commit", "-q", "-m", "change source")
        advanced_head = self.git("rev-parse", "main")
        self.git("merge", "--no-edit", "main", cwd=worktree)
        candidate = self.commit(worktree, "a.txt", "a1\n", "change a")

        with self.assertRaises(PROTOCOL.ProtocolError) as raised:
            PROTOCOL.complete_worktree(
                self.complete_input(
                    "source", binding, candidate, advanced_head, allowed_paths=["a.txt"]
                )
            )

        self.assertEqual(raised.exception.code, "source_changed")
        self.assertTrue(worktree.exists())
        self.assertEqual(self.git("rev-parse", "main"), advanced_head)

    def test_renaming_a_source_into_allowed_scope_still_blocks_integration(self) -> None:
        binding = self.start("rename-source")
        worktree = Path(str(binding["worktree"]))
        (worktree / "implementation").mkdir()
        self.git("mv", "spec.md", "implementation/spec.md", cwd=worktree)
        self.git("commit", "-q", "-m", "move source", cwd=worktree)
        candidate = self.git("rev-parse", "HEAD", cwd=worktree)

        with self.assertRaises(PROTOCOL.ProtocolError) as raised:
            PROTOCOL.complete_worktree(
                self.complete_input(
                    "rename-source",
                    binding,
                    candidate,
                    str(binding["base_commit"]),
                    allowed_paths=["implementation"],
                )
            )

        self.assertEqual(raised.exception.code, "source_changed")
        self.assertTrue(worktree.exists())

    def test_two_candidates_from_one_base_serialize_and_the_loser_can_revalidate(self) -> None:
        first = self.start("first")
        second = self.start("second")
        first_worktree = Path(str(first["worktree"]))
        second_worktree = Path(str(second["worktree"]))
        first_candidate = self.commit(first_worktree, "a.txt", "a1\n", "change a")
        second_candidate = self.commit(second_worktree, "b.txt", "b1\n", "change b")
        base = str(first["base_commit"])
        first_input = self.complete_input(
            "first", first, first_candidate, base, allowed_paths=["a.txt"]
        )
        second_input = self.complete_input(
            "second", second, second_candidate, base, allowed_paths=["b.txt"]
        )

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
                "revalidated",
                remaining_binding,
                updated_candidate,
                current_target,
                allowed_paths=(
                    ["b.txt"] if remaining_binding is second else ["a.txt"]
                ),
            )
        )

        self.assertEqual(result["state"], "completed")
        self.assertEqual((self.repository / "a.txt").read_text(encoding="utf-8"), "a1\n")
        self.assertEqual((self.repository / "b.txt").read_text(encoding="utf-8"), "b1\n")
        self.assertFalse(first_worktree.exists())
        self.assertFalse(second_worktree.exists())

    def test_publish_planning_integrates_latest_target_and_retains_the_worktree(self) -> None:
        binding = self.start("planning")
        worktree = Path(str(binding["worktree"]))
        planning_commit = self.commit(
            worktree,
            "plan.md",
            "accepted plan\n",
            "add plan",
        )
        (self.repository / "unrelated.txt").write_text("main change\n", encoding="utf-8")
        self.git("add", "unrelated.txt")
        self.git("commit", "-q", "-m", "advance main")
        target_before_publish = self.git("rev-parse", "main")

        result = PROTOCOL.publish_planning(
            self.publish_input(
                "planning",
                binding,
                planning_commit,
                allowed_paths=["plan.md"],
            )
        )

        self.assertEqual(result["state"], "planning_published")
        self.assertEqual(result["planning_commit"], planning_commit)
        self.assertEqual(self.git("rev-parse", "main"), result["merge_commit"])
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=worktree), result["merge_commit"])
        self.assertTrue(worktree.is_dir())
        self.assertEqual((self.repository / "plan.md").read_text(), "accepted plan\n")
        self.assertEqual((worktree / "unrelated.txt").read_text(), "main change\n")
        parents = self.git("show", "-s", "--format=%P", result["merge_commit"]).split()
        self.assertEqual(parents, [target_before_publish, result["candidate_commit"]])

    def test_publish_planning_preserves_unrelated_primary_checkout_changes(self) -> None:
        binding = self.start("planning-dirty")
        worktree = Path(str(binding["worktree"]))
        planning_commit = self.commit(
            worktree,
            "plan.md",
            "accepted plan\n",
            "add plan",
        )
        (self.repository / "b.txt").write_text("user change\n", encoding="utf-8")

        result = PROTOCOL.publish_planning(
            self.publish_input(
                "planning-dirty",
                binding,
                planning_commit,
                allowed_paths=["plan.md"],
            )
        )

        self.assertEqual(result["state"], "planning_published")
        self.assertEqual((self.repository / "b.txt").read_text(), "user change\n")
        self.assertEqual(self.git("status", "--short"), "M b.txt")
        self.assertTrue(worktree.is_dir())

    def test_publish_planning_rejects_staged_primary_changes_without_touching_them(self) -> None:
        binding = self.start("planning-staged")
        worktree = Path(str(binding["worktree"]))
        planning_commit = self.commit(
            worktree,
            "plan.md",
            "accepted plan\n",
            "add plan",
        )
        target_head = self.git("rev-parse", "main")
        (self.repository / "b.txt").write_text("staged user change\n", encoding="utf-8")
        self.git("add", "b.txt")

        with self.assertRaises(PROTOCOL.ProtocolError) as raised:
            PROTOCOL.publish_planning(
                self.publish_input(
                    "planning-staged",
                    binding,
                    planning_commit,
                    allowed_paths=["plan.md"],
                )
            )

        self.assertEqual(raised.exception.code, "checkout_not_clean")
        self.assertEqual(raised.exception.context["paths"], ["b.txt"])
        self.assertEqual(self.git("rev-parse", "main"), target_head)
        self.assertEqual(self.git("status", "--short"), "M  b.txt")
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=worktree), planning_commit)
        self.assertTrue(worktree.is_dir())

    def test_publish_planning_conflict_preserves_the_original_candidate(self) -> None:
        binding = self.start("planning-conflict")
        worktree = Path(str(binding["worktree"]))
        planning_commit = self.commit(worktree, "a.txt", "flow\n", "change plan")
        (self.repository / "a.txt").write_text("main\n", encoding="utf-8")
        self.git("add", "a.txt")
        self.git("commit", "-q", "-m", "conflicting main change")
        target_head = self.git("rev-parse", "main")

        with self.assertRaises(PROTOCOL.ProtocolError) as raised:
            PROTOCOL.publish_planning(
                self.publish_input(
                    "planning-conflict",
                    binding,
                    planning_commit,
                    allowed_paths=["a.txt"],
                )
            )

        self.assertEqual(raised.exception.code, "planning_conflict")
        self.assertEqual(raised.exception.context["paths"], ["a.txt"])
        self.assertEqual(self.git("rev-parse", "main"), target_head)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=worktree), planning_commit)
        self.assertEqual(self.git("status", "--short", cwd=worktree), "")
        self.assertTrue(worktree.is_dir())

    def test_concurrent_planning_publications_serialize_and_both_continue(self) -> None:
        first = self.start("planning-first")
        second = self.start("planning-second")
        first_worktree = Path(str(first["worktree"]))
        second_worktree = Path(str(second["worktree"]))
        first_commit = self.commit(first_worktree, "plan-a.md", "a\n", "add plan a")
        second_commit = self.commit(second_worktree, "plan-b.md", "b\n", "add plan b")
        first_input = self.publish_input(
            "planning-first", first, first_commit, allowed_paths=["plan-a.md"]
        )
        second_input = self.publish_input(
            "planning-second", second, second_commit, allowed_paths=["plan-b.md"]
        )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda path: PROTOCOL.publish_planning(path),
                    [first_input, second_input],
                )
            )

        self.assertEqual(
            [result["state"] for result in results],
            ["planning_published", "planning_published"],
        )
        self.assertEqual((self.repository / "plan-a.md").read_text(), "a\n")
        self.assertEqual((self.repository / "plan-b.md").read_text(), "b\n")
        self.assertTrue(first_worktree.is_dir())
        self.assertTrue(second_worktree.is_dir())
        self.assertEqual(self.git("status", "--short", cwd=first_worktree), "")
        self.assertEqual(self.git("status", "--short", cwd=second_worktree), "")

    def test_one_flow_worktree_carries_planning_implementation_and_closure(self) -> None:
        binding = self.start("whole-flow")
        worktree = Path(str(binding["worktree"]))
        planning_commit = self.commit(worktree, "plan.md", "accepted\n", "add plan")

        planning = PROTOCOL.publish_planning(
            self.publish_input(
                "whole-flow",
                binding,
                planning_commit,
                allowed_paths=["plan.md"],
            )
        )
        planning_merge = str(planning["merge_commit"])
        self.assertTrue(worktree.is_dir())

        self.commit(worktree, "src/feature.py", "ENABLED = True\n", "implement feature")
        final_candidate = self.commit(
            worktree,
            "plan.md",
            "accepted\nstatus: completed\n",
            "close plan",
        )
        completed = PROTOCOL.complete_worktree(
            self.complete_input(
                "whole-flow",
                binding,
                final_candidate,
                planning_merge,
                allowed_paths=["plan.md", "src"],
                protected_paths=[],
                scope_base_commit=planning_merge,
            )
        )

        self.assertEqual(completed["state"], "completed")
        self.assertFalse(worktree.exists())
        self.assertEqual((self.repository / "src/feature.py").read_text(), "ENABLED = True\n")
        self.assertIn("status: completed", (self.repository / "plan.md").read_text())


if __name__ == "__main__":
    unittest.main()
