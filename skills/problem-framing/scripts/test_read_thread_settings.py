from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("read_thread_settings.py")
SPEC = importlib.util.spec_from_file_location("read_thread_settings", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class ReadThreadSettingsTests(unittest.TestCase):
    thread_id = "019fd6ea-2afb-73e0-810c-0bb2636aeaae"

    def make_rollout(self, root: Path, *, session_id: str | None = None) -> Path:
        folder = root / "2026" / "08" / "06"
        folder.mkdir(parents=True)
        path = folder / f"rollout-2026-08-06T19-51-38-{self.thread_id}.jsonl"
        records = [
            {"type": "session_meta", "payload": {"id": session_id or self.thread_id}},
            {
                "type": "response_item",
                "payload": {"message": "must never be returned"},
            },
            {
                "type": "turn_context",
                "payload": {
                    "model": "gpt-5.6-sol",
                    "effort": "high",
                    "turn_id": "turn-1",
                },
            },
        ]
        path.write_text("".join(json.dumps(row) + "\n" for row in records))
        return path

    def test_returns_only_latest_settings(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            self.make_rollout(root)
            result = MODULE.resolve_thread_settings(self.thread_id, root)
        self.assertEqual(
            result,
            {
                "schema_version": "1",
                "source": "original-task-latest-turn-context",
                "thread_id": self.thread_id,
                "model": "gpt-5.6-sol",
                "effort": "high",
                "turn_id": "turn-1",
            },
        )
        self.assertNotIn("message", result)

    def test_rejects_session_meta_mismatch(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            self.make_rollout(root, session_id="019fd6ea-2afb-73e0-810c-0bb2636aeab0")
            with self.assertRaisesRegex(MODULE.SettingsError, "session_meta"):
                MODULE.resolve_thread_settings(self.thread_id, root)

    def test_rejects_symlinked_date_component(self):
        with tempfile.TemporaryDirectory(
            dir="/private/tmp"
        ) as directory, tempfile.TemporaryDirectory(dir="/private/tmp") as other:
            root = Path(directory)
            root.mkdir(exist_ok=True)
            (root / "2026").symlink_to(Path(other), target_is_directory=True)
            with self.assertRaisesRegex(MODULE.SettingsError, "unsafe directory"):
                MODULE.resolve_thread_settings(self.thread_id, root)

    def test_ignores_noncanonical_filenames(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            folder = root / "2026" / "08" / "06"
            folder.mkdir(parents=True)
            (folder / f"forged-{self.thread_id}.jsonl").write_text("{}\n")
            with self.assertRaisesRegex(MODULE.SettingsError, "no rollout"):
                MODULE.resolve_thread_settings(self.thread_id, root)


if __name__ == "__main__":
    unittest.main()
