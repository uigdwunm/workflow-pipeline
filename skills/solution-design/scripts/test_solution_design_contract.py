#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path
import unittest


SKILL_ROOT = Path(__file__).resolve().parent.parent
SKILL = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
PROTOCOL = (SKILL_ROOT / "references" / "subagent-protocol.md").read_text(
    encoding="utf-8"
)
TEMPLATES = (SKILL_ROOT / "references" / "templates.md").read_text(
    encoding="utf-8"
)
READINESS_PATH = SKILL_ROOT / "references" / "design-readiness.md"
GUIDED_ROOT = SKILL_ROOT.parent / "guided-implementation"
GUIDED_SKILL = (GUIDED_ROOT / "SKILL.md").read_text(encoding="utf-8")
GUIDED_EXECUTION = (
    GUIDED_ROOT / "references" / "execution-protocol.md"
).read_text(encoding="utf-8")
GUIDED_ORIGINATING = (
    GUIDED_ROOT / "references" / "originating-task-protocol.md"
).read_text(encoding="utf-8")

EVIDENCE_FIELDS = (
    "方案就绪检查：",
    "变更契约预检：",
    "模块与 Interface：",
    "决策覆盖：",
    "状态覆盖：",
    "测试 seam：",
    "Tickets 追踪：",
)


def section(document: str, heading: str, next_heading: str | None = None) -> str:
    start = document.index(heading)
    if next_heading is None:
        return document[start:]
    end = document.index(next_heading, start + len(heading))
    return document[start:end]


def compact(document: str) -> str:
    return " ".join(document.split())


