from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
from unittest.mock import patch
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("thread_settings.py")
SKILL_ROOT = SCRIPT.parent.parent
REPOSITORY_ROOT = SKILL_ROOT.parent.parent
TEMPORARY_ROOT = Path(tempfile.gettempdir()).resolve()
SPEC = importlib.util.spec_from_file_location("thread_settings", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class ThreadSettingsTests(unittest.TestCase):
    thread_id = "019fd6ea-2afb-73e0-810c-0bb2636aeaae"
    parent_thread_id = "019fd6ea-2afb-73e0-810c-0bb2636aeab0"
    first_segment_id = "019fd6ea-2afb-73e0-810c-0bb2636aeab1"
    second_segment_id = "019fd6ea-2afb-73e0-810c-0bb2636aeab2"

    def make_rollout(
        self,
        root: Path,
        *,
        thread_id: str | None = None,
        session_meta_id: str | None = None,
        session_lineage_id: str | None = None,
        source: object = "vscode",
        include_session_lineage: bool = True,
        include_source: bool = True,
        contexts: list[tuple[str, str, str]] | None = None,
        trailing_fragment: str | None = None,
        segment_id: str | None = None,
        history_base_id: str | None = None,
        paginated: bool = False,
        timestamp: str = "2026-08-06T19-51-38",
    ) -> Path:
        resolved_thread_id = thread_id or self.thread_id
        folder = root / "2026" / "08" / "06"
        folder.mkdir(parents=True, exist_ok=True)
        segment_suffix = f"_{segment_id}" if segment_id else ""
        path = folder / f"rollout-{timestamp}-{resolved_thread_id}{segment_suffix}.jsonl"
        session_meta = {
            "id": (
                resolved_thread_id
                if session_meta_id is None
                else session_meta_id
            ),
        }
        if include_session_lineage:
            session_meta["session_id"] = (
                resolved_thread_id
                if session_lineage_id is None
                else session_lineage_id
            )
        if include_source:
            session_meta["source"] = source
        if paginated:
            session_meta["history_mode"] = "paginated"
            session_meta["history_base"] = (
                None
                if history_base_id is None
                else {
                    "thread_id": history_base_id,
                    "end_ordinal_exclusive": 1,
                    "end_byte_offset": 1,
                }
            )
        records = [
            {
                "type": "session_meta",
                "payload": session_meta,
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

    def make_index(self, root, path):
        database = root.parent / "state_5.sqlite"
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, rollout_path TEXT)")
            connection.execute("INSERT OR REPLACE INTO threads VALUES (?, ?)", (self.thread_id, str(path)))
        return database

    def test_index_selects_active_null_root_not_newest_filename(self):
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as directory:
            root = Path(directory) / "sessions"
            self.make_rollout(root, paginated=True, timestamp="2026-08-06T23-59-59")
            active = self.make_rollout(root, paginated=True, segment_id=self.first_segment_id,
                                       contexts=[("gpt-6-astra", "medium", "active-turn")])
            self.make_index(root, active)
            result = MODULE.resolve_current_thread_settings(root, environ={"CODEX_THREAD_ID": self.thread_id})
            self.assertEqual(result["turn_id"], "active-turn")
            self.assertEqual(result["model"], "gpt-6-astra")

    def test_index_invalid_bindings_do_not_fall_back(self):
        for variant in ("missing", "outside", "wrong-id", "symlink", "no-context"):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as directory:
                root = Path(directory) / "sessions"
                canonical = self.make_rollout(root)
                active = canonical
                if variant == "missing": active = canonical.with_name("missing.jsonl")
                if variant == "outside": active = root.parent / canonical.name
                if variant == "wrong-id": active = self.make_rollout(root, thread_id=self.parent_thread_id)
                if variant == "symlink":
                    active = canonical.with_name(canonical.name.replace("19-51-38", "20-51-38"))
                    active.symlink_to(canonical)
                if variant == "no-context":
                    active = self.make_rollout(root, paginated=True, segment_id=self.first_segment_id, contexts=[])
                self.make_index(root, active)
                with self.assertRaises((MODULE.SettingsError, OSError)):
                    MODULE.resolve_thread_settings(self.thread_id, root)

    def test_index_errors_are_not_treated_as_absent_database(self):
        for variant in ("missing-row", "corrupt", "symlink"):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as directory:
                root = Path(directory) / "sessions"
                active = self.make_rollout(root)
                database = self.make_index(root, active)
                if variant == "missing-row":
                    with sqlite3.connect(database) as connection:
                        connection.execute("DELETE FROM threads")
                elif variant == "corrupt": database.write_text("not sqlite")
                else:
                    target = database.with_suffix(".backup")
                    database.rename(target)
                    database.symlink_to(target)
                with self.assertRaises(MODULE.SettingsError):
                    MODULE.resolve_thread_settings(self.thread_id, root)

    def test_indexed_subagent_identity_and_verify(self):
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as directory:
            root = Path(directory) / "sessions"
            active = self.make_rollout(root, paginated=True, segment_id=self.first_segment_id,
                session_lineage_id=self.parent_thread_id,
                source={"subagent": {"thread_spawn": {"parent_thread_id": self.parent_thread_id}}})
            self.make_index(root, active)
            environment = {"CODEX_THREAD_ID": self.thread_id, "CODEX_SESSION_ID": self.parent_thread_id}
            for model, status in (("gpt-5.6-sol", "match"), ("gpt-6-astra", "changed")):
                result = MODULE.verify_current_thread_settings(root=root, environ=environment,
                    expected_model=model, expected_reasoning_effort="high")
                self.assertEqual(result["status"], status)
            environment["CODEX_SESSION_ID"] = self.thread_id
            with self.assertRaisesRegex(MODULE.SettingsError, "lineage conflict"):
                MODULE.resolve_current_thread_settings(root, environ=environment)

    def test_index_binding_change_during_read_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as directory:
            root = Path(directory) / "sessions"
            active = self.make_rollout(root)
            other = self.make_rollout(root, paginated=True, segment_id=self.first_segment_id)
            self.make_index(root, active)
            original = MODULE._read_rollout
            def changed(*args):
                result = original(*args)
                self.make_index(root, other)
                return result
            with patch.object(MODULE, "_read_rollout", side_effect=changed):
                with self.assertRaisesRegex(MODULE.SettingsError, "binding changed"):
                    MODULE.resolve_thread_settings(self.thread_id, root)

    def test_resolve_uses_latest_paginated_rollout_context(self):
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as directory:
            root = Path(directory)
            self.make_rollout(root, paginated=True, contexts=[])
            self.make_rollout(
                root,
                segment_id=self.first_segment_id,
                history_base_id=self.thread_id,
                paginated=True,
                contexts=[("gpt-5.6-terra", "medium", "turn-1")],
                timestamp="2026-08-06T19-52-38",
            )
            self.make_rollout(
                root,
                segment_id=self.second_segment_id,
                history_base_id=self.first_segment_id,
                paginated=True,
                contexts=[("gpt-5.6-sol", "high", "turn-2")],
                timestamp="2026-08-06T19-53-38",
            )

            result = MODULE.resolve_thread_settings(self.thread_id, root)

        self.assertEqual(result["model"], "gpt-5.6-sol")
        self.assertEqual(result["reasoning_effort"], "high")
        self.assertEqual(result["turn_id"], "turn-2")

    def test_paginated_rollout_uses_latest_complete_context_in_chain(self):
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as directory:
            root = Path(directory)
            self.make_rollout(
                root,
                paginated=True,
                contexts=[("gpt-5.6-sol", "high", "turn-1")],
            )
            self.make_rollout(
                root,
                segment_id=self.first_segment_id,
                history_base_id=self.thread_id,
                paginated=True,
                contexts=[],
                timestamp="2026-08-06T19-52-38",
            )

            result = MODULE.resolve_thread_settings(self.thread_id, root)

        self.assertEqual(result["turn_id"], "turn-1")

    def test_paginated_rollout_requires_canonical_root(self):
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as directory:
            root = Path(directory)
            self.make_rollout(
                root,
                segment_id=self.first_segment_id,
                history_base_id=self.thread_id,
                paginated=True,
            )

            with self.assertRaisesRegex(MODULE.SettingsError, "canonical root"):
                MODULE.resolve_thread_settings(self.thread_id, root)

    def test_paginated_canonical_rollout_must_be_the_root(self):
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as directory:
            root = Path(directory)
            self.make_rollout(
                root,
                history_base_id=self.first_segment_id,
                paginated=True,
            )

            with self.assertRaisesRegex(MODULE.SettingsError, "canonical root"):
                MODULE.resolve_thread_settings(self.thread_id, root)

    def test_paginated_rollout_rejects_legacy_suffixed_root(self):
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as directory:
            root = Path(directory)
            self.make_rollout(root, paginated=True)
            self.make_rollout(
                root,
                segment_id=self.first_segment_id,
                paginated=True,
                timestamp="2026-08-06T19-52-38",
            )

            with self.assertRaisesRegex(MODULE.SettingsError, "ambiguous"):
                MODULE.resolve_thread_settings(self.thread_id, root)

    def test_paginated_rollout_rejects_unproven_history(self):
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as directory:
            root = Path(directory)
            self.make_rollout(root, paginated=True)
            self.make_rollout(
                root,
                segment_id=self.second_segment_id,
                history_base_id=self.first_segment_id,
                paginated=True,
            )

            with self.assertRaisesRegex(MODULE.SettingsError, "incomplete"):
                MODULE.resolve_thread_settings(self.thread_id, root)

    def test_resolve_returns_latest_v4_receipt_without_private_fields(self):
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as directory:
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
                "protocol": "thread-settings-v5",
                "source": "codex-rollout-latest-turn-context",
                "thread_id": self.thread_id,
                "model": "gpt-5.6-sol",
                "reasoning_effort": "high",
                "turn_id": "turn-2",
            },
        )
        self.assertNotIn("message", result)
        self.assertNotIn("session_lineage_id", result)
        self.assertNotIn("parent_thread_id", result)

    def test_current_uses_matching_runtime_thread_and_session_ids(self):
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as directory:
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

    def test_current_accepts_native_subagent_lineage_identity(self):
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as directory:
            root = Path(directory)
            self.make_rollout(
                root,
                session_lineage_id=self.parent_thread_id,
                source={
                    "subagent": {
                        "thread_spawn": {
                            "parent_thread_id": self.parent_thread_id,
                            "depth": 1,
                        }
                    }
                },
            )
            result = MODULE.resolve_current_thread_settings(
                root,
                environ={
                    "CODEX_THREAD_ID": self.thread_id,
                    "CODEX_SESSION_ID": self.parent_thread_id,
                },
            )
            verification = MODULE.verify_current_thread_settings(
                expected_model="gpt-5.6-sol",
                expected_reasoning_effort="high",
                root=root,
                environ={
                    "CODEX_THREAD_ID": self.thread_id,
                    "CODEX_SESSION_ID": self.parent_thread_id,
                },
            )
        self.assertEqual(result["thread_id"], self.thread_id)
        self.assertEqual(verification["status"], "match")

    def test_current_accepts_missing_runtime_session_id(self):
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as directory:
            root = Path(directory)
            self.make_rollout(root)
            result = MODULE.resolve_current_thread_settings(
                root,
                environ={"CODEX_THREAD_ID": self.thread_id},
            )
        self.assertEqual(result["thread_id"], self.thread_id)

    def test_current_rejects_missing_thread_or_conflicting_lineage(self):
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as directory:
            root = Path(directory)
            self.make_rollout(root)
            with self.assertRaisesRegex(MODULE.SettingsError, "current thread"):
                MODULE.resolve_current_thread_settings(root, environ={})
            with self.assertRaisesRegex(MODULE.SettingsError, "session lineage"):
                MODULE.resolve_current_thread_settings(
                    root,
                    environ={
                        "CODEX_THREAD_ID": self.thread_id,
                        "CODEX_SESSION_ID": self.parent_thread_id,
                    },
                )

    def test_current_rejects_invalid_subagent_identity_shapes(self):
        invalid_shapes = [
            (
                self.parent_thread_id,
                {"subagent": {"thread_spawn": {}}},
            ),
            (
                self.parent_thread_id,
                {
                    "subagent": {
                        "thread_spawn": {"parent_thread_id": self.thread_id}
                    }
                },
            ),
            (
                self.parent_thread_id,
                {
                    "subagent": {
                        "thread_spawn": {"parent_thread_id": "not-a-thread-id"}
                    }
                },
            ),
            (
                self.thread_id,
                {
                    "subagent": {
                        "thread_spawn": {
                            "parent_thread_id": self.parent_thread_id
                        }
                    }
                },
            ),
        ]
        for session_lineage_id, source in invalid_shapes:
            with self.subTest(
                session_lineage_id=session_lineage_id,
                source=source,
            ), tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as directory:
                root = Path(directory)
                self.make_rollout(
                    root,
                    session_lineage_id=session_lineage_id,
                    source=source,
                )
                with self.assertRaisesRegex(
                    MODULE.SettingsError, "subagent task identity"
                ):
                    MODULE.resolve_current_thread_settings(
                        root,
                        environ={
                            "CODEX_THREAD_ID": self.thread_id,
                            "CODEX_SESSION_ID": session_lineage_id,
                        },
                    )

    def test_current_rejects_unsupported_or_incomplete_identity_shapes(self):
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as directory:
            root = Path(directory)
            self.make_rollout(root, source={"fork": {}})
            with self.assertRaisesRegex(MODULE.SettingsError, "unsupported"):
                MODULE.resolve_current_thread_settings(
                    root,
                    environ={"CODEX_THREAD_ID": self.thread_id},
                )

        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as directory:
            root = Path(directory)
            self.make_rollout(root, include_session_lineage=False)
            with self.assertRaisesRegex(MODULE.SettingsError, "root task identity"):
                MODULE.resolve_current_thread_settings(
                    root,
                    environ={"CODEX_THREAD_ID": self.thread_id},
                )

    def test_explicit_thread_id_accepts_legacy_identity_fields(self):
        legacy_shapes = [
            {
                "include_session_lineage": False,
                "include_source": False,
            },
            {
                "session_lineage_id": "legacy-lineage",
                "source": {"subagent": "legacy-subagent-source"},
            },
        ]
        for shape in legacy_shapes:
            with self.subTest(shape=shape), tempfile.TemporaryDirectory(
                dir=TEMPORARY_ROOT
            ) as directory:
                root = Path(directory)
                self.make_rollout(root, **shape)
                result = MODULE.resolve_thread_settings(self.thread_id, root)
                self.assertEqual(result["thread_id"], self.thread_id)

    def test_rejects_conflicting_session_meta_identity_fields(self):
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as directory:
            root = Path(directory)
            path = self.make_rollout(root)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "type": "session_meta",
                            "payload": {
                                "id": self.thread_id,
                                "session_id": self.parent_thread_id,
                                "source": "vscode",
                            },
                        }
                    )
                    + "\n"
                )
            with self.assertRaisesRegex(MODULE.SettingsError, "identity fields"):
                MODULE.resolve_thread_settings(self.thread_id, root)

    def test_verify_accepts_new_turn_with_same_settings(self):
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as directory:
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
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as directory:
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
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as directory:
            root = Path(directory)
            path = self.make_rollout(root, contexts=[])
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(["not", "an", "object"]) + "\n")
            with self.assertRaisesRegex(MODULE.SettingsError, "turn_context"):
                MODULE.resolve_thread_settings(self.thread_id, root)

    def test_rejects_session_meta_mismatch_and_symlinked_date_component(self):
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as directory:
            root = Path(directory)
            self.make_rollout(
                root,
                session_meta_id="019fd6ea-2afb-73e0-810c-0bb2636aeab0",
            )
            with self.assertRaisesRegex(MODULE.SettingsError, "session_meta"):
                MODULE.resolve_thread_settings(self.thread_id, root)

        with tempfile.TemporaryDirectory(
            dir=TEMPORARY_ROOT
        ) as directory, tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as other:
            root = Path(directory)
            (root / "2026").symlink_to(Path(other), target_is_directory=True)
            with self.assertRaisesRegex(MODULE.SettingsError, "unsafe directory"):
                MODULE.resolve_thread_settings(self.thread_id, root)

    def test_rejects_multiple_rollouts_and_ignores_partial_trailing_record(self):
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as directory:
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
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as directory:
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
        self.assertEqual(version.stdout.strip(), "thread-settings-v5")
        self.assertEqual(changed.returncode, 2)
        self.assertEqual(json.loads(changed.stdout)["status"], "changed")


class ThreadSettingsProtocolTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")

    def test_shared_reference_is_reachable_from_owner_skill(self):
        owner = self.read("skills/guided-implementation/SKILL.md")
        self.assertIn("[references/thread-settings-protocol.md]", owner)

    def test_dynamic_consumers_use_shared_v4_interface(self):
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
                self.assertIn("thread-settings-v5", protocol)
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
        self.assertIn("thread-settings-v5", contract)
        self.assertIn("workflow_runtime_version_mismatch", contract)
        self.assertRegex(contract, r"same\s+workflow-pipeline version")


if __name__ == "__main__":
    unittest.main()
