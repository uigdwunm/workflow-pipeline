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

EVIDENCE_FIELDS = (
    "方案就绪检查：",
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


class SolutionDesignContractTests(unittest.TestCase):
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
            "## Exact child bootstrap prompt",
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
            "## Exact child bootstrap prompt",
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

    def test_readiness_adds_no_new_review_message_or_agent(self) -> None:
        readiness = READINESS_PATH.read_text(encoding="utf-8")
        self.assertNotIn("spawn_agent", readiness)
        self.assertNotIn("SOLUTION_READINESS_REQUIRED", readiness)
        self.assertNotIn("确认方式", readiness)
        self.assertIn("same `solution_designer`", readiness)


if __name__ == "__main__":
    unittest.main()