class SolutionDesignContractTests(unittest.TestCase):
    def test_change_contract_preflight_covers_real_contract_dimensions_before_review(self) -> None:
        readiness = READINESS_PATH.read_text(encoding="utf-8")
        preflight = section(readiness, "## Bounded change-contract preflight", "## Spec readiness")
        compact = " ".join(preflight.split())
        for obligation in (
            "affected entrypoint",
            "production callers",
            "owning module",
            "interface",
            "inputs",
            "outputs",
            "failures",
            "dependency seam",
            "persistence owner",
            "transitions",
            "retry or recovery",
            "call-sequence",
            "governing requirement",
            "real production call chain",
            "contradiction",
            "SOLUTION_DESIGN_ANOMALY",
        ):
            self.assertIn(obligation, compact)

        protocol = section(
            PROTOCOL,
            "## Stepwise review flow",
            "## Continuous exception-only flow",
        )
        self.assertLess(
            protocol.index("bounded change-contract preflight"),
            protocol.index("SOLUTION_REVIEW_REQUIRED"),
        )

    def test_preflight_completion_evidence_is_compact_and_not_a_new_artifact(self) -> None:
        for document in (SKILL, PROTOCOL, TEMPLATES):
            self.assertIn("变更契约预检", document)
        self.assertIn(
            "not a new readiness artifact",
            " ".join(READINESS_PATH.read_text(encoding="utf-8").split()),
        )
        self.assertNotIn("CHANGE_CONTRACT_REVIEW_REQUIRED", PROTOCOL)

    def test_first_tdd_slice_crosses_the_real_changed_boundary_before_expansion(self) -> None:
        for document in (GUIDED_SKILL, GUIDED_EXECUTION):
            normalized = compact(document)
            self.assertIn("representative", normalized)
            self.assertIn("changed internal boundary", normalized)
            self.assertIn("production caller", normalized)
            self.assertIn("smallest failing test", normalized)
            self.assertTrue(
                "Only then" in normalized
                or "Only after that slice passes" in normalized
            )

        execution = compact(
            section(GUIDED_EXECUTION, "Before implementing the remaining Tickets")
        )
        self.assertLess(
            execution.index("smallest failing test"),
            execution.index("expand the same pattern"),
        )

    def test_boundary_doubles_are_prohibited_but_downstream_doubles_remain_allowed(self) -> None:
        for document in (GUIDED_SKILL, GUIDED_EXECUTION):
            for forbidden in ("mock", "stub", "fake", "in-memory substitute"):
                self.assertIn(forbidden, document)
            self.assertIn("external", document)
            self.assertIn("downstream", document)
            self.assertIn("implementation-authority/testing-seam gap", document)
            self.assertTrue(
                "do not bypass" in document or "instead of bypassing" in document
            )

    def test_repeated_mechanisms_require_diagnosis_evidence_before_another_fix(self) -> None:
        for document in (GUIDED_SKILL, GUIDED_EXECUTION, GUIDED_ORIGINATING):
            normalized = compact(document)
            for trigger in (
                "same failure mechanism",
                "same-class regression",
                "successive review/test outcomes",
            ):
                self.assertIn(trigger, normalized)
            self.assertIn("prior failure", normalized)
            self.assertIn("why", normalized)
            self.assertTrue(
                "minimal effective validation" in normalized
                or "smallest effective validation" in normalized
            )
            self.assertIn("sibling paths", normalized)

        diagnosis = compact(
            section(
                GUIDED_ORIGINATING,
                "## Diagnosis-first remediation",
                "## Retain and hand off",
            )
        )
        self.assertLess(
            diagnosis.index("Before another edit"),
            diagnosis.index("repairs in scope"),
        )
        self.assertIn("prior candidate and finding identities", diagnosis)
        self.assertIn("same Dedicated Implementation Task", diagnosis)
        self.assertIn("Flow Worktree", diagnosis)

    def test_scope_gaps_and_stalled_replacement_remain_distinct(self) -> None:
        self.assertIn("implementation-authority or anomaly decision", GUIDED_ORIGINATING)
        normalized = compact(GUIDED_SKILL + GUIDED_EXECUTION + GUIDED_ORIGINATING)
        self.assertIn("stalled-task replacement", normalized)
        self.assertIn("exception above", normalized)
        for prohibition in (
            "fixed remediation round gate",
            "automatic replacement",
            "new Workflow Stage",
            "diagnostic document",
        ):
            self.assertIn(prohibition, normalized)
        self.assertNotIn("automatic replacement agent", normalized)

    def test_readiness_reference_is_reached_by_the_child_before_native_spec_work(self) -> None:
        self.assertTrue(READINESS_PATH.is_file())
        self.assertIn(
            "[references/design-readiness.md](references/design-readiness.md)",
            SKILL,
        )
        child_responsibilities = section(
            PROTOCOL,
            "## Child responsibilities and document authority",
            "## Stepwise review flow",
        )
        self.assertIn("design-readiness.md", child_responsibilities)
        self.assertLess(
            child_responsibilities.index("design-readiness.md"),
            child_responsibilities.index("$to-spec"),
        )

    def test_spec_and_ticket_readiness_precede_existing_review_or_publication_seams(self) -> None:
        stepwise = section(
            PROTOCOL,
            "## Stepwise review flow",
            "## Continuous exception-only flow",
        )
        continuous = section(
            PROTOCOL,
            "## Continuous exception-only flow",
            "## Anomaly contract",
        )
        for flow in (stepwise, continuous):
            self.assertIn("Spec readiness", flow)
            self.assertIn("Ticket traceability", flow)
        self.assertLess(
            stepwise.index("Spec readiness"),
            stepwise.index("SOLUTION_REVIEW_REQUIRED"),
        )
        self.assertLess(
            stepwise.index("Ticket traceability"),
            stepwise.index("TICKETS_REVIEW_REQUIRED"),
        )

    def test_continuous_mode_skips_human_review_but_keeps_readiness(self) -> None:
        flow_mode = section(
            SKILL,
            "## Track the flow mode",
            "## Load subagent actions only at their seam",
        )
        self.assertIn("skip human review", flow_mode)
        self.assertIn("solution readiness", flow_mode)
        self.assertNotIn("skip human review and additional quality gates", flow_mode)

        bootstrap = section(
            TEMPLATES,
            "## Canonical child bootstrap payload",
            "## Started status",
        )
        self.assertIn("方案就绪检查", bootstrap)
        self.assertIn("不发出 review 消息", bootstrap)
        self.assertNotIn("不运行额外质量门", bootstrap)

    def test_scope_expansion_requires_an_explicit_user_decision_before_inclusion(self) -> None:
        visible_scope = section(
            SKILL,
            "## Keep the solution inside the visible scope",
            "## Track the flow mode",
        )
        for obligation in (
            "SOLUTION_DESIGN_ANOMALY",
            "in every flow mode",
            "before incorporating it",
            "reasonable alternatives including staying within scope",
            "user's explicit decision",
        ):
            self.assertIn(obligation, visible_scope)

        bootstrap = section(
            TEMPLATES,
            "## Canonical child bootstrap payload",
            "## Started status",
        )
        self.assertIn("范围扩展", bootstrap)
        self.assertIn("SOLUTION_DESIGN_ANOMALY", bootstrap)
        self.assertIn("等待用户明确决定", bootstrap)

    def test_completion_and_both_handoffs_carry_readiness_evidence(self) -> None:
        blocks = (
            section(TEMPLATES, "## Completion", "## Stepwise success footer"),
            section(
                TEMPLATES,
                "## Stepwise success footer",
                "## Continuous completion handoff",
            ),
            section(
                TEMPLATES,
                "## Continuous completion handoff",
                "## Pre-launch anomaly",
            ),
        )
        for block in blocks:
            for field in EVIDENCE_FIELDS:
                self.assertIn(field, block)

    def test_complete_planning_path_manifest_reaches_publication_and_handoffs(self) -> None:
        blocks = (
            section(TEMPLATES, "## Completion", "## Stepwise success footer"),
            section(
                TEMPLATES,
                "## Stepwise success footer",
                "## Continuous completion handoff",
            ),
            section(
                TEMPLATES,
                "## Continuous completion handoff",
                "## Pre-launch anomaly",
            ),
        )
        for block in blocks:
            self.assertIn("本地规划路径：", block)
            self.assertIn("- <repository-relative path>", block)

        repository_boundary = section(
            PROTOCOL,
            "## Repository and publication boundaries",
            "## Waiting, decisions, and recovery",
        )
        compact_repository_boundary = " ".join(repository_boundary.split())
        for obligation in (
            "first-parent changed-path set",
            "exactly match",
            "sorted",
            "repository-relative",
            "one path per list item",
        ):
            self.assertIn(obligation, compact_repository_boundary)

        completion = section(PROTOCOL, "## Completion intake and stage transition")
        compact_completion = " ".join(completion.split())
        self.assertIn("`本地规划路径`", compact_completion)
        self.assertIn("unchanged as `allowed_paths`", compact_completion)

        complete_stage = section(SKILL, "## Complete the stage")
        compact_complete_stage = " ".join(complete_stage.split())
        self.assertIn("`本地规划路径`", compact_complete_stage)
        self.assertIn("unchanged as `allowed_paths`", compact_complete_stage)

        bootstrap = section(
            TEMPLATES,
            "## Canonical child bootstrap payload",
            "## Started status",
        )
        self.assertIn("本地规划路径", bootstrap)

    def test_readiness_adds_no_new_review_message_or_agent(self) -> None:
        readiness = READINESS_PATH.read_text(encoding="utf-8")
        self.assertNotIn("spawn_agent", readiness)
        self.assertNotIn("SOLUTION_READINESS_REQUIRED", readiness)
        self.assertNotIn("确认方式", readiness)
        self.assertIn("same `solution_designer`", readiness)

    def test_child_role_is_semantic_and_accepts_an_orchestration_envelope(self) -> None:
        role = section(SKILL, "## Select the runtime role", "## Enter the stage")
        compact_role = " ".join(role.split())
        self.assertIn("non-root native subagent", compact_role)
        self.assertIn("solution-design-subagent-v2", compact_role)
        self.assertIn("semantic role `solution_designer`", compact_role)
        self.assertIn("orchestration envelope", compact_role)
        self.assertIn("native task name", compact_role)
        self.assertIn("not role identity", compact_role)
        self.assertNotIn("non-root `solution_designer` subagent", compact_role)
        self.assertNotIn("input is the exact bootstrap", compact_role)

    def test_dispatch_contract_keeps_platform_mechanics_adapter_owned(self) -> None:
        launch = section(
            PROTOCOL,
            "## Launch and model inheritance",
            "## Child responsibilities and document authority",
        )
        waiting = section(
            PROTOCOL,
            "## Waiting, decisions, and recovery",
            "## Completion intake and stage transition",
        )
        completion = section(PROTOCOL, "## Completion intake and stage transition")
        compact_launch = " ".join(launch.split())
        compact_waiting = " ".join(waiting.split())
        compact_completion = " ".join(completion.split())

        for adapter_owned in (
            "native tool arguments",
            "task naming",
            "exact-target binding",
            "liveness",
            "terminal normalization",
            "interruption",
            "bookkeeping",
        ):
            self.assertIn(adapter_owned, compact_launch)
        for stage_owned in (
            "stage payload",
            "review points",
            "anomaly semantics",
            "completion contract",
        ):
            self.assertIn(stage_owned, compact_launch)
        for waiting_rule in (
            "bounded waits",
            "timeout policy",
            "backoff",
            "liveness reconciliation",
            "Wait only while that child is currently known to be active",
            "stop waiting",
        ):
            self.assertIn(waiting_rule, compact_waiting)
        self.assertIn("same semantic child identity", compact_waiting)
        self.assertIn("adapter owns any native interruption", compact_completion)

    def test_private_adapter_protocol_is_not_embedded(self) -> None:
        for leaked_mechanic in (
            "TaskContract v2",
            "state-v9",
            "spawn_args",
            "wait_agent",
            "timeout_ms",
            "3600000",
        ):
            self.assertNotIn(leaked_mechanic, PROTOCOL)
            self.assertNotIn(leaked_mechanic, TEMPLATES)

    def test_continuous_launch_discloses_adapter_before_final_stage_block(self) -> None:
        disclosure = section(
            TEMPLATES,
            "## Continuous automatic launch disclosure",
            "## Canonical child bootstrap payload",
        )
        self.assertIn("disclosure required by the current orchestration adapter immediately\nbefore this block", disclosure)
        self.assertIn("final user-visible commentary", disclosure)
        self.assertIn("执行环境：已创建 Flow Worktree", disclosure)
        self.assertIn("绑定=<exact binding>", disclosure)
        self.assertNotIn("本条披露后调用 `start-worktree`", disclosure)


if __name__ == "__main__":
    unittest.main()
