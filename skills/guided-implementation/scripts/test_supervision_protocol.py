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

    def run_cli(
        self,
        command: str,
        input_data: bytes,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [sys.executable, str(Path(PROTOCOL.__file__)), command],
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def start(
        self,
        name: str,
    ) -> dict[str, object]:
        request = {
            "branch": f"codex/{name}",
            "repository": str(self.repository),
            "target_branch": "main",
            "worktree": str(self.root / name),
        }
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
    ) -> dict[str, object]:
        return {
            "allowed_paths": ["a.txt", "b.txt"] if allowed_paths is None else allowed_paths,
            "binding": binding,
            "candidate_commit": candidate,
            "expected_target_head": expected_target_head,
            "protected_paths": ["spec.md"] if protected_paths is None else protected_paths,
            "scope_base_commit": scope_base_commit or str(binding["base_commit"]),
        }

    def publish_input(
        self,
        name: str,
        binding: dict[str, object],
        planning_commit: str,
        *,
        allowed_paths: list[str],
        protected_paths: list[str] | None = None,
    ) -> dict[str, object]:
        return {
            "allowed_paths": allowed_paths,
            "binding": binding,
            "planning_commit": planning_commit,
            "protected_paths": ["spec.md"] if protected_paths is None else protected_paths,
        }

    def publish_with_failed_flow_advance(
        self,
        worktree: Path,
        publish_input: dict[str, object],
    ) -> PROTOCOL.ProtocolError:
        original_run_git = PROTOCOL._run_git
        failed_advance = False

        def fail_first_flow_advance(
            repository: Path,
            arguments: list[str],
            *,
            check: bool = True,
        ) -> subprocess.CompletedProcess[str]:
            nonlocal failed_advance
            if (
                repository == worktree
                and arguments[:2] == ["merge", "--ff-only"]
                and not failed_advance
            ):
                failed_advance = True
                return subprocess.CompletedProcess(
                    ["git", *arguments],
                    1,
                    "",
                    "forced Flow Worktree advance failure",
                )
            return original_run_git(repository, arguments, check=check)

        with mock.patch.object(
            PROTOCOL,
            "_run_git",
            side_effect=fail_first_flow_advance,
        ):
            with self.assertRaises(PROTOCOL.ProtocolError) as raised:
                PROTOCOL.publish_planning(publish_input)
        self.assertTrue(failed_advance)
        return raised.exception

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
        for command in PROTOCOL.COMMAND_REGISTRY.names:
            arguments = PROTOCOL._build_parser().parse_args([command])
            self.assertFalse(hasattr(arguments, "input"))

    def test_cli_start_reads_one_json_object_from_stdin(self) -> None:
        worktree = self.root / "stdin"
        request = {
            "branch": "codex/stdin",
            "repository": str(self.repository),
            "target_branch": "main",
            "worktree": str(worktree),
        }

        completed = self.run_cli(
            "start-worktree",
            json.dumps(request, sort_keys=True).encode("utf-8"),
        )

        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8"))
        response = json.loads(completed.stdout)
        self.assertTrue(response["ok"])
        self.assertEqual(response["binding"]["worktree"], str(worktree))
        self.assertTrue(worktree.is_dir())

    def test_every_cli_operation_reads_from_the_same_stdin_interface(self) -> None:
        for command in PROTOCOL.COMMAND_REGISTRY.names:
            with self.subTest(command=command):
                completed = self.run_cli(command, b"{")
                self.assertEqual(completed.returncode, 2)
                response = json.loads(completed.stdout)
                self.assertEqual(response["command"], command)
                self.assertEqual(response["error"]["code"], "invalid_input")

    def test_cli_rejects_invalid_json_forms_before_git_side_effects(self) -> None:
        worktrees_before = self.git("worktree", "list", "--porcelain")
        branches_before = self.git("branch", "--format=%(refname)")
        invalid_inputs = {
            "empty": b"",
            "malformed": b"{",
            "invalid-utf8": b"\xff",
            "duplicate-key": b'{"branch":"one","branch":"two"}',
            "floating-point": b'{"value":1.5}',
            "non-finite": b'{"value":NaN}',
            "not-object": b"[]",
        }

        for label, input_data in invalid_inputs.items():
            with self.subTest(label=label):
                completed = self.run_cli("start-worktree", input_data)
                self.assertEqual(completed.returncode, 2)
                response = json.loads(completed.stdout)
                self.assertEqual(response["error"]["code"], "invalid_input")

        self.assertEqual(self.git("worktree", "list", "--porcelain"), worktrees_before)
        self.assertEqual(self.git("branch", "--format=%(refname)"), branches_before)

    def test_cli_rejects_oversized_stdin_before_git_side_effects(self) -> None:
        completed = self.run_cli(
            "start-worktree",
            b"{" + b" " * PROTOCOL.MAX_INPUT_BYTES + b"}",
        )

        self.assertEqual(completed.returncode, 2)
        response = json.loads(completed.stdout)
        self.assertEqual(response["error"]["code"], "invalid_input")
        self.assertEqual(self.git("branch", "--list", "codex/oversized-stdin"), "")

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
        verify_input = {"binding": binding, "platform_cwd": binding["worktree"]}

        verified = PROTOCOL.verify_worktree(verify_input)

        self.assertTrue(verified["verified"])
        wrong_input = {"binding": binding, "platform_cwd": str(self.repository)}
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
        (self.repository / ".gitignore").write_text("local.cache\n", encoding="utf-8")
        self.git("add", ".gitignore")
        self.git("commit", "-q", "-m", "ignore local cache")
        binding = self.start("planning-dirty")
        worktree = Path(str(binding["worktree"]))
        planning_commit = self.commit(
            worktree,
            "plan.md",
            "accepted plan\n",
            "add plan",
        )
        (self.repository / "b.txt").write_text("user change\n", encoding="utf-8")
        (self.repository / "local.cache").write_text("local bytes\n", encoding="utf-8")

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
        self.assertEqual((self.repository / "local.cache").read_text(), "local bytes\n")
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

    def test_publish_planning_rejects_ignored_checkout_collision_without_touching_it(self) -> None:
        (self.repository / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
        self.git("add", ".gitignore")
        self.git("commit", "-q", "-m", "ignore local file")
        binding = self.start("planning-ignored")
        worktree = Path(str(binding["worktree"]))
        (worktree / "ignored.txt").write_text("accepted plan\n", encoding="utf-8")
        self.git("add", "-f", "ignored.txt", cwd=worktree)
        self.git("commit", "-q", "-m", "add ignored plan", cwd=worktree)
        planning_commit = self.git("rev-parse", "HEAD", cwd=worktree)
        target_head = self.git("rev-parse", "main")
        (self.repository / "ignored.txt").write_text("user secret\n", encoding="utf-8")

        with self.assertRaises(PROTOCOL.ProtocolError) as raised:
            PROTOCOL.publish_planning(
                self.publish_input(
                    "planning-ignored",
                    binding,
                    planning_commit,
                    allowed_paths=["ignored.txt"],
                )
            )

        self.assertEqual(raised.exception.code, "checkout_not_clean")
        self.assertEqual(raised.exception.context["paths"], ["ignored.txt"])
        self.assertEqual(self.git("rev-parse", "main"), target_head)
        self.assertEqual(
            (self.repository / "ignored.txt").read_text(encoding="utf-8"),
            "user secret\n",
        )
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=worktree), planning_commit)
        self.assertTrue(worktree.is_dir())

    def test_publish_planning_rejects_ignored_flow_collision_without_touching_it(self) -> None:
        (self.repository / ".gitignore").write_text("flow-secret.txt\n", encoding="utf-8")
        self.git("add", ".gitignore")
        self.git("commit", "-q", "-m", "ignore flow secret")
        binding = self.start("planning-flow-ignored")
        worktree = Path(str(binding["worktree"]))
        planning_commit = self.commit(
            worktree,
            "plan.md",
            "accepted plan\n",
            "add plan",
        )
        (worktree / "flow-secret.txt").write_text("flow secret\n", encoding="utf-8")
        (self.repository / "flow-secret.txt").write_text(
            "target bytes\n",
            encoding="utf-8",
        )
        self.git("add", "-f", "flow-secret.txt")
        self.git("commit", "-q", "-m", "target adds ignored path")
        target_head = self.git("rev-parse", "main")
        publish_input = self.publish_input(
            "planning-flow-ignored",
            binding,
            planning_commit,
            allowed_paths=["plan.md"],
        )

        with self.assertRaises(PROTOCOL.ProtocolError) as raised:
            PROTOCOL.publish_planning(publish_input)

        self.assertEqual(raised.exception.code, "worktree_not_clean")
        self.assertEqual(raised.exception.context["paths"], ["flow-secret.txt"])
        self.assertEqual(self.git("rev-parse", "main"), target_head)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=worktree), planning_commit)
        self.assertEqual(
            (worktree / "flow-secret.txt").read_text(encoding="utf-8"),
            "flow secret\n",
        )

        (worktree / "flow-secret.txt").unlink()
        result = PROTOCOL.publish_planning(publish_input)

        self.assertEqual(result["state"], "planning_published")
        self.assertEqual(
            (worktree / "flow-secret.txt").read_text(encoding="utf-8"),
            "target bytes\n",
        )

    def test_publish_planning_preserves_unrelated_ignored_flow_file(self) -> None:
        (self.repository / ".gitignore").write_text("local.cache\n", encoding="utf-8")
        self.git("add", ".gitignore")
        self.git("commit", "-q", "-m", "ignore local cache")
        binding = self.start("planning-flow-unrelated-ignored")
        worktree = Path(str(binding["worktree"]))
        planning_commit = self.commit(
            worktree,
            "plan.md",
            "accepted plan\n",
            "add plan",
        )
        (worktree / "local.cache").write_text("local bytes\n", encoding="utf-8")
        (self.repository / "target.txt").write_text("target change\n", encoding="utf-8")
        self.git("add", "target.txt")
        self.git("commit", "-q", "-m", "advance target")

        result = PROTOCOL.publish_planning(
            self.publish_input(
                "planning-flow-unrelated-ignored",
                binding,
                planning_commit,
                allowed_paths=["plan.md"],
            )
        )

        self.assertEqual(result["state"], "planning_published")
        self.assertEqual(
            (worktree / "local.cache").read_text(encoding="utf-8"),
            "local bytes\n",
        )

    def test_failed_planning_publication_restores_the_exact_retry_commit(self) -> None:
        binding = self.start("planning-retry")
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
        target_head = self.git("rev-parse", "main")
        (self.repository / "b.txt").write_text("staged user change\n", encoding="utf-8")
        self.git("add", "b.txt")
        publish_input = self.publish_input(
            "planning-retry",
            binding,
            planning_commit,
            allowed_paths=["plan.md"],
        )

        with self.assertRaises(PROTOCOL.ProtocolError) as raised:
            PROTOCOL.publish_planning(publish_input)

        self.assertEqual(raised.exception.code, "checkout_not_clean")
        self.assertEqual(self.git("rev-parse", "main"), target_head)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=worktree), planning_commit)
        self.assertEqual(self.git("status", "--short", cwd=worktree), "")

        self.git("restore", "--staged", "b.txt")
        result = PROTOCOL.publish_planning(publish_input)

        self.assertEqual(result["state"], "planning_published")
        self.assertEqual((self.repository / "b.txt").read_text(), "staged user change\n")
        self.assertEqual(self.git("status", "--short"), "M b.txt")

    def test_target_race_during_planning_publication_retries_once(self) -> None:
        binding = self.start("planning-race")
        worktree = Path(str(binding["worktree"]))
        planning_commit = self.commit(
            worktree,
            "plan.md",
            "accepted plan\n",
            "add plan",
        )
        (self.repository / "first.txt").write_text("first\n", encoding="utf-8")
        self.git("add", "first.txt")
        self.git("commit", "-q", "-m", "first target advance")
        original_merge = PROTOCOL._merge_candidate_into_target
        merge_attempts = 0

        def advance_target_once(
            candidate_binding: dict[str, object],
            candidate: str,
            expected_target_head: str,
        ) -> str:
            nonlocal merge_attempts
            merge_attempts += 1
            if merge_attempts == 1:
                (self.repository / "second.txt").write_text("second\n", encoding="utf-8")
                self.git("add", "second.txt")
                self.git("commit", "-q", "-m", "second target advance")
            return original_merge(candidate_binding, candidate, expected_target_head)

        with mock.patch.object(
            PROTOCOL,
            "_merge_candidate_into_target",
            side_effect=advance_target_once,
        ):
            result = PROTOCOL.publish_planning(
                self.publish_input(
                    "planning-race",
                    binding,
                    planning_commit,
                    allowed_paths=["plan.md"],
                )
            )

        self.assertEqual(result["state"], "planning_published")
        self.assertEqual(merge_attempts, 2)
        self.assertEqual((self.repository / "first.txt").read_text(), "first\n")
        self.assertEqual((self.repository / "second.txt").read_text(), "second\n")
        self.assertEqual((self.repository / "plan.md").read_text(), "accepted plan\n")
        self.assertEqual(self.git("status", "--short", cwd=worktree), "")

    def test_target_race_preserves_new_flow_worktree_changes(self) -> None:
        binding = self.start("planning-race-dirty-flow")
        worktree = Path(str(binding["worktree"]))
        planning_commit = self.commit(
            worktree,
            "plan.md",
            "accepted plan\n",
            "add plan",
        )
        target_before_race = self.git("rev-parse", "main")
        original_run_git = PROTOCOL._run_git
        raced = False

        def advance_target_after_validation(
            repository: Path,
            arguments: list[str],
            *,
            check: bool = True,
        ) -> subprocess.CompletedProcess[str]:
            nonlocal raced
            if (
                repository == self.repository
                and arguments[:3] == ["symbolic-ref", "--quiet", "--short"]
                and not raced
            ):
                raced = True
                (worktree / "plan.md").write_text(
                    "concurrent user edit\n",
                    encoding="utf-8",
                )
                (self.repository / "target.txt").write_text(
                    "target change\n",
                    encoding="utf-8",
                )
                self.git("add", "target.txt")
                self.git("commit", "-q", "-m", "advance target during publication")
            return original_run_git(repository, arguments, check=check)

        with mock.patch.object(
            PROTOCOL,
            "_run_git",
            side_effect=advance_target_after_validation,
        ):
            with self.assertRaises(PROTOCOL.ProtocolError) as raised:
                PROTOCOL.publish_planning(
                    self.publish_input(
                        "planning-race-dirty-flow",
                        binding,
                        planning_commit,
                        allowed_paths=["plan.md"],
                    )
                )

        self.assertTrue(raced)
        self.assertEqual(raised.exception.code, "integration_restore_failed")
        self.assertNotEqual(self.git("rev-parse", "main"), target_before_race)
        self.assertEqual(
            (worktree / "plan.md").read_text(encoding="utf-8"),
            "concurrent user edit\n",
        )
        self.assertEqual(self.git("status", "--short", cwd=worktree), "M plan.md")

    def test_retry_after_published_planning_never_rolls_back_or_republishes(self) -> None:
        binding = self.start("planning-published-retry")
        worktree = Path(str(binding["worktree"]))
        planning_commit = self.commit(
            worktree,
            "plan.md",
            "accepted plan\n",
            "add plan",
        )
        publish_input = self.publish_input(
            "planning-published-retry",
            binding,
            planning_commit,
            allowed_paths=["plan.md"],
        )
        first_error = self.publish_with_failed_flow_advance(worktree, publish_input)

        self.assertEqual(first_error.code, "flow_advance_failed")
        published_merge = str(first_error.context["merge_commit"])
        self.assertEqual(self.git("rev-parse", "main"), published_merge)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=worktree), planning_commit)

        with self.assertRaises(PROTOCOL.ProtocolError) as retry_raised:
            PROTOCOL.publish_planning(publish_input)

        self.assertEqual(retry_raised.exception.code, "integration_unverified")
        self.assertEqual(
            retry_raised.exception.context["published_target_head"],
            published_merge,
        )
        self.assertEqual(self.git("rev-parse", "main"), published_merge)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=worktree), planning_commit)
        self.assertEqual(self.git("status", "--short", cwd=worktree), "")

    def test_retry_after_published_refreshed_planning_preserves_the_candidate(self) -> None:
        binding = self.start("planning-published-refreshed-retry")
        worktree = Path(str(binding["worktree"]))
        planning_commit = self.commit(
            worktree,
            "plan.md",
            "accepted plan\n",
            "add plan",
        )
        (self.repository / "target.txt").write_text("target change\n", encoding="utf-8")
        self.git("add", "target.txt")
        self.git("commit", "-q", "-m", "advance target before publication")
        publish_input = self.publish_input(
            "planning-published-refreshed-retry",
            binding,
            planning_commit,
            allowed_paths=["plan.md"],
        )
        first_error = self.publish_with_failed_flow_advance(worktree, publish_input)

        self.assertEqual(first_error.code, "flow_advance_failed")
        published_merge = str(first_error.context["merge_commit"])
        refreshed_candidate = self.git("rev-parse", "HEAD", cwd=worktree)
        self.assertNotEqual(refreshed_candidate, planning_commit)

        with self.assertRaises(PROTOCOL.ProtocolError) as retry_raised:
            PROTOCOL.publish_planning(publish_input)

        self.assertEqual(retry_raised.exception.code, "integration_unverified")
        self.assertEqual(
            retry_raised.exception.context["published_target_head"],
            published_merge,
        )
        self.assertEqual(self.git("rev-parse", "main"), published_merge)
        self.assertEqual(
            self.git("rev-parse", "HEAD", cwd=worktree),
            refreshed_candidate,
        )
        self.assertEqual(self.git("status", "--short", cwd=worktree), "")

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
