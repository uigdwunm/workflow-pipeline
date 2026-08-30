from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("thread_settings.py")
SKILL_ROOT = SCRIPT.parent.parent
REPOSITORY_ROOT = SKILL_ROOT.parent.parent
SPEC = importlib.util.spec_from_file_location("thread_settings", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class ThreadSettingsTests(unittest.TestCase):
    thread_id = "019fd6ea-2afb-73e0-810c-0bb2636aeaae"

    def make_rollout(
        self,
        root: Path,
        *,
        thread_id: str | None = None,
        session_id: str | None = None,
        contexts: list[tuple[str, str, str]] | None = None,
        trailing_fragment: str | None = None,
    ) -> Path:
        resolved_thread_id = thread_id or self.thread_id
        folder = root / "2026" / "08" / "06"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"rollout-2026-08-06T19-51-38-{resolved_thread_id}.jsonl"
        records = [
            {
                "type": "session_meta",
                "payload": {"id": session_id or resolved_thread_id},
            },
            {
                "type": "response_item",
                "payload": {"message": "must never be returned"},
            },
        ]
        resolved_contexts = (
            [("gpt-5.6-sol", "high", "turn-1")]
            if contexts is None
            else contexts
        )
        for model, effort, turn_id in resolved_contexts:
            records.append(
                {
                    "type": "turn_context",
                    "payload": {
                        "model": model,
                        "effort": effort,
                        "turn_id": turn_id,
                    },
                }
            )
        contents = "".join(json.dumps(row) + "\n" for row in records)
        if trailing_fragment is not None:
            contents += trailing_fragment
        path.write_text(contents)
        return path

    def test_resolve_returns_latest_v2_receipt_without_messages(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            self.make_rollout(
                root,
                contexts=[
                    ("gpt-5.6-terra", "medium", "turn-1"),
                    ("gpt-5.6-sol", "high", "turn-2"),
                ],
            )
            result = MODULE.resolve_thread_settings(self.thread_id, root)
        self.assertEqual(
            result,
            {
                "protocol": "thread-settings-v2",
                "source": "codex-rollout-latest-turn-context",
                "thread_id": self.thread_id,
                "model": "gpt-5.6-sol",
                "reasoning_effort": "high",
                "turn_id": "turn-2",
            },
        )
        self.assertNotIn("message", result)

    def test_current_uses_matching_runtime_thread_and_session_ids(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            self.make_rollout(root)
            result = MODULE.resolve_current_thread_settings(
                root,
                environ={
                    "CODEX_THREAD_ID": self.thread_id,
                    "CODEX_SESSION_ID": self.thread_id,
                },
            )
        self.assertEqual(result["thread_id"], self.thread_id)

    def test_current_rejects_missing_or_mismatched_runtime_identity(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            self.make_rollout(root)
            with self.assertRaisesRegex(MODULE.SettingsError, "CODEX_THREAD_ID"):
                MODULE.resolve_current_thread_settings(root, environ={})
            with self.assertRaisesRegex(MODULE.SettingsError, "runtime task identity"):
                MODULE.resolve_current_thread_settings(
                    root,
                    environ={
                        "CODEX_THREAD_ID": self.thread_id,
                        "CODEX_SESSION_ID": "019fd6ea-2afb-73e0-810c-0bb2636aeab0",
                    },
                )

    def test_verify_accepts_new_turn_with_same_settings(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            self.make_rollout(
                root,
                contexts=[
                    ("gpt-5.6-sol", "high", "turn-1"),
                    ("gpt-5.6-sol", "high", "turn-2"),
                ],
            )
            result = MODULE.verify_thread_settings(
                self.thread_id,
                expected_model="gpt-5.6-sol",
                expected_reasoning_effort="high",
                root=root,
            )
        self.assertEqual(result["status"], "match")
        self.assertEqual(result["observed"]["turn_id"], "turn-2")

    def test_verify_reports_changed_settings_with_current_receipt(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            self.make_rollout(
                root,
                contexts=[("gpt-5.6-terra", "medium", "turn-2")],
            )
            result = MODULE.verify_thread_settings(
                self.thread_id,
                expected_model="gpt-5.6-sol",
                expected_reasoning_effort="high",
                root=root,
            )
        self.assertEqual(result["status"], "changed")
        self.assertEqual(result["observed"]["model"], "gpt-5.6-terra")
        self.assertEqual(result["observed"]["reasoning_effort"], "medium")

    def test_ignores_non_object_records_and_requires_complete_settings(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            path = self.make_rollout(root, contexts=[])
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(["not", "an", "object"]) + "\n")
            with self.assertRaisesRegex(MODULE.SettingsError, "turn_context"):
                MODULE.resolve_thread_settings(self.thread_id, root)

    def test_rejects_session_meta_mismatch_and_symlinked_date_component(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            self.make_rollout(
                root,
                session_id="019fd6ea-2afb-73e0-810c-0bb2636aeab0",
            )
            with self.assertRaisesRegex(MODULE.SettingsError, "session_meta"):
                MODULE.resolve_thread_settings(self.thread_id, root)

        with tempfile.TemporaryDirectory(
            dir="/private/tmp"
        ) as directory, tempfile.TemporaryDirectory(dir="/private/tmp") as other:
            root = Path(directory)
            (root / "2026").symlink_to(Path(other), target_is_directory=True)
            with self.assertRaisesRegex(MODULE.SettingsError, "unsafe directory"):
                MODULE.resolve_thread_settings(self.thread_id, root)

    def test_rejects_multiple_rollouts_and_ignores_partial_trailing_record(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            self.make_rollout(root, trailing_fragment='{"type":"turn_context"')
            result = MODULE.resolve_thread_settings(self.thread_id, root)
            self.assertEqual(result["turn_id"], "turn-1")

            second = root / "2026" / "08" / "07"
            second.mkdir()
            second_path = (
                second
                / f"rollout-2026-08-07T19-51-38-{self.thread_id}.jsonl"
            )
            second_path.write_text(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {"id": self.thread_id},
                    }
                )
                + "\n"
            )
            with self.assertRaisesRegex(MODULE.SettingsError, "multiple rollouts"):
                MODULE.resolve_thread_settings(self.thread_id, root)

    def test_cli_protocol_version_and_changed_exit_code(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            self.make_rollout(root, contexts=[("gpt-5.6-terra", "medium", "turn-2")])
            environment = {
                **os.environ,
                "CODEX_SESSIONS_ROOT": str(root),
            }
            version = subprocess.run(
                [sys.executable, str(SCRIPT), "protocol-version"],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            changed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "verify",
                    "--thread-id",
                    self.thread_id,
                    "--model",
                    "gpt-5.6-sol",
                    "--reasoning-effort",
                    "high",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
        self.assertEqual(version.returncode, 0)
        self.assertEqual(version.stdout.strip(), "thread-settings-v2")
        self.assertEqual(changed.returncode, 2)
        self.assertEqual(json.loads(changed.stdout)["status"], "changed")


class ThreadSettingsProtocolTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")

    def test_shared_reference_is_reachable_from_owner_skill(self):
        owner = self.read("skills/guided-implementation/SKILL.md")
        self.assertIn("[references/thread-settings-protocol.md]", owner)

    def test_dynamic_consumers_use_shared_v2_interface(self):
        consumers = {
            "skills/design-discussion/references/child-topic-protocol.md": (
                "resolve --current",
                "verify --current",
            ),
            "skills/problem-framing/references/dedicated-grilling-protocol.md": (
                "resolve --thread-id",
                "verify --thread-id",
            ),
            "skills/solution-design/references/subagent-protocol.md": (
                "resolve --current",
                "verify --current",
            ),
        }
        for path, operations in consumers.items():
            with self.subTest(path=path):
                protocol = self.read(path)
                self.assertIn("thread-settings-v2", protocol)
                self.assertIn("thread-settings-protocol.md", protocol)
                for operation in operations:
                    self.assertIn(operation, protocol)

    def test_dedicated_task_launch_and_self_check_use_fixed_pair(self):
        launch = self.read(
            "skills/guided-implementation/references/originating-task-protocol.md"
        )
        execution = self.read(
            "skills/guided-implementation/references/execution-protocol.md"
        )
        for protocol in (launch, execution):
            self.assertIn("gpt-5.6-terra", protocol)
            self.assertIn("high", protocol)
        self.assertIn("`model` and `thinking`", launch)
        self.assertIn("thread_settings.py verify", execution)
        self.assertIn("--current", execution)
        self.assertIn("--model gpt-5.6-terra", execution)
        self.assertIn("--reasoning-effort high", execution)

    def test_dependency_contract_requires_one_workflow_version(self):
        contract = self.read("docs/dependencies.md")
        self.assertIn("thread-settings-v2", contract)
        self.assertIn("workflow_runtime_version_mismatch", contract)
        self.assertRegex(contract, r"same\s+workflow-pipeline version")


if __name__ == "__main__":
    unittest.main()
