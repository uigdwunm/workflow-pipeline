#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT_PATH = Path(__file__).with_name("supervision_protocol.py")
SPEC = importlib.util.spec_from_file_location("supervision_protocol", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
PROTOCOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROTOCOL)


class DocumentLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name).resolve()
        self.repository = self.root / "repository"
        subprocess.run(
            ["git", "init", "-q", str(self.repository)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_input(
        self,
        name: str,
        *,
        task_id: str,
        ttl_seconds: int = 300,
        stage: str = "problem-framing",
        purpose: str = "document-write",
    ) -> Path:
        path = self.root / name
        path.write_text(
            json.dumps(
                {
                    "owner_host_id": "host-1",
                    "owner_task_id": task_id,
                    "purpose": purpose,
                    "repository": str(self.repository),
                    "stage": stage,
                    "ttl_seconds": ttl_seconds,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def write_repository_lease_input(self, name: str) -> Path:
        path = self.root / name
        path.write_text(
            json.dumps(
                {
                    "base_branch": "main",
                    "base_head": "0" * 40,
                    "owner_host_id": "host-1",
                    "owner_task_id": "task-a",
                    "repository": str(self.repository),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def test_new_repository_lease_uses_v2_and_legacy_v1_still_inspects(self) -> None:
        lease_input = self.write_repository_lease_input("repository-lease.json")
        acquired = PROTOCOL.acquire_repository_lease(lease_input)
        self.assertEqual(acquired["lease_version"], 2)
        self.assertEqual(acquired["mode"], "exclusive-checkout-v2")
        PROTOCOL.release_repository_lease(
            Path(acquired["path"]),
            expected_id=acquired["lease_id"],
            expected_bytes=acquired["file_bytes"],
            expected_sha256=acquired["file_sha256"],
        )

        legacy_id = "1" * 32
        legacy = {
            "base_branch": "main",
            "base_head": "0" * 40,
            "complete": f"REPOSITORY_LEASE_COMPLETE:{legacy_id}",
            "lease_id": legacy_id,
            "lease_version": 1,
            "mode": "exclusive-checkout-v1",
            "owner_host_id": "legacy-host",
            "owner_task_id": "legacy-task",
            "repository": str(self.repository),
        }
        legacy_path = self.repository / ".git" / PROTOCOL.LEASE_FILENAME
        legacy_path.write_bytes(PROTOCOL._canonical_json_bytes(legacy))
        legacy_path.chmod(0o400)
        inspected = PROTOCOL.inspect_repository_lease(self.repository)
        self.assertEqual(inspected["lease_version"], 1)
        self.assertEqual(inspected["mode"], "exclusive-checkout-v1")

    def test_closure_document_receipt_requires_v2_lease_evidence_only(self) -> None:
        common_result = {
            "closure_commit": "a" * 40,
            "documents_updated": ["docs/requirement.md"],
            "verification": ["docs-check: passed"],
        }
        v2_facts = {
            "execution_mode": "exclusive-checkout-v2",
            "repository": str(self.repository),
        }
        with self.assertRaises(PROTOCOL.ProtocolError):
            PROTOCOL._validate_closure_phase_result(
                "documents-committed",
                common_result,
                v2_facts,
                closure_version=PROTOCOL.CLOSURE_VERSION,
            )

        v2_result = {
            **common_result,
            "document_lease": {
                "lease_id": "2" * 32,
                "path": str(
                    self.repository / ".git" / PROTOCOL.DOCUMENT_LEASE_FILENAME
                ),
                "state": "available",
                "version": 4,
            },
            "preserved_documents": ["docs/other-requirement.md"],
        }
        normalized = PROTOCOL._validate_closure_phase_result(
            "documents-committed",
            v2_result,
            v2_facts,
            closure_version=PROTOCOL.CLOSURE_VERSION,
        )
        self.assertEqual(normalized, v2_result)

        legacy_facts = {
            "execution_mode": "exclusive-checkout-v1",
            "repository": str(self.repository),
        }
        legacy_normalized = PROTOCOL._validate_closure_phase_result(
            "documents-committed",
            common_result,
            legacy_facts,
            closure_version=PROTOCOL.CLOSURE_VERSION,
        )
        self.assertEqual(legacy_normalized, common_result)

    def test_acquire_renew_verify_and_release(self) -> None:
        lease_input = self.write_input("lease.json", task_id="task-a")
        with mock.patch.object(PROTOCOL, "_now_epoch", return_value=1_000):
            acquired = PROTOCOL.acquire_document_lease(
                lease_input, wait_seconds=0, max_retries=0
            )
        self.assertTrue(acquired["acquired"])
        self.assertEqual(acquired["version"], 1)
        lease_path = Path(acquired["path"])

        with mock.patch.object(PROTOCOL, "_now_epoch", return_value=1_010):
            verified = PROTOCOL.verify_document_lease(
                lease_path,
                expected_id=acquired["holder"]["lease_id"],
                expected_version=1,
            )
            renewed = PROTOCOL.renew_document_lease(
                lease_path,
                expected_id=acquired["holder"]["lease_id"],
                expected_version=1,
                ttl_seconds=300,
            )
        self.assertTrue(verified["verified"])
        self.assertTrue(renewed["renewed"])
        self.assertEqual(renewed["version"], 2)
        self.assertEqual(renewed["holder"]["expires_at_epoch"], 1_310)

        with mock.patch.object(PROTOCOL, "_now_epoch", return_value=1_020):
            released = PROTOCOL.release_document_lease(
                lease_path,
                expected_id=acquired["holder"]["lease_id"],
                expected_version=2,
            )
        self.assertTrue(released["released"])
        self.assertEqual(released["state"], "available")
        self.assertEqual(released["version"], 3)
        self.assertIsNone(released["holder"])

    def test_design_discussion_is_an_accepted_document_lease_stage(self) -> None:
        lease_input = self.write_input(
            "design-discussion-lease.json",
            task_id="discussion-task",
            stage="design-discussion",
        )
        acquired = PROTOCOL.acquire_document_lease(
            lease_input, wait_seconds=0, max_retries=0
        )
        self.assertTrue(acquired["acquired"])
        self.assertEqual(acquired["holder"]["stage"], "design-discussion")
        PROTOCOL.release_document_lease(
            Path(acquired["path"]),
            expected_id=acquired["holder"]["lease_id"],
            expected_version=acquired["version"],
        )

    def test_non_git_project_uses_shared_codex_document_lease(self) -> None:
        non_git_project = self.root / "non-git-project"
        non_git_project.mkdir()
        self.repository = non_git_project
        lease_input = self.write_input(
            "non-git-document-lease.json",
            task_id="discussion-task",
            stage="design-discussion",
        )
        acquired = PROTOCOL.acquire_document_lease(
            lease_input, wait_seconds=0, max_retries=0
        )
        self.assertEqual(
            Path(acquired["path"]),
            non_git_project / ".codex" / PROTOCOL.DOCUMENT_LEASE_FILENAME,
        )
        verified = PROTOCOL.verify_document_lease(
            Path(acquired["path"]),
            expected_id=acquired["holder"]["lease_id"],
            expected_version=acquired["version"],
        )
        self.assertTrue(verified["verified"])
        released = PROTOCOL.release_document_lease(
            Path(acquired["path"]),
            expected_id=acquired["holder"]["lease_id"],
            expected_version=acquired["version"],
        )
        self.assertTrue(released["released"])

    def test_expired_lease_can_be_replaced_but_old_owner_cannot_release(self) -> None:
        first_input = self.write_input("first.json", task_id="task-a", ttl_seconds=10)
        second_input = self.write_input("second.json", task_id="task-b", ttl_seconds=10)
        with mock.patch.object(PROTOCOL, "_now_epoch", return_value=1_000):
            first = PROTOCOL.acquire_document_lease(
                first_input, wait_seconds=0, max_retries=0
            )
        with mock.patch.object(PROTOCOL, "_now_epoch", return_value=1_011):
            second = PROTOCOL.acquire_document_lease(
                second_input, wait_seconds=0, max_retries=0
            )
        self.assertTrue(second["acquired"])
        self.assertEqual(
            second["replaced_expired_lease_id"], first["holder"]["lease_id"]
        )
        self.assertEqual(second["version"], 2)

        with self.assertRaises(PROTOCOL.ProtocolError):
            PROTOCOL.release_document_lease(
                Path(first["path"]),
                expected_id=first["holder"]["lease_id"],
                expected_version=1,
            )

    def test_waits_ten_times_then_returns_timeout(self) -> None:
        first_input = self.write_input("first.json", task_id="task-a", ttl_seconds=1_000)
        second_input = self.write_input("second.json", task_id="task-b", ttl_seconds=1_000)
        with mock.patch.object(PROTOCOL, "_now_epoch", return_value=1_000):
            PROTOCOL.acquire_document_lease(first_input, wait_seconds=0, max_retries=0)

        with (
            mock.patch.object(PROTOCOL, "_now_epoch", return_value=1_001),
            mock.patch.object(PROTOCOL.time, "sleep") as sleep,
        ):
            result = PROTOCOL.acquire_document_lease(
                second_input, wait_seconds=10, max_retries=10
            )
        self.assertEqual(result["state"], "timeout")
        self.assertFalse(result["acquired"])
        self.assertEqual(result["attempts"], 11)
        self.assertEqual(result["waited_seconds"], 100)
        self.assertEqual(sleep.call_count, 10)
        sleep.assert_called_with(10)

    def test_concurrent_cli_acquire_has_one_winner(self) -> None:
        first_input = self.write_input("first.json", task_id="task-a")
        second_input = self.write_input("second.json", task_id="task-b")
        command = [
            sys.executable,
            str(SCRIPT_PATH),
            "acquire-document-lease",
            "--wait-seconds",
            "0",
            "--max-retries",
            "0",
            "--input",
        ]
        first = subprocess.Popen(
            command + [str(first_input)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        second = subprocess.Popen(
            command + [str(second_input)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        first_stdout, first_stderr = first.communicate(timeout=10)
        second_stdout, second_stderr = second.communicate(timeout=10)
        self.assertEqual(first.returncode, 0, first_stderr)
        self.assertEqual(second.returncode, 0, second_stderr)
        results = [json.loads(first_stdout), json.loads(second_stdout)]
        self.assertEqual(sum(bool(result["acquired"]) for result in results), 1)
        self.assertEqual(
            sorted(result["state"] for result in results), ["held", "timeout"]
        )


if __name__ == "__main__":
    unittest.main()
