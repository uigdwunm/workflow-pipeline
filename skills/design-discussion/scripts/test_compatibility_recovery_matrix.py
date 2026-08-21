#!/usr/bin/env python3

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).parents[3]
MATRIX = Path(__file__).with_name("compatibility_recovery_matrix.json")
DISCUSSION = Path(__file__).with_name("discussion_protocol.py")
LEGACY_DISCOVERY_OUTPUTS = {
    "none": b'{"candidate_count":0,"context":"none","created":false,"ok":true,"state":"none"}\n',
    "ambiguous": b'{"candidate_count":2,"context":"ambiguous","created":false,"ok":true,"state":"ambiguous"}\n',
}


class CompatibilityRecoveryMatrixTests(unittest.TestCase):
    def available_tests(self, relative_path: str) -> set[str]:
        source = REPOSITORY / relative_path
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        return {
            f"{node.name}.{child.name}"
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            and child.name.startswith("test_")
        }

    def snapshot(self, root: Path) -> dict[str, dict[str, object]]:
        result: dict[str, dict[str, object]] = {}
        for path in sorted(root.rglob("*")):
            metadata = path.lstat()
            entry: dict[str, object] = {
                "mode": stat.S_IMODE(metadata.st_mode),
                "mtime_ns": metadata.st_mtime_ns,
                "size": metadata.st_size,
                "type": "directory" if path.is_dir() else "file",
            }
            if path.is_file():
                entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            result[path.relative_to(root).as_posix()] = entry
        return result

    def run_discovery(self, project: Path) -> subprocess.CompletedProcess[bytes]:
        request = json.dumps(
            {
                "operation": "discover-context",
                "project_path": str(project),
                "protocol_version": 1,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return subprocess.run(
            [sys.executable, str(DISCUSSION)],
            input=request,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_matrix_is_complete_and_every_cell_names_an_executable_test(self) -> None:
        matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
        self.assertEqual(matrix["schema"], "compatibility-recovery-matrix-v1")
        required = {
            "scenarios": {
                "root-discussion-to-1", "root-discussion-to-2", "root-discussion-to-3",
                "root-discussion-to-4", "direct-0-to-2", "direct-1-to-3",
                "parent-child-absorption-and-coverage", "no-code-integration",
                "two-safe-isolated-implementations", "shared-base-serial-integration",
            },
            "fault_boundaries": {
                "ledger-transaction", "lease", "git-worktree", "external-carrier-creation",
                "ready-activate", "terminal-claim", "documentation-commit", "cleanup",
            },
            "invariants": {
                "no-automatic-unconfirmed-worktree", "no-double-active-run-or-binding",
                "no-stale-source-integration", "no-documentation-in-implementation-commits",
                "no-force-cleanup-of-unknown-work", "no-premature-topic-close",
            },
            "compatibility": {
                "none-byte-equivalent-zero-state", "ambiguous-byte-equivalent-zero-state",
            },
            "legacy": {"handoff-v2", "handoff-v1", "older-worktree-checkpoint"},
        }
        known_by_file: dict[str, set[str]] = {}
        for section, required_ids in required.items():
            rows = matrix[section]
            self.assertEqual({row["id"] for row in rows}, required_ids)
            for row in rows:
                self.assertTrue(row["tests"], row["id"])
                if section == "fault_boundaries":
                    self.assertEqual(
                        set(row),
                        {"id", "failure", "recovery", "invariants", "tests"},
                        row["id"],
                    )
                    self.assertTrue(row["failure"], row["id"])
                    self.assertTrue(row["recovery"], row["id"])
                    self.assertTrue(row["invariants"], row["id"])
                for reference in row["tests"]:
                    relative_path, test_name = reference.split("::", 1)
                    known = known_by_file.setdefault(
                        relative_path, self.available_tests(relative_path)
                    )
                    self.assertIn(test_name, known, reference)

    def test_none_and_ambiguous_are_byte_stable_and_create_zero_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            empty_snapshot = self.snapshot(project)
            none_first = self.run_discovery(project)
            none_second = self.run_discovery(project)
            self.assertEqual(none_first.returncode, 0, none_first.stderr)
            self.assertEqual(
                (none_first.returncode, none_first.stdout, none_first.stderr),
                (none_second.returncode, none_second.stdout, none_second.stderr),
            )
            self.assertEqual(json.loads(none_first.stdout)["context"], "none")
            self.assertEqual(none_first.stdout, LEGACY_DISCOVERY_OUTPUTS["none"])
            self.assertEqual(self.snapshot(project), empty_snapshot)

            for number in (1, 2):
                topic = project / "docs" / "discussions" / f"candidate-{number}" / "topic.md"
                topic.parent.mkdir(parents=True, exist_ok=True)
                topic.write_text(
                    "---\n"
                    f"project_id: project-{'1' * 32}\n"
                    f"tree_id: tree-{'2' * 32}\n"
                    f"topic_id: topic-{str(number) * 32}\n"
                    "---\n",
                    encoding="utf-8",
                )
            ambiguous_snapshot = self.snapshot(project)
            ambiguous_first = self.run_discovery(project)
            ambiguous_second = self.run_discovery(project)
            self.assertEqual(ambiguous_first.returncode, 0, ambiguous_first.stderr)
            self.assertEqual(
                (ambiguous_first.returncode, ambiguous_first.stdout, ambiguous_first.stderr),
                (ambiguous_second.returncode, ambiguous_second.stdout, ambiguous_second.stderr),
            )
            self.assertEqual(json.loads(ambiguous_first.stdout)["context"], "ambiguous")
            self.assertEqual(
                ambiguous_first.stdout,
                LEGACY_DISCOVERY_OUTPUTS["ambiguous"],
            )
            self.assertEqual(self.snapshot(project), ambiguous_snapshot)
            self.assertFalse((project / ".codex").exists())


if __name__ == "__main__":
    unittest.main()
