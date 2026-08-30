#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest import mock


SCRIPT_PATH = Path(__file__).with_name("supervision_protocol.py")
DISCUSSION_SCRIPT_PATH = (
    Path(__file__).parents[2]
    / "design-discussion"
    / "scripts"
    / "discussion_protocol.py"
)
SPEC = importlib.util.spec_from_file_location("supervision_protocol", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
PROTOCOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROTOCOL)
MATRIX_PROOF_PATH = (
    Path(__file__).parents[2] / "design-discussion" / "scripts" / "matrix_proof.py"
)
MATRIX_PROOF_SPEC = importlib.util.spec_from_file_location(
    "matrix_proof", MATRIX_PROOF_PATH
)
assert MATRIX_PROOF_SPEC is not None and MATRIX_PROOF_SPEC.loader is not None
MATRIX_PROOF = importlib.util.module_from_spec(MATRIX_PROOF_SPEC)
MATRIX_PROOF_SPEC.loader.exec_module(MATRIX_PROOF)
matrix_proof = MATRIX_PROOF.matrix_proof


class DocumentLeaseTests(unittest.TestCase):
    def test_command_registry_is_the_single_complete_cli_authority(self) -> None:
        self.assertEqual(len(PROTOCOL.COMMAND_REGISTRY.names), 42)
        self.assertEqual(
            set(PROTOCOL.COMMAND_REGISTRY.names),
            set(PROTOCOL._build_parser()._subparsers._group_actions[0].choices),
        )
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertNotIn("if arguments.command ==", source)
        self.assertNotIn("elif arguments.command ==", source)

    @matrix_proof("cutover:stale-state-fails-closed")
    def test_cutover_preflight_rejects_old_active_artifacts_without_mutation(self) -> None:
        stale_targets = [
            self.repository / ".git" / "cc-switch-guided-implementation-lease.json",
            self.repository / ".git" / "cc-switch-worktree-execution-leases",
            self.repository / ".git" / "cc-switch-isolated-worktree-confirmations",
        ]
        for index, target in enumerate(stale_targets):
            if target.suffix:
                target.write_bytes(f"old-state-{index}".encode("ascii"))
                before = target.read_bytes()
            else:
                target.mkdir()
                marker = target / "state.json"
                marker.write_bytes(f"old-state-{index}".encode("ascii"))
                before = marker.read_bytes()
            with self.assertRaises(PROTOCOL.ProtocolError) as rejected:
                PROTOCOL.cutover_preflight(self.repository)
            self.assertEqual(rejected.exception.code, "unsupported_stale_execution_state")
            if target.is_dir():
                self.assertEqual((target / "state.json").read_bytes(), before)
                (target / "state.json").unlink()
                target.rmdir()
            else:
                self.assertEqual(target.read_bytes(), before)
                target.unlink()

    @matrix_proof("cutover:stale-state-fails-closed")
    def test_old_handoff_control_and_closure_fixtures_fail_closed_without_mutation(self) -> None:
        runtime_root = self.root / "handoffs"
        runtime_root.mkdir(mode=0o700)
        handoff_id = "a" * 32
        handoff_directory = runtime_root / handoff_id
        handoff_directory.mkdir(mode=0o700)
        handoff_path = handoff_directory / PROTOCOL.HANDOFF_FILENAME
        handoff_path.write_bytes(
            PROTOCOL._canonical_json_bytes(
                {"handoff_id": handoff_id, "handoff_version": 4}
            )
        )
        handoff_path.chmod(0o400)
        handoff_before = handoff_path.read_bytes()
        with self.assertRaises(PROTOCOL.ProtocolError) as old_handoff:
            PROTOCOL.verify_handoff(handoff_path, runtime_root=runtime_root)
        self.assertEqual(old_handoff.exception.code, "unsupported_stale_execution_state")
        self.assertEqual(handoff_path.read_bytes(), handoff_before)

        control_path = self.root / "old-control.json"
        control_path.write_bytes(PROTOCOL._canonical_json_bytes({"control_version": 5}))
        control_before = control_path.read_bytes()
        with self.assertRaises(PROTOCOL.ProtocolError) as old_control:
            PROTOCOL.verify_control(
                control_path, handoff_path, runtime_root=runtime_root
            )
        self.assertEqual(old_control.exception.code, "unsupported_stale_execution_state")
        self.assertEqual(control_path.read_bytes(), control_before)

        closure_root = self.root / "closures"
        closure_root.mkdir(mode=0o700)
        closure_id = "b" * 32
        closure_path = closure_root / f"{closure_id}.json"
        closure_path.write_bytes(
            PROTOCOL._canonical_json_bytes(
                {"closure_id": closure_id, "closure_version": 3}
            )
        )
        closure_before = closure_path.read_bytes()
        closure_input = self.root / "old-closure-receipt.json"
        closure_input.write_bytes(
            PROTOCOL._canonical_json_bytes(
                {
                    "closure": {
                        "file_bytes": len(closure_before),
                        "file_sha256": hashlib.sha256(closure_before).hexdigest(),
                        "id": closure_id,
                        "path": str(closure_path),
                        "version": 3,
                    },
                    "implementation_id": "implementation-old",
                    "repository": str(self.repository),
                }
            )
        )
        with mock.patch.object(PROTOCOL, "WORKTREE_CLOSURE_ROOT", closure_root):
            with self.assertRaises(PROTOCOL.ProtocolError) as old_closure:
                PROTOCOL.verify_worktree_closure_receipt(closure_input)
        self.assertEqual(old_closure.exception.code, "unsupported_stale_execution_state")
        self.assertEqual(closure_path.read_bytes(), closure_before)

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
        paths: list[str] | None = None,
        implementation_id: str | None = None,
    ) -> Path:
        path = self.root / name
        path.write_text(
            json.dumps(
                {
                    "implementation_id": implementation_id,
                    "owner_host_id": "host-1",
                    "owner_task_id": task_id,
                    "paths": ["docs/plan.md"] if paths is None else paths,
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

    def write_repository_coordination_input(self, name: str) -> Path:
        path = self.root / name
        path.write_text(
            json.dumps(
                {
                    "owner_host_id": "host-1",
                    "owner_task_id": "discussion-task",
                    "purpose": "checkpoint-publish",
                    "repository": str(self.repository),
                    "stage": "design-discussion",
                    "ttl_seconds": 300,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def write_worktree_claim_input(
        self,
        name: str,
        *,
        base: str,
        implementation_id: str,
        branch: str,
        worktree: Path,
    ) -> Path:
        checkpoint = {
            "checkpoint_id": "CP-" + implementation_id,
            "commit_id": base,
            "committed": True,
            "read_only": True,
            "sha256": hashlib.sha256(("source:" + implementation_id).encode()).hexdigest(),
        }
        protection = PROTOCOL.inspect_implementation_sources(self.repository)
        if implementation_id not in protection["active_implementation_ids"]:
            tracked = subprocess.run(
                ["git", "-C", str(self.repository), "ls-tree", "-r", "--name-only", base],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout.splitlines()
            self.assertTrue(tracked)
            source_path = tracked[0]
            source_bytes = subprocess.run(
                ["git", "-C", str(self.repository), "show", f"{base}:{source_path}"],
                check=True,
                stdout=subprocess.PIPE,
            ).stdout
            blob_id = subprocess.run(
                ["git", "-C", str(self.repository), "rev-parse", f"{base}:{source_path}"],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout.strip()
            protection_input = self.root / f"claim-protection-{implementation_id}.json"
            protection_input.write_text(
                json.dumps(
                    {
                        "artifacts": [
                            {
                                "blob_id": blob_id,
                                "path": source_path,
                                "sha256": hashlib.sha256(source_bytes).hexdigest(),
                            }
                        ],
                        "implementation_id": implementation_id,
                        "repository": str(self.repository),
                        "source_checkpoint": checkpoint,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            PROTOCOL.protect_implementation_source(protection_input)
        path = self.root / name
        path.write_text(
            json.dumps(
                {
                    "base_commit": base,
                    "implementation_branch": branch,
                    "implementation_id": implementation_id,
                    "phase_run_id": "PR-" + hashlib.sha256(implementation_id.encode()).hexdigest()[:16],
                    "repository": str(self.repository),
                    "scope_sha256": hashlib.sha256(implementation_id.encode()).hexdigest(),
                    "source_checkpoint": checkpoint,
                    "topic_id": "topic-worktree-claim",
                    "worktree_path": str(worktree),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def write_claim_operation_input(
        self, name: str, claim: dict[str, object]
    ) -> Path:
        path = self.root / name
        path.write_text(
            json.dumps(
                {
                    "claim": {
                        key: claim[key]
                        for key in (
                            "claim_id",
                            "file_bytes",
                            "file_sha256",
                            "path",
                            "version",
                        )
                    },
                    "repository": str(self.repository),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def commit_repository(self, message: str) -> str:
        subprocess.run(["git", "-C", str(self.repository), "add", "-A"], check=True)
        subprocess.run(
            [
                "git", "-C", str(self.repository), "-c", "user.name=Test",
                "-c", "user.email=test@example.com", "commit", "-qm", message,
            ],
            check=True,
        )
        return subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()

    def prepare_accepted_candidate(
        self,
        *,
        implementation_id: str,
        relative_path: str,
        candidate_content: str,
    ) -> tuple[dict[str, object], dict[str, object], str, str, Path]:
        base = subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        source_path = self.repository / "docs" / "plan.md"
        source_bytes = source_path.read_bytes()
        source_blob = subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", f"{base}:docs/plan.md"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        checkpoint = {
            "checkpoint_id": "CP-" + implementation_id,
            "commit_id": base,
            "committed": True,
            "read_only": True,
            "sha256": hashlib.sha256(("source:" + implementation_id).encode()).hexdigest(),
        }
        protection_input = self.root / f"protection-{implementation_id}.json"
        protection_input.write_text(
            json.dumps(
                {
                    "artifacts": [
                        {
                            "blob_id": source_blob,
                            "path": "docs/plan.md",
                            "sha256": hashlib.sha256(source_bytes).hexdigest(),
                        }
                    ],
                    "implementation_id": implementation_id,
                    "repository": str(self.repository),
                    "source_checkpoint": checkpoint,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        PROTOCOL.protect_implementation_source(protection_input)

        branch = "codex/" + implementation_id.removeprefix("implementation-")
        worktree = self.root / (implementation_id + "-worktree")
        claim_input = self.write_worktree_claim_input(
            f"claim-{implementation_id}.json",
            base=base,
            implementation_id=implementation_id,
            branch=branch,
            worktree=worktree,
        )
        claim = PROTOCOL.reserve_worktree_execution_claim(claim_input)
        provision_input = self.write_claim_operation_input(
            f"provision-{implementation_id}.json", claim
        )
        active_claim = PROTOCOL.provision_claimed_worktree(provision_input)
        candidate_path = worktree / relative_path
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.write_text(candidate_content, encoding="utf-8")
        subprocess.run(["git", "-C", str(worktree), "add", relative_path], check=True)
        subprocess.run(
            [
                "git", "-C", str(worktree), "-c", "user.name=Test",
                "-c", "user.email=test@example.com", "commit", "-qm", implementation_id,
            ],
            check=True,
        )
        candidate = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        target_branch = subprocess.run(
            ["git", "-C", str(self.repository), "branch", "--show-current"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        review_input = self.root / f"review-{implementation_id}.json"
        review_input.write_text(
            json.dumps(
                {
                    "candidate_commit": candidate,
                    "execution_claim": {
                        key: active_claim[key]
                        for key in ("claim_id", "file_bytes", "file_sha256", "path", "version")
                    },
                    "implementation_id": implementation_id,
                    "implementation_paths": ["src"],
                    "repository": str(self.repository),
                    "review": {
                        "decision": "approved",
                        "sha256": hashlib.sha256(("review:" + candidate).encode()).hexdigest(),
                    },
                    "source_checkpoint": checkpoint,
                    "target_branch": target_branch,
                    "verification": {
                        "commands": ["python -m unittest"],
                        "passed": True,
                        "sha256": hashlib.sha256(("tests:" + candidate).encode()).hexdigest(),
                    },
                    "worktree_path": str(worktree),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        review = PROTOCOL.record_candidate_review(review_input)
        acceptance_input = self.root / f"accept-{implementation_id}.json"
        acceptance_input.write_text(
            json.dumps(
                {
                    "acceptance": {
                        "accepted": True,
                        "sha256": hashlib.sha256(("accept:" + candidate).encode()).hexdigest(),
                    },
                    "publication": {
                        key: review[key]
                        for key in ("file_bytes", "file_sha256", "path", "review_id", "version")
                    },
                    "repository": str(self.repository),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        accepted = PROTOCOL.accept_implementation_candidate(acceptance_input)
        return active_claim, accepted, candidate, target_branch, worktree

    @matrix_proof("invariants:protected-active-source")
    def test_claim_reservation_requires_active_exact_source_protection(self) -> None:
        (self.repository / "base.txt").write_text("base\n", encoding="utf-8")
        base = self.commit_repository("base")
        worktree = self.root / "unprotected-worktree"
        claim_input = self.write_worktree_claim_input(
            "unprotected-claim.json",
            base=base,
            implementation_id="implementation-unprotected",
            branch="codex/unprotected",
            worktree=worktree,
        )
        report = PROTOCOL.inspect_implementation_sources(self.repository)
        release_input = self.root / "release-unprotected-source.json"
        release_input.write_text(
            json.dumps(
                {
                    "expected_version": report["version"],
                    "implementation_id": "implementation-unprotected",
                    "repository": str(self.repository),
                    "terminal_state": "cancelled",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        PROTOCOL.release_implementation_source(release_input)
        with self.assertRaises(PROTOCOL.ProtocolError) as rejected:
            PROTOCOL.reserve_worktree_execution_claim(claim_input)
        self.assertEqual(rejected.exception.code, "implementation_source_cas_mismatch")
        self.assertFalse(worktree.exists())
        self.assertIsNone(PROTOCOL._git_branch_oid(self.repository, "codex/unprotected"))

    @matrix_proof(
        "scenarios:serial-worktree-claim",
        "invariants:nonexpiring-cas-ownership",
    )
    def test_durable_worktree_claim_is_reserved_before_creation_and_has_no_ttl(self) -> None:
        (self.repository / "base.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repository), "add", "base.txt"], check=True)
        subprocess.run(
            [
                "git", "-C", str(self.repository), "-c", "user.name=Test",
                "-c", "user.email=test@example.com", "commit", "-qm", "base",
            ],
            check=True,
        )
        base = subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        worktree = self.root / "claimed-worktree"
        claim_input = self.write_worktree_claim_input(
            "claim.json",
            base=base,
            implementation_id="implementation-claim",
            branch="codex/claim",
            worktree=worktree,
        )
        claim = PROTOCOL.reserve_worktree_execution_claim(claim_input)
        self.assertTrue(claim["created"])
        self.assertEqual(claim["state"], "reserved")
        self.assertFalse(worktree.exists())
        self.assertIsNone(
            PROTOCOL._git_branch_oid(self.repository, "codex/claim")
        )
        claim_document = json.loads(Path(claim["path"]).read_text(encoding="utf-8"))
        self.assertNotIn("holder", claim_document)
        self.assertNotIn("expires_at_epoch", json.dumps(claim_document))
        self.assertNotIn("ttl", json.dumps(claim_document))
        self.assertNotIn("release-worktree-execution-claim", PROTOCOL.COMMAND_REGISTRY.names)

        replay = PROTOCOL.reserve_worktree_execution_claim(claim_input)
        self.assertFalse(replay["created"])
        self.assertEqual(replay["claim_id"], claim["claim_id"])
        self.assertEqual(replay["version"], claim["version"])

        conflicting = self.write_worktree_claim_input(
            "conflicting-claim.json",
            base=base,
            implementation_id="implementation-other",
            branch="codex/other",
            worktree=worktree,
        )
        before = Path(claim["path"]).read_bytes()
        with self.assertRaises(PROTOCOL.ProtocolError) as raised:
            PROTOCOL.reserve_worktree_execution_claim(conflicting)
        self.assertEqual(raised.exception.code, "worktree_claim_conflict")
        self.assertEqual(Path(claim["path"]).read_bytes(), before)

        provision_input = self.write_claim_operation_input(
            "provision.json", claim
        )
        active = PROTOCOL.provision_claimed_worktree(provision_input)
        self.assertEqual(active["state"], "active")
        self.assertEqual(active["classification"], "exact-adoption")
        self.assertTrue(worktree.is_dir())
        verified = PROTOCOL.verify_worktree_execution_claim(
            Path(active["path"]),
            expected_id=active["claim_id"],
            expected_version=active["version"],
            expected_bytes=active["file_bytes"],
            expected_sha256=active["file_sha256"],
            platform_cwd=worktree,
        )
        self.assertTrue(verified["verified"])
        self.assertEqual(
            PROTOCOL.inspect_repository_coordination_lease(self.repository)["state"],
            "available",
        )

        admin_input = self.root / "admin-release-claim.json"
        admin_input.write_text(
            json.dumps(
                {
                    "action": "release-abandoned-claim",
                    "claim": {
                        key: active[key]
                        for key in (
                            "claim_id",
                            "file_bytes",
                            "file_sha256",
                            "path",
                            "version",
                        )
                    },
                    "terminal_state": "cancelled",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        released = PROTOCOL.administratively_release_worktree_execution_claim(
            admin_input
        )
        self.assertEqual(released["state"], "released")

    @matrix_proof("fault_boundaries:worktree-provisioning")
    def test_worktree_claim_reconciles_branch_and_worktree_failure_boundaries(self) -> None:
        (self.repository / "base.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repository), "add", "base.txt"], check=True)
        subprocess.run(
            [
                "git", "-C", str(self.repository), "-c", "user.name=Test",
                "-c", "user.email=test@example.com", "commit", "-qm", "base",
            ],
            check=True,
        )
        base = subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()

        branch_worktree = self.root / "claim-after-branch"
        branch_input = self.write_worktree_claim_input(
            "claim-after-branch-input.json",
            base=base,
            implementation_id="implementation-after-branch",
            branch="codex/after-branch",
            worktree=branch_worktree,
        )
        branch_claim = PROTOCOL.reserve_worktree_execution_claim(branch_input)
        branch_provision = self.write_claim_operation_input(
            "provision-after-branch.json", branch_claim
        )
        with mock.patch.dict(
            os.environ,
            {"CODEX_SUPERVISION_TEST_FAILPOINT": "claim-after-branch"},
        ):
            with self.assertRaises(PROTOCOL.ProtocolError) as raised:
                PROTOCOL.provision_claimed_worktree(branch_provision)
        self.assertEqual(raised.exception.code, "outcome_unknown")
        reconciled = PROTOCOL.reconcile_worktree_provisioning(branch_provision)
        self.assertEqual(reconciled["classification"], "safe-retry")
        self.assertEqual(
            reconciled["observation"]["retry_action"], "reuse-exact-branch"
        )
        active = PROTOCOL.provision_claimed_worktree(branch_provision)
        self.assertEqual(active["state"], "active")

        adoption_worktree = self.root / "claim-after-worktree"
        adoption_input = self.write_worktree_claim_input(
            "claim-after-worktree-input.json",
            base=base,
            implementation_id="implementation-after-worktree",
            branch="codex/after-worktree",
            worktree=adoption_worktree,
        )
        adoption_claim = PROTOCOL.reserve_worktree_execution_claim(adoption_input)
        adoption_provision = self.write_claim_operation_input(
            "provision-after-worktree.json", adoption_claim
        )
        with mock.patch.dict(
            os.environ,
            {"CODEX_SUPERVISION_TEST_FAILPOINT": "claim-after-worktree"},
        ):
            with self.assertRaises(PROTOCOL.ProtocolError) as raised:
                PROTOCOL.provision_claimed_worktree(adoption_provision)
        self.assertEqual(raised.exception.code, "outcome_unknown")
        adopted = PROTOCOL.reconcile_worktree_provisioning(adoption_provision)
        self.assertEqual(adopted["classification"], "exact-adoption")
        self.assertEqual(adopted["state"], "active")

    @matrix_proof("fault_boundaries:worktree-provisioning")
    def test_worktree_claim_reconciliation_stops_on_unregistered_filesystem_state(self) -> None:
        (self.repository / "base.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repository), "add", "base.txt"], check=True)
        subprocess.run(
            [
                "git", "-C", str(self.repository), "-c", "user.name=Test",
                "-c", "user.email=test@example.com", "commit", "-qm", "base",
            ],
            check=True,
        )
        base = subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        worktree = self.root / "ambiguous-worktree"
        claim_input = self.write_worktree_claim_input(
            "ambiguous-claim.json",
            base=base,
            implementation_id="implementation-ambiguous",
            branch="codex/ambiguous",
            worktree=worktree,
        )
        claim = PROTOCOL.reserve_worktree_execution_claim(claim_input)
        worktree.mkdir()
        reconcile_input = self.write_claim_operation_input(
            "ambiguous-reconcile.json", claim
        )
        with self.assertRaises(PROTOCOL.ProtocolError) as raised:
            PROTOCOL.reconcile_worktree_provisioning(reconcile_input)
        self.assertEqual(raised.exception.code, "worktree_provisioning_ambiguous")

    @matrix_proof(
        "scenarios:advanced-target-publication",
        "fault_boundaries:publication",
    )
    def test_accepted_candidate_publishes_against_advanced_exact_target_head(self) -> None:
        (self.repository / "docs").mkdir()
        (self.repository / "docs" / "plan.md").write_text("frozen plan\n", encoding="utf-8")
        (self.repository / "src").mkdir()
        (self.repository / "src" / "base.py").write_text("BASE = True\n", encoding="utf-8")
        base = self.commit_repository("base")
        _, accepted, candidate, target_branch, _ = self.prepare_accepted_candidate(
            implementation_id="implementation-publication",
            relative_path="src/candidate.py",
            candidate_content="CANDIDATE = True\n",
        )

        (self.repository / "src" / "advanced.py").write_text("ADVANCED = True\n", encoding="utf-8")
        advanced = self.commit_repository("advance target independently")
        self.assertNotEqual(advanced, base)
        publication_input = self.root / "publish-advanced.json"
        publication_input.write_text(
            json.dumps(
                {
                    "expected_target_head": advanced,
                    "publication": {
                        key: accepted[key]
                        for key in ("file_bytes", "file_sha256", "path", "review_id", "version")
                    },
                    "repository": str(self.repository),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        integrated = PROTOCOL.publish_accepted_candidate(publication_input)
        self.assertTrue(integrated["integrated"])
        self.assertEqual(integrated["integration"]["parents"], [advanced, candidate])
        self.assertTrue((self.repository / "src" / "candidate.py").is_file())
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(self.repository), "status", "--porcelain"],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout,
            "",
        )
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(self.repository), "branch", "--show-current"],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout.strip(),
            target_branch,
        )

    @matrix_proof("scenarios:parallel-worktrees-serial-publication")
    def test_two_independent_worktrees_publish_serially_against_evolving_target(self) -> None:
        (self.repository / "docs").mkdir()
        (self.repository / "docs" / "plan.md").write_text("plan\n", encoding="utf-8")
        (self.repository / "src").mkdir()
        (self.repository / "src" / "base.py").write_text("BASE = True\n", encoding="utf-8")
        base = self.commit_repository("base")
        first_claim, first, _, _, first_worktree = self.prepare_accepted_candidate(
            implementation_id="implementation-parallel-a",
            relative_path="src/a.py",
            candidate_content="A = True\n",
        )
        second_claim, second, _, _, second_worktree = self.prepare_accepted_candidate(
            implementation_id="implementation-parallel-b",
            relative_path="src/b.py",
            candidate_content="B = True\n",
        )
        self.assertTrue(first_worktree.is_dir())
        self.assertTrue(second_worktree.is_dir())
        self.assertEqual(first_claim["state"], "active")
        self.assertEqual(second_claim["state"], "active")

        def publish(name: str, publication: dict[str, object], expected: str) -> dict[str, object]:
            input_path = self.root / f"publish-{name}.json"
            input_path.write_text(
                json.dumps(
                    {
                        "expected_target_head": expected,
                        "publication": {
                            key: publication[key]
                            for key in (
                                "file_bytes",
                                "file_sha256",
                                "path",
                                "review_id",
                                "version",
                            )
                        },
                        "repository": str(self.repository),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            return PROTOCOL.publish_accepted_candidate(input_path)

        first_result = publish("a", first, base)
        second_result = publish("b", second, first_result["integration"]["merge_commit"])
        self.assertTrue(first_result["integrated"])
        self.assertTrue(second_result["integrated"])
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(self.repository), "show", "HEAD:src/a.py"],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout,
            "A = True\n",
        )
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(self.repository), "show", "HEAD:src/b.py"],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout,
            "B = True\n",
        )

    @matrix_proof(
        "scenarios:typed-conflict-repair",
        "fault_boundaries:publication",
    )
    def test_publication_conflicts_restore_checkout_and_use_three_typed_classes(self) -> None:
        (self.repository / "docs").mkdir()
        (self.repository / "docs" / "plan.md").write_text("frozen plan\n", encoding="utf-8")
        (self.repository / "src").mkdir()
        self.commit_repository("base documents")
        cases = (
            ("textual", "textual-structural", True, "repair-required"),
            ("local", "local-semantic-adaptation", True, "repair-required"),
            ("material", "material-semantic-conflict", False, "paused"),
        )
        for suffix, classification, preserves, expected_state in cases:
            relative = f"src/conflict_{suffix}.py"
            (self.repository / relative).write_text("VALUE = 'base'\n", encoding="utf-8")
            self.commit_repository(f"base {suffix}")
            _, accepted, _, target_branch, _ = self.prepare_accepted_candidate(
                implementation_id=f"implementation-conflict-{suffix}",
                relative_path=relative,
                candidate_content="VALUE = 'candidate'\n",
            )
            (self.repository / relative).write_text("VALUE = 'target'\n", encoding="utf-8")
            target_head = self.commit_repository(f"target {suffix}")
            publication_input = self.root / f"publish-conflict-{suffix}.json"
            publication_input.write_text(
                json.dumps(
                    {
                        "expected_target_head": target_head,
                        "publication": {
                            key: accepted[key]
                            for key in ("file_bytes", "file_sha256", "path", "review_id", "version")
                        },
                        "repository": str(self.repository),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(PROTOCOL.ProtocolError) as raised:
                PROTOCOL.publish_accepted_candidate(publication_input)
            self.assertEqual(raised.exception.code, "publication_conflict")
            conflict = raised.exception.context["current"]
            self.assertEqual(
                subprocess.run(
                    ["git", "-C", str(self.repository), "rev-parse", "HEAD"],
                    check=True,
                    stdout=subprocess.PIPE,
                    text=True,
                ).stdout.strip(),
                target_head,
            )
            self.assertEqual(
                subprocess.run(
                    ["git", "-C", str(self.repository), "status", "--porcelain"],
                    check=True,
                    stdout=subprocess.PIPE,
                    text=True,
                ).stdout,
                "",
            )
            self.assertEqual(
                subprocess.run(
                    ["git", "-C", str(self.repository), "branch", "--show-current"],
                    check=True,
                    stdout=subprocess.PIPE,
                    text=True,
                ).stdout.strip(),
                target_branch,
            )
            classification_input = self.root / f"classify-{suffix}.json"
            classification_input.write_text(
                json.dumps(
                    {
                        "classification": classification,
                        "preserves_acceptance": preserves,
                        "preserves_business_behavior": preserves,
                        "preserves_scope": preserves,
                        "publication": {
                            key: conflict[key]
                            for key in ("file_bytes", "file_sha256", "path", "review_id", "version")
                        },
                        "repository": str(self.repository),
                        "summary": f"classified as {classification}",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            classified = PROTOCOL.classify_publication_conflict(classification_input)
            self.assertEqual(classified["state"], expected_state)
            self.assertEqual(classified["user_decision_required"], expected_state == "paused")

    @matrix_proof(
        "scenarios:archive-cleanup",
        "fault_boundaries:closure",
        "invariants:no-force-cleanup",
    )
    def test_outcome_convergence_and_checkpoint_owned_cleanup_are_recoverable(self) -> None:
        for name, path in (
            ("IMPLEMENTATION_OUTCOME_ROOT", self.root / "outcomes"),
            ("DOCUMENT_CONVERGENCE_ROOT", self.root / "convergences"),
            ("WORKTREE_CLOSURE_ROOT", self.root / "closures"),
        ):
            previous = getattr(PROTOCOL, name)
            setattr(PROTOCOL, name, path)
            self.addCleanup(setattr, PROTOCOL, name, previous)
        (self.repository / "docs").mkdir()
        (self.repository / "docs" / "plan.md").write_text("frozen plan\n", encoding="utf-8")
        (self.repository / "src").mkdir()
        (self.repository / "src" / "base.py").write_text("BASE = True\n", encoding="utf-8")
        self.commit_repository("base")
        claim, accepted, _, target_branch, worktree = self.prepare_accepted_candidate(
            implementation_id="implementation-closure",
            relative_path="src/closure.py",
            candidate_content="CLOSURE = True\n",
        )
        target_before = subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        publish_input = self.root / "publish-closure.json"
        publish_input.write_text(
            json.dumps(
                {
                    "expected_target_head": target_before,
                    "publication": {
                        key: accepted[key]
                        for key in ("file_bytes", "file_sha256", "path", "review_id", "version")
                    },
                    "repository": str(self.repository),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        integrated = PROTOCOL.publish_accepted_candidate(publish_input)

        proposed_bytes = b"frozen plan\n\nImplementation result: complete.\n"
        proposal_input = self.root / "outcome-proposal.json"
        proposal_input.write_text(
            json.dumps(
                {
                    "after_utf8_b64": __import__("base64").b64encode(proposed_bytes).decode("ascii"),
                    "change_kind": "implementation-outcome",
                    "implementation_id": "implementation-closure",
                    "repository": str(self.repository),
                    "source_checkpoint": claim["binding"]["source_checkpoint"],
                    "target_path": "docs/plan.md",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        proposal = PROTOCOL.create_implementation_outcome_proposal(proposal_input)
        self.assertTrue(Path(proposal["path"]).is_file())
        self.assertEqual((worktree / "docs" / "plan.md").read_bytes(), b"frozen plan\n")
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(worktree), "status", "--porcelain"],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout,
            "",
        )

        lease = PROTOCOL.acquire_document_lease(
            self.write_input(
                "closure-document-lease.json",
                task_id="closure-task",
                stage="change-closure",
                paths=["docs/plan.md"],
                implementation_id="implementation-closure",
            ),
            wait_seconds=0,
            max_retries=0,
        )
        convergence_input = self.root / "converge-outcomes.json"
        convergence_input.write_text(
            json.dumps(
                {
                    "document_lease": {
                        "lease_id": lease["holder"]["lease_id"],
                        "path": lease["path"],
                        "version": lease["version"],
                    },
                    "paths": ["docs/plan.md"],
                    "repository": str(self.repository),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        convergence = PROTOCOL.converge_implementation_outcomes(convergence_input)
        self.assertEqual((self.repository / "docs" / "plan.md").read_bytes(), proposed_bytes)
        documentation_input = self.root / "commit-documents.json"
        documentation_input.write_text(
            json.dumps(
                {
                    "convergence": {
                        key: convergence[key]
                        for key in ("file_bytes", "file_sha256", "id", "path", "version")
                    },
                    "expected_target_head": integrated["integration"]["merge_commit"],
                    "repository": str(self.repository),
                    "target_branch": target_branch,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        committed = PROTOCOL.commit_converged_documents(documentation_input)
        self.assertEqual(committed["state"], "committed")
        PROTOCOL.release_document_lease(
            Path(lease["path"]),
            expected_id=lease["holder"]["lease_id"],
            expected_version=lease["version"],
        )

        closure_input = self.root / "worktree-closure.json"
        closure_input.write_text(
            json.dumps(
                {
                    "convergence": {
                        key: committed[key]
                        for key in ("file_bytes", "file_sha256", "id", "path", "version")
                    },
                    "execution_claim": {
                        key: claim[key]
                        for key in ("claim_id", "file_bytes", "file_sha256", "path", "version")
                    },
                    "publication": {
                        key: integrated[key]
                        for key in ("file_bytes", "file_sha256", "path", "review_id", "version")
                    },
                    "repository": str(self.repository),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        closure = PROTOCOL.create_worktree_closure_checkpoint(closure_input)
        self.assertEqual(closure["phase"], "documents-committed")
        checkpoint = Path(closure["path"])

        with mock.patch.dict(
            os.environ,
            {"CODEX_SUPERVISION_TEST_FAILPOINT": "closure-after-worktree-removed-effect"},
        ):
            with self.assertRaises(PROTOCOL.ProtocolError) as raised:
                PROTOCOL.advance_worktree_closure_checkpoint(checkpoint)
        self.assertEqual(raised.exception.code, "outcome_unknown")
        closure = PROTOCOL.advance_worktree_closure_checkpoint(checkpoint)
        self.assertEqual(closure["phase"], "worktree-removed")
        self.assertTrue(closure["receipts"][-1]["result"]["adopted_absence"])

        with mock.patch.dict(
            os.environ,
            {"CODEX_SUPERVISION_TEST_FAILPOINT": "closure-after-branch-removed-effect"},
        ):
            with self.assertRaises(PROTOCOL.ProtocolError):
                PROTOCOL.advance_worktree_closure_checkpoint(checkpoint)
        closure = PROTOCOL.advance_worktree_closure_checkpoint(checkpoint)
        self.assertEqual(closure["phase"], "branch-removed")
        self.assertTrue(closure["receipts"][-1]["result"]["adopted_absence"])

        with mock.patch.dict(
            os.environ,
            {"CODEX_SUPERVISION_TEST_FAILPOINT": "closure-after-execution-claim-released-effect"},
        ):
            with self.assertRaises(PROTOCOL.ProtocolError):
                PROTOCOL.advance_worktree_closure_checkpoint(checkpoint)
        closure = PROTOCOL.advance_worktree_closure_checkpoint(checkpoint)
        self.assertEqual(closure["phase"], "execution-claim-released")
        self.assertTrue(closure["receipts"][-1]["result"]["adopted_release"])
        released_claim, _ = PROTOCOL._read_worktree_execution_claim(Path(claim["path"]))
        self.assertEqual(released_claim["state"], "released")
        self.assertEqual(released_claim["provisioning"]["terminal_state"], "archived")

        closure = PROTOCOL.advance_worktree_closure_checkpoint(checkpoint)
        self.assertEqual(closure["phase"], "archived")
        replay = PROTOCOL.advance_worktree_closure_checkpoint(checkpoint)
        self.assertTrue(replay["idempotent"])

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

    def test_repository_coordination_lease_is_separate_and_cas_protected(self) -> None:
        input_path = self.write_repository_coordination_input(
            "repository-coordination.json"
        )
        with mock.patch.object(PROTOCOL, "_now_epoch", return_value=1_000):
            acquired = PROTOCOL.acquire_repository_coordination_lease(input_path)
        self.assertTrue(acquired["acquired"])
        self.assertEqual(acquired["state"], "held")
        self.assertEqual(
            Path(acquired["path"]),
            self.repository / ".git" / PROTOCOL.REPOSITORY_COORDINATION_LEASE_FILENAME,
        )
        self.assertFalse(
            (self.repository / ".git" / PROTOCOL.DOCUMENT_LEASE_FILENAME).exists()
        )
        with mock.patch.object(PROTOCOL, "_now_epoch", return_value=1_010):
            verified = PROTOCOL.verify_repository_coordination_lease(
                Path(acquired["path"]),
                expected_id=acquired["holder"]["lease_id"],
                expected_version=acquired["version"],
            )
            self.assertTrue(verified["verified"])
            released = PROTOCOL.release_repository_coordination_lease(
                Path(acquired["path"]),
                expected_id=acquired["holder"]["lease_id"],
                expected_version=acquired["version"],
            )
        self.assertTrue(released["released"])
        self.assertEqual(released["state"], "available")
        with self.assertRaises(PROTOCOL.ProtocolError):
            PROTOCOL.release_repository_coordination_lease(
                Path(acquired["path"]),
                expected_id=acquired["holder"]["lease_id"],
                expected_version=acquired["version"],
            )

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

    @matrix_proof("invariants:protected-active-source")
    def test_active_source_protection_blocks_document_writes_until_final_dependency(self) -> None:
        plan = self.repository / "docs" / "plan.md"
        plan.parent.mkdir()
        plan.write_text("frozen plan\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(self.repository), "add", "docs/plan.md"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.repository),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-qm",
                "freeze plan",
            ],
            check=True,
        )
        commit_id = subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        blob_id = subprocess.run(
            [
                "git",
                "-C",
                str(self.repository),
                "rev-parse",
                "HEAD:docs/plan.md",
            ],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        checkpoint = {
            "checkpoint_id": "CP-source",
            "commit_id": commit_id,
            "committed": True,
            "read_only": True,
            "sha256": "a" * 64,
        }
        artifact = {
            "blob_id": blob_id,
            "path": "docs/plan.md",
            "sha256": hashlib.sha256(b"frozen plan\n").hexdigest(),
        }

        def protect(implementation_id: str) -> dict[str, object]:
            input_path = self.root / f"protect-{implementation_id}.json"
            input_path.write_text(
                json.dumps(
                    {
                        "artifacts": [artifact],
                        "implementation_id": implementation_id,
                        "repository": str(self.repository),
                        "source_checkpoint": checkpoint,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            return PROTOCOL.protect_implementation_source(input_path)

        first = protect("implementation-1")
        replay = protect("implementation-1")
        second = protect("implementation-2")
        self.assertTrue(first["protected"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(second["active_implementation_ids"], ["implementation-1", "implementation-2"])

        verify_input = self.root / "verify-source.json"
        verify_input.write_text(
            json.dumps(
                {
                    "implementation_id": "implementation-1",
                    "repository": str(self.repository),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        self.assertTrue(PROTOCOL.verify_implementation_source(verify_input)["verified"])

        protected_write = self.write_input(
            "protected-write.json",
            task_id="writer",
            paths=["docs/plan.md"],
        )
        with self.assertRaises(PROTOCOL.ProtocolError) as blocked:
            PROTOCOL.acquire_document_lease(
                protected_write, wait_seconds=0, max_retries=0
            )
        self.assertEqual(blocked.exception.code, "implementation_source_protected")

        shared_closure = self.write_input(
            "shared-closure.json",
            task_id="closure",
            stage="change-closure",
            paths=["docs/plan.md"],
            implementation_id="implementation-1",
        )
        with self.assertRaises(PROTOCOL.ProtocolError):
            PROTOCOL.acquire_document_lease(
                shared_closure, wait_seconds=0, max_retries=0
            )

        successor_write = self.write_input(
            "successor-write.json",
            task_id="writer",
            paths=["docs/successor.md"],
        )
        successor = PROTOCOL.acquire_document_lease(
            successor_write, wait_seconds=0, max_retries=0
        )
        PROTOCOL.release_document_lease(
            Path(successor["path"]),
            expected_id=successor["holder"]["lease_id"],
            expected_version=successor["version"],
        )

        release_second = self.root / "release-second-source.json"
        release_second.write_text(
            json.dumps(
                {
                    "expected_version": second["version"],
                    "implementation_id": "implementation-2",
                    "repository": str(self.repository),
                    "terminal_state": "cancelled",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        released_second = PROTOCOL.release_implementation_source(release_second)
        self.assertEqual(released_second["active_implementation_ids"], ["implementation-1"])

        final_closure = PROTOCOL.acquire_document_lease(
            shared_closure, wait_seconds=0, max_retries=0
        )
        self.assertEqual(
            final_closure["holder"]["implementation_id"], "implementation-1"
        )
        self.assertEqual(final_closure["holder"]["paths"], ["docs/plan.md"])
        PROTOCOL.release_document_lease(
            Path(final_closure["path"]),
            expected_id=final_closure["holder"]["lease_id"],
            expected_version=final_closure["version"],
        )

        plan.write_text("externally changed\n", encoding="utf-8")
        with self.assertRaises(PROTOCOL.ProtocolError) as drift:
            PROTOCOL.verify_implementation_source(verify_input)
        self.assertEqual(drift.exception.code, "implementation_source_drift")

    def test_document_write_requires_exact_paths(self) -> None:
        input_path = self.write_input(
            "missing-document-paths.json", task_id="writer", paths=[]
        )
        with self.assertRaises(PROTOCOL.ProtocolError) as raised:
            PROTOCOL.acquire_document_lease(
                input_path, wait_seconds=0, max_retries=0
            )
        self.assertEqual(raised.exception.code, "implementation_source_protected")

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

    @matrix_proof("fault_boundaries:lease")
    def test_lease_conflict_fault_recovers_only_after_exact_owner_release(self) -> None:
        first_input = self.write_input("lease-boundary-first.json", task_id="task-a")
        second_input = self.write_input("lease-boundary-second.json", task_id="task-b")
        first = PROTOCOL.acquire_document_lease(
            first_input, wait_seconds=0, max_retries=0
        )
        blocked = PROTOCOL.acquire_document_lease(
            second_input, wait_seconds=0, max_retries=0
        )
        self.assertEqual(blocked["state"], "timeout")
        self.assertFalse(blocked["acquired"])
        with self.assertRaises(PROTOCOL.ProtocolError):
            PROTOCOL.release_document_lease(
                Path(first["path"]),
                expected_id="0" * 32,
                expected_version=first["version"],
            )
        still_held = PROTOCOL.inspect_document_lease(self.repository)
        self.assertEqual(still_held["holder"]["lease_id"], first["holder"]["lease_id"])
        PROTOCOL.release_document_lease(
            Path(first["path"]),
            expected_id=first["holder"]["lease_id"],
            expected_version=first["version"],
        )
        recovered = PROTOCOL.acquire_document_lease(
            second_input, wait_seconds=0, max_retries=0
        )
        self.assertTrue(recovered["acquired"])
        self.assertEqual(recovered["holder"]["owner_task_id"], "task-b")

if __name__ == "__main__":
    unittest.main()
