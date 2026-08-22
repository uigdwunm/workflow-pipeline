#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys


DEPENDENCY_PATTERN = re.compile(r"\$([a-z][a-z0-9-]*)")
REGISTERED_NAME_PATTERN = re.compile(r"`([a-z][a-z0-9-]*)`")
MARKDOWN_REFERENCE_PATTERN = re.compile(
    r"(?:\]\(|`)(?P<path>(?:\.\./)?(?:[a-z0-9-]+/)*references/[a-z0-9-]+\.md)(?:\)|`)"
)
SKILL_NAME_PATTERN = re.compile(r"^name:\s*([a-z][a-z0-9-]*)\s*$", re.MULTILINE)
USER_PATH_BYTES_PATTERN = re.compile(
    rb"(?:(?:/Users|/home)/[^/\s]+/|[A-Za-z]:[\\/]Users[\\/][^\\/\s]+[\\/])",
    re.IGNORECASE,
)
DOCUMENTATION_EXTENSIONS = {".adoc", ".asciidoc", ".md", ".mdx", ".org", ".rst"}
DOCUMENTATION_DIRECTORIES = {
    ".scratch",
    "adr",
    "adrs",
    "architecture",
    "design",
    "design-docs",
    "doc",
    "docs",
    "documentation",
    "requirement-drafts",
    "requirements",
    "spec",
    "specs",
    "ticket",
    "tickets",
}
DOCUMENTATION_NAMES = {
    "agents.md",
    "authors",
    "changelog",
    "changes",
    "code_of_conduct.md",
    "context.md",
    "contributing.md",
    "governance.md",
    "history",
    "maintainers",
    "security.md",
    "support.md",
    "third_party_notices.md",
}
IMPLEMENTATION_DIRECTORIES = {
    "app",
    "build",
    "config",
    "configs",
    "fixture",
    "fixtures",
    "lib",
    "migration",
    "migrations",
    "schema",
    "schemas",
    "source",
    "src",
    "test",
    "tests",
}


def relative(path: Path, repository: Path) -> str:
    return path.relative_to(repository).as_posix()


def discover_tests(repository: Path) -> list[Path]:
    return sorted(
        path.resolve()
        for path in (repository / "skills").glob("*/scripts/test_*.py")
        if path.is_file()
    )


def is_documentation_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    parts = tuple(part.casefold() for part in normalized.split("/") if part)
    if not parts:
        return False
    name = parts[-1]
    if ".github" in parts and any(
        part in {"issue_template", "pull_request_template"} for part in parts
    ):
        return True
    if any(part in DOCUMENTATION_DIRECTORIES for part in parts[:-1]):
        return True
    if any(part in IMPLEMENTATION_DIRECTORIES for part in parts[:-1]):
        return False
    if name in DOCUMENTATION_NAMES or name.startswith("readme"):
        return True
    if Path(name).suffix.casefold() in DOCUMENTATION_EXTENSIONS:
        return True
    return False


