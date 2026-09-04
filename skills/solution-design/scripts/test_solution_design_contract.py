#!/usr/bin/env python3

from __future__ import annotations

import json
from pathlib import Path
import re
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


def fenced_block(document: str, language: str) -> str:
    match = re.search(rf"```{re.escape(language)}\n(.*?)\n```", document, re.DOTALL)
    if match is None:
        raise AssertionError(f"missing {language} fenced block")
    return match.group(1)


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

        governed_contract = section(
            TEMPLATES,
            "## Governed TaskContract v2",
            "## Started status",
        )
        self.assertIn("complete local planning path manifest", governed_contract)

    def test_readiness_adds_no_new_review_message_or_agent(self) -> None:
        readiness = READINESS_PATH.read_text(encoding="utf-8")
        self.assertNotIn("spawn_agent", readiness)
        self.assertNotIn("SOLUTION_READINESS_REQUIRED", readiness)
        self.assertNotIn("确认方式", readiness)
        self.assertIn("same `solution_designer`", readiness)

    def test_child_role_is_semantic_and_accepts_a_governance_envelope(self) -> None:
        role = section(SKILL, "## Select the runtime role", "## Enter the stage")
        compact_role = " ".join(role.split())
        self.assertIn("non-root native subagent", compact_role)
        self.assertIn("solution-design-subagent-v2", compact_role)
        self.assertIn("semantic role `solution_designer`", compact_role)
        self.assertIn("governance envelope", compact_role)
        self.assertIn("native task name", compact_role)
        self.assertIn("not role identity", compact_role)
        self.assertNotIn("non-root `solution_designer` subagent", compact_role)
        self.assertNotIn("input is the exact bootstrap", compact_role)

    def test_governed_contract_separates_absolute_root_from_relative_paths(self) -> None:
        bootstrap_section = section(
            TEMPLATES,
            "## Canonical child bootstrap payload",
            "## Governed TaskContract v2",
        )
        governed_section = section(
            TEMPLATES,
            "## Governed TaskContract v2",
            "## Started status",
        )
        payload = fenced_block(bootstrap_section, "text")
        contract = json.loads(fenced_block(governed_section, "json"))
        contract["context"]["summary"] = payload

        self.assertEqual(contract["profile"], "strict")
        self.assertLessEqual(len(contract["context"]["summary"]), 8192)
        self.assertIn("协议：solution-design-subagent-v2", payload)
        self.assertIn("业务角色：solution_designer", payload)
        self.assertEqual(
            contract["context"]["verified"]["workspace_root"],
            "<absolute Flow Worktree path>",
        )
        self.assertEqual(
            contract["context"]["verified"]["baseline"],
            {"kind": "working_tree", "revision": None},
        )
        self.assertTrue(contract["forbidden_scope"])
        self.assertTrue(contract["evidence"])
        self.assertEqual(contract["spawn"]["fork_turns"], "none")

        locator_paths = contract["context"]["paths"]
        verified_paths = [
            item["path"]
            for item in contract["context"]["verified"]["required_paths"]
        ]
        self.assertEqual(locator_paths, verified_paths)
        for path in [*locator_paths, *verified_paths]:
            self.assertFalse(path.startswith("/"))
            self.assertNotIn("\\", path)
            self.assertNotIn("..", path.split("/"))

    def test_governed_dispatch_preserves_native_exact_target_lifecycle(self) -> None:
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

        for requirement in (
            "strict TaskContract v2",
            "context.summary",
            "repository-relative POSIX paths",
            "working_tree",
            "Never rewrite governance's returned `spawn_args`",
            "immediately confirm",
            "result=failed",
            "result=unknown",
        ):
            self.assertIn(requirement, compact_launch)
        self.assertIn("terminal notification", compact_waiting)
        self.assertIn("does not create a new governed attempt", compact_waiting)
        self.assertIn("close the governed task once", compact_completion)

    def test_continuous_launch_discloses_governance_before_final_stage_block(self) -> None:
        disclosure = section(
            TEMPLATES,
            "## Continuous automatic launch disclosure",
            "## Canonical child bootstrap payload",
        )
        self.assertIn("governance `user_message` immediately\nbefore this block", disclosure)
        self.assertIn("final user-visible commentary", disclosure)
        self.assertIn("执行环境：已创建 Flow Worktree", disclosure)
        self.assertIn("绑定=<exact binding>", disclosure)
        self.assertNotIn("本条披露后调用 `start-worktree`", disclosure)


if __name__ == "__main__":
    unittest.main()
