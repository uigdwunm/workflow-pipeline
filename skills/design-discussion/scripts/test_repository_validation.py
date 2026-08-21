#!/usr/bin/env python3

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).parents[3]
VALIDATOR = REPOSITORY / "scripts" / "validate_repository.py"


class RepositoryValidationTests(unittest.TestCase):
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
        source = repository / "src" / "feature.py"
        source.parent.mkdir()
        source.write_text("enabled = True\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", "src/feature.py"], check=True)
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

        (repository / "README.md").write_text("changed documentation\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
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
            json.loads(leaked.stdout)["documentation_paths"], ["README.md"]
        )


if __name__ == "__main__":
    unittest.main()
