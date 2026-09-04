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
REPOSITORY = Path(__file__).parents[3]
VALIDATOR = REPOSITORY / "scripts" / "validate_repository.py"
DEPENDENCY_CHECK = REPOSITORY / "scripts" / "check-dependencies.sh"
sys.path.insert(0, str(REPOSITORY / "scripts"))
import validate_repository as REPOSITORY_VALIDATION


class RepositoryValidationTests(unittest.TestCase):
    def test_stage_three_assigns_candidate_review_to_the_originating_task(self) -> None:
        guided = (
            REPOSITORY / "skills/guided-implementation/SKILL.md"
        ).read_text(encoding="utf-8")
        execution = (
            REPOSITORY
            / "skills/guided-implementation/references/execution-protocol.md"
        ).read_text(encoding="utf-8")
        originating = (
            REPOSITORY
            / "skills/guided-implementation/references/originating-task-protocol.md"
        ).read_text(encoding="utf-8")
        worktree_execution = (
            REPOSITORY
            / "skills/guided-implementation/references/worktree-execution.md"
        ).read_text(encoding="utf-8")
        normalized_guided = " ".join(guided.split())
        normalized_execution = " ".join(execution.split())
        normalized_originating = " ".join(originating.split())

        for marker in ("$implement", "$tdd"):
            self.assertIn(marker, guided)
            self.assertIn(marker, execution)
        self.assertIn("$code-review", guided)
        self.assertIn("$code-review", execution)
        self.assertIn("`Blocked by`", execution)
        self.assertIn("topologically", execution.casefold())
        for source in (normalized_guided, normalized_execution):
            self.assertIn(
                "Dedicated Implementation Task must not dispatch Standards or Spec review agents",
                source,
            )
            self.assertIn("candidate commits", source)
            self.assertIn("remediation", source)
            self.assertIn("originating-task-protocol.md", source)
            self.assertNotIn("pins the exact candidate commit", source)
            self.assertNotIn("reruns both axes against that replacement candidate", source)
        for marker in (
            "review contract is authoritative",
            "pins the exact candidate commit",
            "expected target-branch commit as one review fixed point",
            "passes that same fixed point and candidate to both Standards and Spec review axes",
            "dispatches the Standards and Spec review axes independently",
            "same Dedicated Implementation Task and verified worktree",
            "Any replacement candidate invalidates both review results",
            "reruns both axes against that replacement candidate",
            "Only the Originating Task accepts the candidate; Stage 4 integrates it",
            "merges that target, runs affected and full checks, and commits a replacement candidate",
        ):
            self.assertIn(marker, normalized_originating)
        normalized_worktree_execution = " ".join(worktree_execution.split())
        self.assertNotIn("reruns verification and review", normalized_worktree_execution)
        for marker in (
            "Dedicated Implementation Task merges the new target",
            "runs affected and full checks",
            "commits a replacement candidate",
            "Originating Task establishes the new review fixed point",
            "reruns both Standards and Spec axes",
        ):
            self.assertIn(marker, normalized_worktree_execution)

    def test_stage_three_intakes_execution_results_before_declaring_completion(self) -> None:
        guided = (
            REPOSITORY / "skills/guided-implementation/SKILL.md"
        ).read_text(encoding="utf-8")
        execution = (
            REPOSITORY
            / "skills/guided-implementation/references/execution-protocol.md"
        ).read_text(encoding="utf-8")
        originating = (
            REPOSITORY
            / "skills/guided-implementation/references/originating-task-protocol.md"
        ).read_text(encoding="utf-8")
        normalized_guided = " ".join(guided.split())
        normalized_execution = " ".join(execution.split())
        normalized_originating = " ".join(originating.split())

        self.assertIn(
            "A platform terminal result ends one execution turn; it does not complete Stage 3",
            normalized_originating,
        )
        for result_type in ("candidate", "checkpoint", "blocked", "no-progress"):
            self.assertIn(f"`{result_type}`", normalized_originating)
        for marker in (
            "exact HEAD before and after the turn",
            "ordinary continuation, not recovery",
            "one corrective continuation",
            "at most one active Dedicated Implementation Task",
            "same Flow Worktree and current verified clean HEAD",
        ):
            self.assertIn(marker, normalized_originating)
        for marker in (
            "结果类型：<candidate | checkpoint | blocked>",
            "当前 HEAD：<commit>",
            "已完成：<completed scope>",
            "剩余：<remaining scope | none>",
            "阻塞：<specific decision gap | none>",
        ):
            self.assertIn(marker, execution)
        self.assertIn(
            "at most one active dedicated implementation task",
            normalized_guided.casefold(),
        )
        self.assertNotIn("连续两次恢复", guided + execution + originating)

    def test_stage_three_uses_executable_thread_settings_verification(self) -> None:
        execution = (
            REPOSITORY
            / "skills/guided-implementation/references/execution-protocol.md"
        ).read_text(encoding="utf-8")

        self.assertIn("thread_settings.py verify", execution)
        self.assertIn("--current", execution)
        self.assertIn("--model gpt-5.6-terra", execution)
        self.assertIn("--reasoning-effort high", execution)

    def test_stage_three_preserves_flow_and_emits_complete_closure_handoff(self) -> None:
        guided = (
            REPOSITORY / "skills/guided-implementation/SKILL.md"
        ).read_text(encoding="utf-8")

        for marker in (
            "流程模式：<逐阶段确认 | 连续执行后续全部流程>",
            "目标仓库：<absolute repository path>",
            "目标分支：<target branch>",
            "验证：<focused and full checks>",
            "审查：<Standards and Spec review result>",
            "待归档文档：<exact paths and required updates | none>",
            "讨论上下文：<unattached | attached>",
            "讨论身份：<project path, project id, tree id and topic id | none>",
            "讨论绑定：<actor conversation ref | none>",
            "阶段 3 结果：<phase result id | none>",
            "讨论版本：<ledger revision and topic revision at current_phase 3 | none>",
            "连续执行后续全部流程",
        ):
            self.assertIn(marker, guided)

    def test_stages_two_through_four_share_one_flow_worktree(self) -> None:
        solution = (
            REPOSITORY / "skills/solution-design/SKILL.md"
        ).read_text(encoding="utf-8")
        solution_templates = (
            REPOSITORY / "skills/solution-design/references/templates.md"
        ).read_text(encoding="utf-8")
        guided = (
            REPOSITORY / "skills/guided-implementation/SKILL.md"
        ).read_text(encoding="utf-8")
        closure = (
            REPOSITORY / "skills/change-closure/SKILL.md"
        ).read_text(encoding="utf-8")

        self.assertIn("`start-worktree`", solution)
        self.assertIn("`publish-planning`", solution)
        self.assertIn("retains the Flow Worktree", solution)
        self.assertIn("方案合并提交：<planning merge commit>", solution_templates)
        self.assertIn(
            "Flow Worktree：<exact retained binding at planning merge commit>",
            solution_templates,
        )
        self.assertIn("An inherited entry must reuse its Flow Worktree", guided)
        self.assertIn("For a qualified standalone entry, call", guided)
        self.assertIn("`start-worktree` once", guided)
        self.assertIn("never create a replacement", guided)
        self.assertNotIn("calls `complete-worktree`", guided)
        self.assertIn("passes the retained Flow Worktree", guided)
        self.assertIn("Flow Worktree：<exact retained binding>", guided)
        self.assertIn("inherited Flow Worktree", closure)
        self.assertIn("`complete-worktree`", closure)
        self.assertIn("removes the Flow Worktree and branch", closure)

    def test_stage_four_uses_the_existing_topic_local_three_to_four_run(self) -> None:
        closure = (
            REPOSITORY / "skills/change-closure/SKILL.md"
        ).read_text(encoding="utf-8")
        lifecycle = (
            REPOSITORY
            / "skills/design-discussion/references/lifecycle-integration.md"
        ).read_text(encoding="utf-8")
        combined = closure + "\n" + lifecycle

        ordered = (
            "`read-topic`",
            "`prepare-phase-run` for `3→4` with carrier kind `change-closure`",
            "`authorize-phase-carrier`",
            "`phase-ready`",
            "`phase-activate`",
            "perform the ordinary Stage-4 Git and document closure",
            "`claim-phase-completion`",
            "`complete-phase-run`",
            "`finalize-phase-run`",
            "confirm `current_phase: 4`",
        )
        cursor = 0
        for marker in ordered:
            cursor = combined.index(marker, cursor) + len(marker)

        self.assertNotIn("`claim-phase-carrier`", closure)
        self.assertIn(
            "Standalone Stage-4 entry performs no discussion discovery or protocol calls.",
            closure,
        )

    def test_stage_four_recovery_keeps_git_and_phase_run_authority_separate(self) -> None:
        protocol = (
            REPOSITORY
            / "skills/change-closure/references/closure-protocol.md"
        ).read_text(encoding="utf-8")
        normalized = " ".join(protocol.split())

        for marker in (
            "Git remains authoritative",
            "Phase Run remains authoritative",
            "same Phase Run attempt active",
            "`retry-phase-run`",
            "`phase-outcome-unknown`",
            "`reconcile-phase-run`",
            "never repeat the Git closure",
        ):
            self.assertIn(marker, normalized)

    def test_closure_verifies_worktree_and_routes_unclear_representation(self) -> None:
        closure = (
            REPOSITORY / "skills/change-closure/SKILL.md"
        ).read_text(encoding="utf-8")
        actions = (
            REPOSITORY / "skills/change-closure/references/closure-actions.md"
        ).read_text(encoding="utf-8")

        self.assertIn("`verify-worktree`", closure)
        self.assertIn("`verify-worktree`", actions)
        self.assertIn("$ask-matt", closure)
        self.assertIn("representation", closure)

    def test_stage_four_rechecks_overall_completion_in_every_flow_mode(self) -> None:
        closure = (
            REPOSITORY / "skills/change-closure/SKILL.md"
        ).read_text(encoding="utf-8")

        review_start = closure.index(
            "Before the originating task reports the overall request complete"
        )
        footer_start = closure.index("归档结果：成功", review_start)
        review = closure[review_start:footer_start]

        for marker in (
            "in every flow mode",
            "the user's request",
            "the work promised by the primary flow",
            "every requested or promised item has an explicit completed outcome",
            "nothing remains in progress, pending, identified as a next step",
            "A successful stage result proves only that stage",
            "Emit the success footer only after the recheck passes",
        ):
            self.assertIn(marker, review)
        self.assertIn("整体复检：通过", closure[footer_start:])

    def test_stage_three_supports_standalone_without_restoring_one_to_three(self) -> None:
        framing = (
            REPOSITORY / "skills/problem-framing/SKILL.md"
        ).read_text(encoding="utf-8")
        guided = (
            REPOSITORY / "skills/guided-implementation/SKILL.md"
        ).read_text(encoding="utf-8")

        lifecycle = (
            REPOSITORY
            / "skills/design-discussion/references/lifecycle-integration.md"
        ).read_text(encoding="utf-8")
        originating = (
            REPOSITORY
            / "skills/guided-implementation/references/originating-task-protocol.md"
        ).read_text(encoding="utf-8")
        execution = (
            REPOSITORY
            / "skills/guided-implementation/references/execution-protocol.md"
        ).read_text(encoding="utf-8")
        worktree = (
            REPOSITORY
            / "skills/guided-implementation/references/worktree-execution.md"
        ).read_text(encoding="utf-8")
        adr = (
            REPOSITORY
            / "docs/adr/0005-allow-standalone-implementation.md"
        ).read_text(encoding="utf-8")

        self.assertIn("Phase 1 routes only to Phase 2", framing)
        self.assertNotIn("Route `1->3`", framing)
        self.assertIn("discussion-attached Phase 3 accepts only Phase-2", lifecycle)
        self.assertIn("explicit standalone Stage-3 invocation is unattached", lifecycle)
        self.assertNotIn("`1→3` wrapper run", lifecycle)
        self.assertIn("## Establish standalone authority", guided)
        self.assertIn("fixed standalone implementation brief", guided)
        self.assertIn("进入结果：未进入", guided)
        self.assertIn("For an explicit standalone entry", originating)
        self.assertIn("qualified standalone Stage-3 entry", execution)
        self.assertIn("standalone request", worktree)
        self.assertIn("Supersedes: ADR-0004", adr)
        self.assertIn("lifecycle-integration.md", guided)
        for marker in (
            "`claim-phase-carrier`",
            "`phase-ready`",
            "`claim-phase-completion`",
            "`complete-phase-run`",
            "`finalize-phase-run`",
        ):
            self.assertIn(marker, guided)

    def test_phase_one_always_owns_one_current_requirement_document(self) -> None:
        framing = (
            REPOSITORY / "skills/problem-framing/SKILL.md"
        ).read_text(encoding="utf-8")
        requirement_contract = (
            REPOSITORY
            / "skills/design-discussion/references/requirement-document-contract.md"
        ).read_text(encoding="utf-8")
        solution = (
            REPOSITORY / "skills/solution-design/SKILL.md"
        ).read_text(encoding="utf-8")
        solution_protocol = (
            REPOSITORY
            / "skills/solution-design/references/subagent-protocol.md"
        ).read_text(encoding="utf-8")
        solution_templates = (
            REPOSITORY / "skills/solution-design/references/templates.md"
        ).read_text(encoding="utf-8")

        self.assertIn("exactly one such document", framing)
        self.assertIn("before the first substantive question", requirement_contract)
        self.assertIn("After every material answer", requirement_contract)
        self.assertIn("not a transcript or an audit", requirement_contract)
        self.assertIn("at most one current concise change note", requirement_contract)
        self.assertIn("one committed, frozen Phase-1 requirement draft", solution)
        self.assertIn("supplement it from chat", solution)
        self.assertIn("before creating a Flow Worktree", solution)
        self.assertIn("fixed pre-launch anomaly", solution)
        self.assertIn("before `start-worktree` or launch", solution_protocol)
        self.assertIn("recommends returning to `$problem-framing`", solution_protocol)
        self.assertNotIn("current-task accepted handoff", solution_templates)
        self.assertNotIn("current-task-handoff", solution_templates)

    def test_confirmations_bind_context_not_magic_strings(self) -> None:
        confirmation = (
            REPOSITORY
            / "skills/design-discussion/references/confirmation-contract.md"
        ).read_text(encoding="utf-8")
        for marker in (
            "not a password or an exact-string challenge",
            "immediately preceding unresolved confirmation block",
            "Natural replies",
            "meaning-preserving wording",
            "confirmation_intent",
        ):
            self.assertIn(marker, confirmation)

        for relative_path in (
            "skills/problem-framing/SKILL.md",
            "skills/solution-design/SKILL.md",
            "skills/guided-implementation/SKILL.md",
            "skills/change-closure/SKILL.md",
        ):
            document = (REPOSITORY / relative_path).read_text(encoding="utf-8")
            self.assertIn("confirmation-contract.md", document)
            self.assertNotIn("only exact `确认`", document)

    def test_stage_boundaries_and_dependency_contract_match_execution(self) -> None:
        guided = (
            REPOSITORY / "skills/guided-implementation/SKILL.md"
        ).read_text(encoding="utf-8")
        dependencies = (REPOSITORY / "docs/dependencies.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("Implementation commits contain no documentation", guided)
        self.assertIn("never asks the user directly", guided)
        self.assertIn("separate explicit authority", guided)
        self.assertIn(
            "| `code-review` | stage 3 | Independently review the implementation |",
            dependencies,
        )

    def test_retained_worktree_recovery_has_explicit_stage_entry_tokens(self) -> None:
        execution = (
            REPOSITORY
            / "skills/guided-implementation/references/worktree-execution.md"
        ).read_text(encoding="utf-8")
        guided = (
            REPOSITORY / "skills/guided-implementation/SKILL.md"
        ).read_text(encoding="utf-8")
        closure = (
            REPOSITORY / "skills/change-closure/SKILL.md"
        ).read_text(encoding="utf-8")

        for marker in (
            "执行结果：未完成",
            "保留工作区：<canonical worktree path>",
            "no merge was published",
            "It never creates a replacement worktree.",
        ):
            self.assertIn(marker, execution)
        self.assertIn("$guided-implementation 重试", guided)
        self.assertIn("$change-closure 重试", closure)

    def test_child_topic_reference_reaches_the_real_codex_task_seam_in_order(self) -> None:
        reference = (
            REPOSITORY
            / "skills/design-discussion/references/child-topic-protocol.md"
        ).read_text(encoding="utf-8")
        ordered = [
            "publish the latest verified `CP-*`",
            "`prepare-handoff` exactly once",
            "Only a clear,",
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

    def test_removed_execution_contract_is_rejected_from_active_skill_files(self) -> None:
        temporary_directory, repository = self.make_repository()
        self.addCleanup(temporary_directory.cleanup)
        fixture = repository / "skills" / "alpha" / "scripts" / "protocol.py"
        fixture.write_text(
            'MODE = "exclusive-' + 'checkout-v2"\n',
            encoding="utf-8",
        )
        completed = self.run_validator(repository)
        self.assertEqual(completed.returncode, 1)
        issues = json.loads(completed.stdout)["issues"]
        self.assertIn(
            {
                "code": "removed-execution-contract",
                "marker": "exclusive-" + "checkout-v2",
                "path": "skills/alpha/scripts/protocol.py",
            },
            issues,
        )

    def test_test_discovery_is_dynamic_and_sorted(self) -> None:
        temporary_directory, repository = self.make_repository()
        self.addCleanup(temporary_directory.cleanup)
        first = repository / "skills" / "alpha" / "scripts" / "test_zeta.py"
        second = repository / "skills" / "alpha" / "scripts" / "test_alpha.py"
        nested = repository / "skills" / "alpha" / "nested" / "scripts" / "test_nested.py"
        ignored = repository / "skills" / "alpha" / "scripts" / "alpha_test.py"
        nested.parent.mkdir(parents=True)
        for path in (first, second, nested, ignored):
            path.write_text("# fixture\n", encoding="utf-8")
        completed = self.run_validator(repository, "--list-tests")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            completed.stdout.splitlines(),
            sorted(str(path.resolve()) for path in (second, nested, first)),
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

    def test_frontier_listing_excludes_completed_ready_issues(self) -> None:
        temporary_directory, repository = self.make_repository()
        self.addCleanup(temporary_directory.cleanup)
        issues = repository / ".scratch" / "design" / "issues"
        issues.mkdir(parents=True)
        open_issue = issues / "01-open.md"
        completed_issue = issues / "02-completed.md"
        open_issue.write_text(
            "# Open\n\nStatus: `ready-for-agent`\nLifecycle: `open`\n"
            "\n## Acceptance criteria\n\n- [ ] Implemented.\n",
            encoding="utf-8",
        )
        completed_issue.write_text(
            "# Completed\n\nStatus: `ready-for-agent`\nLifecycle: `completed`\n"
            "\n## Acceptance criteria\n\n- [x] Implemented.\n",
            encoding="utf-8",
        )

        frontier = self.run_validator(repository, "--list-frontier")

        self.assertEqual(frontier.returncode, 0, frontier.stderr)
        self.assertEqual(
            frontier.stdout.splitlines(),
            [open_issue.relative_to(repository).as_posix()],
        )

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
