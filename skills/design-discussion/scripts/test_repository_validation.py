#!/usr/bin/env python3

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parent))
from matrix_proof import matrix_proof


REPOSITORY = Path(__file__).parents[3]
VALIDATOR = REPOSITORY / "scripts" / "validate_repository.py"
DEPENDENCY_CHECK = REPOSITORY / "scripts" / "check-dependencies.sh"
sys.path.insert(0, str(REPOSITORY / "scripts"))
import validate_repository as REPOSITORY_VALIDATION


class RepositoryValidationTests(unittest.TestCase):
    def test_child_topic_reference_reaches_the_real_codex_task_seam_in_order(self) -> None:
        reference = (
            REPOSITORY
            / "skills/design-discussion/references/child-topic-protocol.md"
        ).read_text(encoding="utf-8")
        ordered = [
            "publish the latest verified `CP-*`",
            "`prepare-handoff` exactly once",
            "Only an exact `确认`",
            "call `create_thread` once",
            "call `bind-handoff`",
            "call `accept-handoff`",
            "`authorize-handoff-discussion`",
            "`submit-child-result`",
            "`record-child-result`",
        ]
        cursor = 0
        positions = []
        for marker in ordered:
            cursor = reference.index(marker, cursor)
            positions.append(cursor)
        for marker in (
            "send_message_to_thread", "wait_threads", "handoff_next_turn_required",
            "record-handoff-outcome-unknown", "reconcile-handoff-attempt",
            "record-handoff-failure", "retry-handoff", "handoff_late_arrival",
            "continuation_of", "marks the old binding superseded",
        ):
            self.assertIn(marker, reference)
        self.assertNotIn("When the later protocol is available", reference)
        self.assertNotIn("Until those operations exist", reference)

    def run_validator(self, repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VALIDATOR), "--repository", str(repository), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def make_repository(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary_directory = tempfile.TemporaryDirectory()
        repository = Path(temporary_directory.name)
        (repository / "skills" / "alpha" / "references").mkdir(parents=True)
        (repository / "skills" / "alpha" / "scripts").mkdir()
        (repository / "docs").mkdir()
        (repository / "skills" / "alpha" / "SKILL.md").write_text(
            "---\nname: alpha\ndescription: test\n---\n"
            "Use `$external-one`. Read [details](references/details.md).\n",
            encoding="utf-8",
        )
        (repository / "skills" / "alpha" / "references" / "details.md").write_text(
            "# Details\n", encoding="utf-8"
        )
        (repository / "README.md").write_text("`alpha` and `external-one`\n", encoding="utf-8")
        (repository / "docs" / "dependencies.md").write_text(
            "| `alpha` | internal |\n| `external-one` | runtime |\n",
            encoding="utf-8",
        )
        return temporary_directory, repository

    def test_current_repository_has_registered_dependencies_and_complete_references(self) -> None:
        completed = self.run_validator(REPOSITORY)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["state"], "valid")
        self.assertGreaterEqual(report["skill_count"], 5)
        self.assertGreaterEqual(report["reference_count"], 10)
        self.assertGreaterEqual(report["test_count"], 3)

    def test_dependency_reference_and_user_path_failures_are_independently_reported(self) -> None:
        temporary_directory, repository = self.make_repository()
        self.addCleanup(temporary_directory.cleanup)
        skill = repository / "skills" / "alpha" / "SKILL.md"
        skill.write_text(
            skill.read_text(encoding="utf-8")
            + "Use `$missing-runtime`. Read [missing](references/missing.md).\n",
            encoding="utf-8",
        )
        (repository / "skills" / "alpha" / "scripts" / "tool.py").write_text(
            'OUTPUT = "' + "/" + "Users/alice/private/output.md" + '"\n',
            encoding="utf-8",
        )
        completed = self.run_validator(repository)
        self.assertEqual(completed.returncode, 1)
        report = json.loads(completed.stdout)
        self.assertEqual(
            {issue["code"] for issue in report["issues"]},
            {
                "unregistered-dependency",
                "missing-progressive-reference",
                "user-specific-absolute-path",
            },
        )

    def test_test_discovery_is_dynamic_and_sorted(self) -> None:
        temporary_directory, repository = self.make_repository()
        self.addCleanup(temporary_directory.cleanup)
        first = repository / "skills" / "alpha" / "scripts" / "test_zeta.py"
        second = repository / "skills" / "alpha" / "scripts" / "test_alpha.py"
        ignored = repository / "skills" / "alpha" / "scripts" / "alpha_test.py"
        for path in (first, second, ignored):
            path.write_text("# fixture\n", encoding="utf-8")
        completed = self.run_validator(repository, "--list-tests")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            completed.stdout.splitlines(),
            [str(second.resolve()), str(first.resolve())],
        )

    def test_non_utf8_skill_files_are_scanned_without_being_silently_skipped(self) -> None:
        temporary_directory, repository = self.make_repository()
        self.addCleanup(temporary_directory.cleanup)
        binary = repository / "skills" / "alpha" / "scripts" / "fixture.bin"
        binary.write_bytes(
            b"\xffprefix " + b"/" + b"Users/private-user/secret suffix\x80"
        )

        completed = self.run_validator(repository)

        self.assertEqual(completed.returncode, 1)
        issues = json.loads(completed.stdout)["issues"]
        self.assertEqual(
            [(issue["code"], issue["path"]) for issue in issues],
            [("user-specific-absolute-path", "skills/alpha/scripts/fixture.bin")],
        )

    def test_user_specific_paths_cover_macos_linux_and_windows(self) -> None:
        for label, value in (
            ("macos", "/" + "Users/alice/private/output.md"),
            ("linux", "/" + "home/alice/private/output.md"),
            ("windows-slash", "C:/" + "Users/Alice/private/output.md"),
            ("windows-backslash", "D:\\" + "Users\\Alice\\private\\output.md"),
        ):
            with self.subTest(label=label):
                temporary_directory, repository = self.make_repository()
                self.addCleanup(temporary_directory.cleanup)
                fixture = repository / "skills" / "alpha" / "scripts" / f"{label}.bin"
                fixture.write_bytes(value.encode("utf-8"))
                completed = self.run_validator(repository)
                self.assertEqual(completed.returncode, 1)
                self.assertIn(
                    ("user-specific-absolute-path", fixture.relative_to(repository).as_posix()),
                    [(issue["code"], issue["path"]) for issue in json.loads(completed.stdout)["issues"]],
                )

    def test_documentation_classifier_matches_protocol_scope_without_absorbing_code(self) -> None:
        documentation = {
            "CONTRIBUTING.md", "CHANGELOG.md", "SECURITY.md", "architecture.md",
            "design/spec.md", ".github/ISSUE_TEMPLATE/bug.md", "Specs/authentication.yaml",
            "Tickets/42-login.md", "ADRs/0001-cache.md", "requirements/payment-draft.rst",
        }
        implementation = {
            "src/main.py", "tests/test_main.py", "migrations/0001.sql",
            "schemas/api.json", "fixtures/request.json", "pyproject.toml",
            "package-lock.json", "Dockerfile", ".github/workflows/ci.yml",
            "fixtures/golden.md", "tests/snapshots/result.mdx", "migrations/notes.rst",
        }
        self.assertEqual(
            {path for path in documentation if not REPOSITORY_VALIDATION.is_documentation_path(path)},
            set(),
        )
        self.assertEqual(
            {path for path in implementation if REPOSITORY_VALIDATION.is_documentation_path(path)},
            set(),
        )

    def test_completed_ready_tracker_requires_orthogonal_lifecycle_closure(self) -> None:
        temporary_directory, repository = self.make_repository()
        self.addCleanup(temporary_directory.cleanup)
        tracker = repository / ".scratch" / "design" / "issues" / "01-done.md"
        tracker.parent.mkdir(parents=True)
        tracker.write_text(
            "# Done\n\nStatus: `ready-for-agent`\n\n## Acceptance criteria\n\n- [x] Verified.\n",
            encoding="utf-8",
        )
        missing = json.loads(self.run_validator(repository).stdout)
        self.assertIn("completed-tracker-missing-closure", {item["code"] for item in missing["issues"]})

        tracker.write_text(
            tracker.read_text(encoding="utf-8").replace(
                "Status: `ready-for-agent`", "Status: `ready-for-agent`\nLifecycle: `completed`"
            ),
            encoding="utf-8",
        )
        completed = self.run_validator(repository)
        self.assertEqual(completed.returncode, 0, completed.stdout)

    def test_dependency_check_rejects_duplicate_filesystem_skill_sources(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        root = Path(temporary_directory.name)
        codex_home = root / "codex"
        agents_home = root / "home" / ".agents" / "skills"
        required_external = {
            "setup-matt-pocock-skills", "ask-matt", "grill-with-docs", "grilling",
            "domain-modeling", "to-spec", "to-tickets", "implement", "tdd", "code-review",
        }
        for name in required_external:
            path = agents_home / name / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
        duplicate = codex_home / "skills" / "ask-matt" / "SKILL.md"
        duplicate.parent.mkdir(parents=True)
        duplicate.write_text("---\nname: ask-matt\n---\n", encoding="utf-8")
        completed = subprocess.run(
            [str(DEPENDENCY_CHECK)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "HOME": str(root / "home"), "CODEX_HOME": str(codex_home)},
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("duplicate", completed.stdout)
        self.assertIn(str(duplicate), completed.stdout)

    @matrix_proof(
        "fault_boundaries:documentation-commit",
        "invariants:no-documentation-in-implementation-commits",
    )
    def test_implementation_range_rejects_documentation_paths(self) -> None:
        temporary_directory, repository = self.make_repository()
        self.addCleanup(temporary_directory.cleanup)
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
        subprocess.run(
            [
                "git", "-C", str(repository), "-c", "user.name=Test",
                "-c", "user.email=test@example.com", "commit", "-qm", "base",
            ],
            check=True,
        )
        base = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True, stdout=subprocess.PIPE, text=True,
        ).stdout.strip()
        implementation_paths = {
            "src/feature.py": "enabled = True\n",
            "tests/test_feature.py": "def test_feature(): pass\n",
            "migrations/0001.sql": "select 1;\n",
            "schemas/api.json": "{}\n",
            "fixtures/input.json": "{}\n",
            "pyproject.toml": "[tool.test]\n",
            "package-lock.json": "{}\n",
        }
        for relative_path, content in implementation_paths.items():
            path = repository / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", *implementation_paths], check=True)
        subprocess.run(
            [
                "git", "-C", str(repository), "-c", "user.name=Test",
                "-c", "user.email=test@example.com", "commit", "-qm", "implementation",
            ],
            check=True,
        )
        implementation = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True, stdout=subprocess.PIPE, text=True,
        ).stdout.strip()
        clean = self.run_validator(
            repository, "--implementation-range", f"{base}..{implementation}"
        )
        self.assertEqual(clean.returncode, 0, clean.stderr)
        self.assertEqual(json.loads(clean.stdout)["documentation_paths"], [])

        documentation_paths = {
            "CONTRIBUTING.md", "CHANGELOG.md", "SECURITY.md", "architecture.md",
            "design/spec.md", ".github/ISSUE_TEMPLATE/bug.md", "Specs/api.yaml",
            "Tickets/42.md", "ADRs/0002.md", "requirements/draft.rst",
        }
        for relative_path in documentation_paths:
            path = repository / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("documentation\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", *documentation_paths], check=True)
        subprocess.run(
            [
                "git", "-C", str(repository), "-c", "user.name=Test",
                "-c", "user.email=test@example.com", "commit", "-qm", "documentation leak",
            ],
            check=True,
        )
        leaked = self.run_validator(
            repository, "--implementation-range", f"{base}..HEAD"
        )
        self.assertEqual(leaked.returncode, 1)
        self.assertEqual(
            json.loads(leaked.stdout)["documentation_paths"], sorted(documentation_paths)
        )


if __name__ == "__main__":
    unittest.main()