def validate_implementation_range(repository: Path, revision_range: str) -> dict[str, object]:
    completed = subprocess.run(
        ["git", "-C", str(repository), "diff", "--name-only", "-z", revision_range],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        return {
            "state": "invalid",
            "revision_range": revision_range,
            "changed_paths": [],
            "documentation_paths": [],
            "issues": [
                {
                    "code": "invalid-implementation-range",
                    "message": completed.stderr.decode("utf-8", errors="replace").strip(),
                }
            ],
        }
    changed_paths = sorted(
        item.decode("utf-8", errors="strict")
        for item in completed.stdout.split(b"\0")
        if item
    )
    documentation_paths = [path for path in changed_paths if is_documentation_path(path)]
    return {
        "state": "valid" if not documentation_paths else "invalid",
        "revision_range": revision_range,
        "changed_paths": changed_paths,
        "documentation_paths": documentation_paths,
        "issues": [
            {"code": "documentation-in-implementation-range", "path": path}
            for path in documentation_paths
        ],
    }


def referenced_markdown(source: Path) -> list[Path]:
    text = source.read_text(encoding="utf-8")
    targets: list[Path] = []
    for match in MARKDOWN_REFERENCE_PATTERN.finditer(text):
        reference = Path(match.group("path"))
        base = source.parent.parent if source.parent.name == "references" and reference.parts[0] == "references" else source.parent
        targets.append((base / reference).resolve())
    return targets


def validate(repository: Path) -> dict[str, object]:
    skills_root = repository / "skills"
    skill_files = sorted(skills_root.glob("*/SKILL.md"))
    markdown_files = sorted(skills_root.rglob("*.md"))
    dependency_document = repository / "docs" / "dependencies.md"
    readme = repository / "README.md"
    issues: list[dict[str, str]] = []

    local_names: set[str] = set()
    for skill_file in skill_files:
        match = SKILL_NAME_PATTERN.search(skill_file.read_text(encoding="utf-8"))
        if match is None:
            issues.append({"code": "invalid-skill-registration", "path": relative(skill_file, repository)})
            continue
        local_names.add(match.group(1))

    registration_text = ""
    for document in (dependency_document, readme):
        if document.is_file():
            registration_text += document.read_text(encoding="utf-8") + "\n"
    registered_names = set(REGISTERED_NAME_PATTERN.findall(registration_text))
    invoked_names: set[str] = set()
    for markdown_file in markdown_files:
        text = markdown_file.read_text(encoding="utf-8")
        invoked_names.update(DEPENDENCY_PATTERN.findall(text))
    for skill_path in sorted(path for path in skills_root.rglob("*") if path.is_file()):
        if USER_PATH_BYTES_PATTERN.search(skill_path.read_bytes()):
            issues.append(
                {"code": "user-specific-absolute-path", "path": relative(skill_path, repository)}
            )
    tracker_paths = sorted(
        path
        for path in (repository / ".scratch").glob("*/**/*.md")
        if path.is_file() and (path.name == "PRD.md" or "issues" in path.parts)
    )
    for tracker_path in tracker_paths:
        tracker_text = tracker_path.read_text(encoding="utf-8")
        checkboxes = re.findall(r"^- \[([ xX])\] ", tracker_text, re.MULTILINE)
        ready = re.search(
            r"^Status:\s*`?ready-for-agent`?\s*$", tracker_text, re.MULTILINE
        )
        completed = re.search(
            r"^Lifecycle:\s*`?completed`?\s*$", tracker_text, re.MULTILINE
        )
        if ready and checkboxes and all(value.casefold() == "x" for value in checkboxes) and not completed:
            issues.append(
                {
                    "code": "completed-tracker-missing-closure",
                    "path": relative(tracker_path, repository),
                }
            )
    for dependency in sorted(invoked_names | local_names):
        if dependency not in registered_names:
            issues.append({"code": "unregistered-dependency", "dependency": dependency})

    reference_files = {path.resolve() for path in skills_root.glob("*/references/*.md")}
    reachable: set[Path] = set()
    pending = [path.resolve() for path in skill_files]
    visited: set[Path] = set()
    while pending:
        source = pending.pop()
        if source in visited or not source.is_file():
            continue
        visited.add(source)
        for target in referenced_markdown(source):
            if not target.is_file():
                issues.append(
                    {
                        "code": "missing-progressive-reference",
                        "path": relative(source, repository),
                        "target": str(target),
                    }
                )
                continue
            if target in reference_files and target not in reachable:
                reachable.add(target)
                pending.append(target)
    for orphan in sorted(reference_files - reachable):
        issues.append({"code": "unreachable-progressive-reference", "path": relative(orphan, repository)})

    issues.sort(key=lambda issue: json.dumps(issue, sort_keys=True))
    return {
        "state": "valid" if not issues else "invalid",
        "skill_count": len(skill_files),
        "reference_count": len(reference_files),
        "test_count": len(discover_tests(repository)),
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--list-tests", action="store_true")
    parser.add_argument("--implementation-range")
    arguments = parser.parse_args()
    repository = arguments.repository.resolve()
    if arguments.list_tests:
        for test_file in discover_tests(repository):
            print(test_file)
        return 0
    if arguments.implementation_range is not None:
        report = validate_implementation_range(repository, arguments.implementation_range)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 0 if report["state"] == "valid" else 1
    report = validate(repository)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if report["state"] == "valid" else 1


if __name__ == "__main__":
    sys.exit(main())
