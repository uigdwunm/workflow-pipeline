"""Discussion checkpoint publication, recovery, repair, and garbage collection."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid
from typing import Any

from .state import (
    ProtocolError,
    _active_pending_write,
    _atomic_replace,
    _canonical_json,
    _coordination_root,
    _evolution_paths,
    _expect_keys,
    _expect_string,
    _flock_with_timeout,
    _idempotent_result,
    _inject_failure,
    _json_field,
    _load_records,
    _mutation_fingerprint,
    _record_by_id,
    _require_regular_nosymlink,
    _sha256,
    _topic_snapshot,
    _validate_revisions,
    _validate_uuid4,
    _verify_topic_owner,
    _write_ledger_transaction,
)


CHECKPOINT_PURPOSES = {"pause", "handoff", "split", "stage-entry", "implementation-source"}
CHECKPOINT_ACTIVE_STATES = {"prepared", "outcome-unknown"}
CHECKPOINT_GC_ACTIVE_STATES = {"outcome-unknown"}


def _checkpoint_id(idempotency_key: str) -> str:
    return f"CP-{uuid.UUID(idempotency_key).hex}"


def _checkpoint_gc_id(idempotency_key: str) -> str:
    return f"GC-{uuid.UUID(idempotency_key).hex}"


def _checkpoint_record(
    records: dict[str, list[dict[str, Any]]], checkpoint_id: str
) -> dict[str, Any]:
    return _record_by_id(records["Checkpoints"], "checkpoint_id", checkpoint_id, "checkpoint_id")


def _checkpoint_data(record: dict[str, Any]) -> dict[str, Any]:
    return _json_field(record, "data_json", "checkpoint")


def _store_checkpoint(record: dict[str, Any], checkpoint: dict[str, Any]) -> None:
    record["state"] = checkpoint["state"]
    record["record_revision"] = checkpoint["record_revision"]
    record["data_json"] = _canonical_json(checkpoint)


def _active_checkpoint(records: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    active = [
        record
        for record in records["Checkpoints"]
        if record.get("state") in CHECKPOINT_ACTIVE_STATES
    ]
    if len(active) > 1:
        raise ProtocolError("state_corrupt", "more than one checkpoint intent is active")
    return active[0] if active else None


def _checkpoint_gc_data(record: dict[str, Any]) -> dict[str, Any]:
    return _json_field(record, "data_json", "checkpoint GC")


def _checkpoint_gc_record(
    records: dict[str, list[dict[str, Any]]], gc_operation_id: str
) -> dict[str, Any]:
    return _record_by_id(
        records["Phase Results"],
        "result_id",
        gc_operation_id,
        "gc_operation_id",
    )


def _store_checkpoint_gc(record: dict[str, Any], operation: dict[str, Any]) -> None:
    record["state"] = operation["state"]
    record["record_revision"] = operation["record_revision"]
    record["data_json"] = _canonical_json(operation)


def _active_checkpoint_gc(
    records: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    active = [
        record
        for record in records["Phase Results"]
        if record.get("result_kind") == "checkpoint-gc"
        and record.get("state") in CHECKPOINT_GC_ACTIVE_STATES
    ]
    if len(active) > 1:
        raise ProtocolError("state_corrupt", "more than one checkpoint GC is active")
    return active[0] if active else None


def _checkpoint_decision_digest(records: dict[str, list[dict[str, Any]]], topic_id: str) -> str:
    decisions = _topic_snapshot(records, topic_id)["decisions"]
    normalized = [
        {
            "decision_id": item["decision_id"],
            "evolution": item.get("evolution"),
            "rationale": item.get("rationale"),
            "state": item.get("state"),
            "summary": item["summary"],
        }
        for item in decisions
    ]
    return _sha256(_canonical_json(normalized).encode("utf-8"))


def _checkpoint_decision_authority(
    records: dict[str, list[dict[str, Any]]], topic_id: str
) -> tuple[dict[str, str], list[str]]:
    decisions = [
        _json_field(record, "data_json", "decision")
        for record in records["Pending Items"]
        if record.get("topic_id") == topic_id and record.get("item_kind") == "decision"
    ]
    digests = {
        item["decision_id"]: _sha256(_canonical_json(item).encode("utf-8"))
        for item in sorted(decisions, key=lambda item: item["decision_id"])
    }
    confirmed = sorted(
        item["decision_id"] for item in decisions if item.get("state") == "confirmed"
    )
    return digests, confirmed


def _checkpoint_authority_context(
    request: dict[str, Any], ledger_revision: int, checkpoint: dict[str, Any]
) -> dict[str, Any]:
    return {
        "state": checkpoint["state"],
        "ledger_revision": ledger_revision,
        "record_revision": checkpoint["record_revision"],
        "project_id": request["project_id"],
        "tree_id": request["tree_id"],
        "topic_id": request["actor_topic_id"],
        "checkpoint_id": checkpoint["checkpoint_id"],
    }


def _project_relative_path(project: Path, path: Path, label: str) -> str:
    try:
        relative = path.relative_to(project)
    except ValueError as error:
        raise ProtocolError("invalid_request", f"{label} must be inside the project") from error
    text = relative.as_posix()
    if not text or text.startswith("../") or text == ".git" or text.startswith(".git/"):
        raise ProtocolError("invalid_request", f"{label} is not a safe project-relative path")
    return text


def _git(project: Path, arguments: list[str], *, input_bytes: bytes | None = None) -> bytes:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=project,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise ProtocolError(
            "checkpoint_history_mismatch",
            f"Git command failed: git {' '.join(arguments)}",
            cause=completed.stderr.decode("utf-8", errors="replace").strip(),
        )
    return completed.stdout


def _git_object_type(project: Path, object_id: str) -> str | None:
    completed = subprocess.run(
        ["git", "cat-file", "-t", object_id],
        cwd=project,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _verify_git_base(project: Path, base_ref: str) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "--verify", f"{base_ref}^{{commit}}"],
        cwd=project,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40,64}", completed.stdout.strip()):
        raise ProtocolError("checkpoint_base_invalid", "checkpoint base_ref must resolve to a commit")
    return completed.stdout.strip()


def _tree_entries(project: Path, tree_id: str) -> dict[str, tuple[str, str]]:
    output = _git(project, ["ls-tree", "-rz", tree_id])
    entries: dict[str, tuple[str, str]] = {}
    for raw in output.split(b"\0"):
        if not raw:
            continue
        metadata, path_bytes = raw.split(b"\t", 1)
        mode, object_type, object_id = metadata.decode("ascii").split(" ")
        if object_type not in {"blob", "commit", "tree"}:
            raise ProtocolError("checkpoint_history_mismatch", "Git tree contains an unsupported object")
        entries[path_bytes.decode("utf-8")] = (mode, object_id)
    return entries


def _create_checkpoint_tree(
    project: Path, parent_commit: str, path_blobs: dict[str, str]
) -> str:
    parent_tree = _git(project, ["show", "-s", "--format=%T", parent_commit]).decode("ascii").strip()
    index_file = Path(tempfile.mkstemp(prefix="discussion-checkpoint-index-")[1])
    try:
        environment = dict(os.environ)
        environment["GIT_INDEX_FILE"] = str(index_file)
        index_file.unlink()
        read = subprocess.run(
            ["git", "read-tree", parent_tree],
            cwd=project,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if read.returncode != 0:
            raise ProtocolError("checkpoint_history_mismatch", "cannot seed checkpoint tree", cause=read.stderr.strip())
        index_input = b"".join(
            f"100644 {blob}\t{path}\0".encode("utf-8")
            for path, blob in sorted(path_blobs.items())
        )
        update = subprocess.run(
            ["git", "update-index", "-z", "--index-info"],
            cwd=project,
            env=environment,
            input=index_input,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if update.returncode != 0:
            raise ProtocolError(
                "checkpoint_history_mismatch",
                "cannot write checkpoint paths to temporary index",
                cause=update.stderr.decode("utf-8", errors="replace").strip(),
            )
        write_tree = subprocess.run(
            ["git", "write-tree"],
            cwd=project,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if write_tree.returncode != 0:
            raise ProtocolError("checkpoint_history_mismatch", "cannot write checkpoint tree", cause=write_tree.stderr.strip())
        return write_tree.stdout.strip()
    finally:
        try:
            index_file.unlink()
        except FileNotFoundError:
            pass


def _checkpoint_commit_message(checkpoint: dict[str, Any]) -> str:
    paths = json.loads(checkpoint["paths_json"])
    document_digests = json.loads(checkpoint["document_digests_json"])
    return (
        f"discussion checkpoint: {checkpoint['purpose']}\n\n"
        f"Codex-Discussion-Checkpoint: {checkpoint['checkpoint_id']}\n"
        f"Codex-Document-SHA256: {document_digests[paths[0]]}\n"
        f"Codex-Discussion-Decision-SHA256: {checkpoint['decision_digest']}\n"
        f"Codex-Discussion-Paths-SHA256: {checkpoint['path_set_digest']}\n"
    )


def _commit_metadata(project: Path, commit_id: str) -> dict[str, Any]:
    raw = _git(project, ["cat-file", "-p", commit_id]).decode("utf-8")
    header, message = raw.split("\n\n", 1)
    parents = [line.split(" ", 1)[1] for line in header.splitlines() if line.startswith("parent ")]
    trees = [line.split(" ", 1)[1] for line in header.splitlines() if line.startswith("tree ")]
    trailers: dict[str, str] = {}
    for line in message.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            if key.startswith("Codex-"):
                if key in trailers:
                    raise ProtocolError("checkpoint_history_mismatch", "checkpoint trailer is duplicated")
                trailers[key] = value
    return {"message": message, "parents": parents, "trailers": trailers, "tree": trees[0] if len(trees) == 1 else None}


def _commit_matches_checkpoint(
    project: Path,
    commit_id: str,
    checkpoint: dict[str, Any],
    *,
    expected_parent: str | None = None,
) -> dict[str, Any] | None:
    if _git_object_type(project, commit_id) != "commit":
        return None
    metadata = _commit_metadata(project, commit_id)
    parent_commit = expected_parent or checkpoint["base_commit"]
    if metadata["parents"] != [parent_commit]:
        return None
    paths = json.loads(checkpoint["paths_json"])
    blobs = json.loads(checkpoint["blob_ids_json"])
    digests = json.loads(checkpoint["document_digests_json"])
    expected_trailers = {
        "Codex-Discussion-Checkpoint": checkpoint["checkpoint_id"],
        "Codex-Document-SHA256": digests[paths[0]],
        "Codex-Discussion-Decision-SHA256": checkpoint["decision_digest"],
        "Codex-Discussion-Paths-SHA256": checkpoint["path_set_digest"],
    }
    if metadata["trailers"] != expected_trailers:
        return None
    parent_tree = _git(project, ["show", "-s", "--format=%T", parent_commit]).decode("ascii").strip()
    parent_entries = _tree_entries(project, parent_tree)
    expected_entries = dict(parent_entries)
    for path in paths:
        expected_entries[path] = ("100644", blobs[path])
    observed_entries = _tree_entries(project, metadata["tree"])
    if observed_entries != expected_entries:
        return None
    for path in paths:
        content = _git(project, ["cat-file", "blob", blobs[path]])
        if _sha256(content) != digests[path]:
            return None
    return {"commit_id": commit_id, "tree_id": metadata["tree"]}


def _all_checkpoint_candidates(
    project: Path,
    checkpoint: dict[str, Any],
    *,
    expected_parent: str | None = None,
) -> list[dict[str, Any]]:
    completed = subprocess.run(
        [
            "git", "cat-file", "--batch-all-objects",
            "--batch-check=%(objectname) %(objecttype)",
        ],
        cwd=project,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        raise ProtocolError("checkpoint_history_mismatch", "cannot enumerate Git history", cause=completed.stderr.strip())
    matches = []
    commit_ids = [
        line.split(" ", 1)[0]
        for line in completed.stdout.splitlines()
        if line.endswith(" commit")
    ]
    for commit_id in dict.fromkeys(commit_ids):
        match = _commit_matches_checkpoint(
            project, commit_id, checkpoint, expected_parent=expected_parent
        )
        if match is not None:
            matches.append(match)
    return matches


def _checkpoint_paths(request: dict[str, Any]) -> tuple[Path, Path, Path, Path, str, str]:
    project, ledger_path, topic_path, lock_path, owner_ref = _evolution_paths(request)
    _, storage_kind = _coordination_root(project)
    return project, ledger_path, topic_path, lock_path, owner_ref, storage_kind


def _normalize_checkpoint_source_path(
    project: Path, value: Any, label: str
) -> str:
    text = _expect_string(value, label, max_bytes=8_192)
    relative = Path(text)
    if (
        relative.is_absolute()
        or relative == Path(".")
        or ".." in relative.parts
        or relative.as_posix() != text
    ):
        raise ProtocolError(
            "invalid_request",
            f"{label} must be one normalized repository-relative path",
        )
    return _project_relative_path(project, project / relative, label)


def _prepare_checkpoint(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, topic_path, lock_path, owner_ref, storage_kind = _checkpoint_paths(request)
    expected_keys = {
        "protocol_version", "operation", "project_path", "project_id", "tree_id",
        "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
        "expected_topic_revision", "idempotency_key", "purpose", "base_ref",
    }
    if "source_paths" in request:
        expected_keys.add("source_paths")
    _expect_keys(
        request,
        expected_keys,
        "prepare-checkpoint request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    purpose = _expect_string(request["purpose"], "purpose", max_bytes=128)
    if purpose not in CHECKPOINT_PURPOSES:
        raise ProtocolError("invalid_request", "checkpoint purpose is unsupported")
    if "source_paths" in request and purpose != "implementation-source":
        raise ProtocolError(
            "invalid_request",
            "source_paths is valid only for an implementation-source checkpoint",
        )
    base_ref = _expect_string(request["base_ref"], "base_ref", max_bytes=512)
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        checkpoint_id = _checkpoint_id(request["idempotency_key"])
        existing_records = [
            record
            for record in records["Checkpoints"]
            if record.get("checkpoint_id") == checkpoint_id
        ]
        if len(existing_records) > 1:
            raise ProtocolError("state_corrupt", "checkpoint identity is duplicated")
        if existing_records:
            existing = _checkpoint_data(existing_records[0])
            fingerprint = _mutation_fingerprint(request)
            if (
                existing.get("creation_idempotency_key") != request["idempotency_key"]
                or existing.get("creation_fingerprint") != fingerprint
            ):
                raise ProtocolError(
                    "idempotency_conflict",
                    "checkpoint creation identity was reused with different frozen intent",
                )
            creation_result = json.loads(existing["creation_result_json"])
            creation_result["idempotent_replay"] = True
            creation_result["state"] = existing["state"]
            creation_result["checkpoint_record_revision"] = existing["record_revision"]
            creation_result["identity_reusable"] = False
            return creation_result
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        if _active_pending_write(records) is not None:
            raise ProtocolError("document_write_reconciliation_required", "document write must complete before checkpoint preparation")
        active_gc = _active_checkpoint_gc(records)
        if active_gc is not None:
            raise ProtocolError(
                "checkpoint_gc_reconciliation_required",
                "checkpoint GC must be reconciled before preparing another checkpoint",
                context={"gc_operation_id": active_gc["result_id"]},
            )
        active = _active_checkpoint(records)
        if active is not None:
            active_data = _checkpoint_data(active)
            code = (
                "checkpoint_reconciliation_required"
                if active_data["state"] == "outcome-unknown"
                else "checkpoint_identity_conflict"
            )
            raise ProtocolError(code, "an older checkpoint intent must be resolved first", context={"state": active_data["state"]})
        relative_path = _project_relative_path(project, topic_path, "topic document")
        requested_source_paths: list[str] = []
        if "source_paths" in request:
            if not isinstance(request["source_paths"], list):
                raise ProtocolError("invalid_request", "source_paths must be a list")
            requested_source_paths = [
                _normalize_checkpoint_source_path(
                    project,
                    item,
                    f"source_paths[{index}]",
                )
                for index, item in enumerate(request["source_paths"])
            ]
            if requested_source_paths != sorted(set(requested_source_paths)):
                raise ProtocolError(
                    "invalid_request", "source_paths must be sorted and unique"
                )
        paths = sorted(set([relative_path, *requested_source_paths]))
        documents = {
            path: _require_regular_nosymlink(
                project / path,
                "topic document" if path == relative_path else f"source artifact {path}",
            )
            for path in paths
        }
        decision_digest = _checkpoint_decision_digest(records, request["actor_topic_id"])
        decision_digests, confirmed_decision_ids = _checkpoint_decision_authority(
            records, request["actor_topic_id"]
        )
        digests = {path: _sha256(documents[path]) for path in paths}
        blob_ids: dict[str, str] = {}
        base_commit = None
        snapshot_digest = None
        snapshot_bytes_b64 = None
        if storage_kind == "git":
            base_commit = _verify_git_base(project, base_ref)
            blob_ids = {
                path: _git(
                    project,
                    ["hash-object", "-w", "--stdin"],
                    input_bytes=documents[path],
                )
                .decode("ascii")
                .strip()
                for path in paths
            }
        else:
            snapshot = {
                "decision_digest": decision_digest,
                "documents": {
                    path: base64.b64encode(documents[path]).decode("ascii")
                    for path in paths
                },
                "document_digests": digests,
                "path_set_digest": _sha256(_canonical_json(paths).encode("utf-8")),
                "paths": paths,
                "purpose": purpose,
            }
            snapshot_bytes = (_canonical_json(snapshot) + "\n").encode("utf-8")
            snapshot_digest = _sha256(snapshot_bytes)
            snapshot_bytes_b64 = base64.b64encode(snapshot_bytes).decode("ascii")
        next_revision = ledger_revision + 1
        stage_entry_phase = topic_record.get("current_phase") if purpose == "stage-entry" else None
        stage_entry_phase_result_id = None
        if purpose == "stage-entry":
            for phase_result_record in reversed(records["Phase Results"]):
                if (
                    phase_result_record.get("result_kind") != "phase-result"
                    or phase_result_record.get("state") != "completed"
                ):
                    continue
                phase_result = _json_field(
                    phase_result_record, "data_json", "phase result"
                )
                if phase_result.get("to_phase") != stage_entry_phase:
                    continue
                phase_run_record = _record_by_id(
                    records["Phase Runs"],
                    "run_id",
                    phase_result.get("phase_run_id"),
                    "phase_run_id",
                )
                phase_run = _json_field(
                    phase_run_record, "data_json", "phase run"
                )
                if (
                    phase_run_record.get("state") == "completed"
                    and phase_run.get("source_topic_id") == request["actor_topic_id"]
                    and phase_run.get("to_phase") == stage_entry_phase
                ):
                    stage_entry_phase_result_id = phase_result["result_id"]
                    break
        checkpoint = {
            "checkpoint_id": checkpoint_id,
            "record_revision": 1,
            "project_id": request["project_id"],
            "authority_tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"],
            "purpose": purpose,
            "storage_kind": storage_kind,
            "state": "prepared",
            "base_ref": base_ref,
            "base_commit": base_commit,
            "paths_json": _canonical_json(paths),
            "path_set_digest": _sha256(_canonical_json(paths).encode("utf-8")),
            "document_digests_json": _canonical_json(digests),
            "blob_ids_json": _canonical_json(blob_ids),
            "decision_digest": decision_digest,
            "decision_digests_json": _canonical_json(decision_digests),
            "confirmed_decision_ids_json": _canonical_json(confirmed_decision_ids),
            "published_identity": None,
            "tree_id": None,
            "broken_identity": None,
            "replacement_identity": None,
            "repaired_from": None,
            "snapshot_digest": snapshot_digest,
            "snapshot_bytes_b64": snapshot_bytes_b64,
            "stage_entry_phase": stage_entry_phase,
            "stage_entry_phase_result_id": stage_entry_phase_result_id,
            "creation_idempotency_key": request["idempotency_key"],
            "creation_fingerprint": _mutation_fingerprint(request),
            "creation_result_json": None,
        }
        record = {
            "checkpoint_id": checkpoint_id,
            "topic_id": request["actor_topic_id"],
            "state": "prepared",
            "record_revision": 1,
            "data_json": _canonical_json(checkpoint),
        }
        records["Checkpoints"].append(record)
        result = {
            "ok": True, "state": "prepared", "idempotent_replay": False,
            "project_id": request["project_id"], "tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"], "ledger_revision": next_revision,
            "record_revision": topic_revision, "checkpoint_record_revision": 1,
            "checkpoint_id": checkpoint_id, "storage_kind": storage_kind,
            "purpose": purpose, "base_ref": base_ref, "base_commit": base_commit,
            "paths": paths, "path_set_digest": checkpoint["path_set_digest"],
            "document_digests": digests, "decision_digest": decision_digest,
            "stage_entry_phase": stage_entry_phase,
            "stage_entry_phase_result_id": stage_entry_phase_result_id,
            "identity_reusable": False,
        }
        checkpoint["creation_result_json"] = _canonical_json(result)
        record["data_json"] = _canonical_json(checkpoint)
        _inject_failure("checkpoint-before-prepared-ledger-write")
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="checkpoint-prepared", result=result)
        _inject_failure("checkpoint-after-prepared-ledger-write")
        return result


def _cancel_checkpoint(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, topic_path, lock_path, owner_ref, _ = _checkpoint_paths(request)
    _expect_keys(
        request,
        {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key", "checkpoint_id", "reason",
        },
        "cancel-checkpoint request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    reason = _expect_string(request["reason"], "reason", max_bytes=2048)
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _checkpoint_record(records, _expect_string(request["checkpoint_id"], "checkpoint_id"))
        checkpoint = _checkpoint_data(record)
        if checkpoint["state"] != "prepared":
            raise ProtocolError("checkpoint_identity_conflict", "only an uncommitted prepared checkpoint can be cancelled")
        current_digest = _sha256(_require_regular_nosymlink(topic_path, "topic document"))
        frozen = json.loads(checkpoint["document_digests_json"])[json.loads(checkpoint["paths_json"])[0]]
        checkpoint["state"] = "cancelled"
        checkpoint["record_revision"] += 1
        checkpoint["cancel_reason"] = reason
        checkpoint["draft_changed"] = current_digest != frozen
        _store_checkpoint(record, checkpoint)
        next_revision = ledger_revision + 1
        result = {
            "ok": True, "state": "cancelled", "idempotent_replay": False,
            "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision, "record_revision": topic_revision,
            "checkpoint_id": checkpoint["checkpoint_id"], "checkpoint_record_revision": checkpoint["record_revision"],
            "draft_changed": checkpoint["draft_changed"], "identity_reusable": False,
        }
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="checkpoint-cancelled", result=result)
        return result


def _publish_git_checkpoint(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, topic_path, lock_path, owner_ref, storage_kind = _checkpoint_paths(request)
    if storage_kind != "git":
        raise ProtocolError("checkpoint_identity_conflict", "Git checkpoint publication requires a Git project")
    _expect_keys(
        request,
        {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key", "checkpoint_id",
            "expected_checkpoint_revision",
        },
        "publish-git-checkpoint request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _checkpoint_record(records, _expect_string(request["checkpoint_id"], "checkpoint_id"))
        checkpoint = _checkpoint_data(record)
        if request["expected_checkpoint_revision"] != checkpoint["record_revision"] or checkpoint["state"] != "prepared":
            raise ProtocolError(
                "checkpoint_identity_conflict",
                "checkpoint is not the expected prepared intent",
                context=_checkpoint_authority_context(request, ledger_revision, checkpoint),
            )
        paths = json.loads(checkpoint["paths_json"])
        digests = json.loads(checkpoint["document_digests_json"])
        current = _require_regular_nosymlink(topic_path, "topic document")
        if _sha256(current) != digests[paths[0]]:
            raise ProtocolError("checkpoint_changed_draft", "topic document bytes differ from the frozen checkpoint intent")
        if _checkpoint_decision_digest(records, request["actor_topic_id"]) != checkpoint["decision_digest"]:
            raise ProtocolError("checkpoint_changed_draft", "topic decisions differ from the frozen checkpoint intent")
        base_commit = _verify_git_base(project, checkpoint["base_ref"])
        if base_commit != checkpoint["base_commit"]:
            raise ProtocolError("checkpoint_base_invalid", "checkpoint base_ref no longer resolves to the frozen base commit")
        blobs = json.loads(checkpoint["blob_ids_json"])
        tree_id = _create_checkpoint_tree(project, base_commit, blobs)
        _inject_failure("git-before-commit-create")
        commit_id = _git(
            project,
            ["commit-tree", tree_id, "-p", base_commit],
            input_bytes=_checkpoint_commit_message(checkpoint).encode("utf-8"),
        ).decode("ascii").strip()
        verified = _commit_matches_checkpoint(project, commit_id, checkpoint)
        if verified is None:
            raise ProtocolError("checkpoint_history_mismatch", "new checkpoint commit did not fully verify")
        checkpoint_ref = (
            "refs/codex/design-discussion/checkpoints/"
            + checkpoint["checkpoint_id"].lower()
        )
        _git(project, ["update-ref", checkpoint_ref, commit_id])
        _inject_failure("git-after-commit-before-result-record")
        checkpoint["state"] = "completed"
        checkpoint["record_revision"] += 1
        checkpoint["published_identity"] = commit_id
        checkpoint["tree_id"] = tree_id
        checkpoint["checkpoint_ref"] = checkpoint_ref
        _store_checkpoint(record, checkpoint)
        next_revision = ledger_revision + 1
        result = {
            "ok": True, "state": "completed", "idempotent_replay": False,
            "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision, "record_revision": topic_revision,
            "checkpoint_id": checkpoint["checkpoint_id"], "checkpoint_record_revision": checkpoint["record_revision"],
            "commit_id": commit_id, "checkpoint_tree_id": tree_id, "paths_verified": paths,
            "checkpoint_ref": checkpoint_ref,
            "stage_entry_phase": checkpoint.get("stage_entry_phase"),
            "stage_entry_phase_result_id": checkpoint.get("stage_entry_phase_result_id"),
        }
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="git-checkpoint-published", result=result)
        return result


def _record_checkpoint_outcome_unknown(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, _, lock_path, owner_ref, storage_kind = _checkpoint_paths(request)
    _expect_keys(
        request,
        {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key", "checkpoint_id",
            "expected_checkpoint_revision",
        },
        "record-checkpoint-outcome-unknown request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _checkpoint_record(records, _expect_string(request["checkpoint_id"], "checkpoint_id"))
        checkpoint = _checkpoint_data(record)
        if request["expected_checkpoint_revision"] != checkpoint["record_revision"] or checkpoint["state"] != "prepared":
            raise ProtocolError("checkpoint_identity_conflict", "only the expected prepared checkpoint can become outcome-unknown")
        checkpoint["state"] = "outcome-unknown"
        checkpoint["record_revision"] += 1
        _store_checkpoint(record, checkpoint)
        next_revision = ledger_revision + 1
        result = {
            "ok": True, "state": "outcome-unknown", "idempotent_replay": False,
            "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision, "record_revision": topic_revision,
            "checkpoint_id": checkpoint["checkpoint_id"], "checkpoint_record_revision": checkpoint["record_revision"],
            "reconcile_required": True, "storage_kind": storage_kind,
        }
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="checkpoint-outcome-unknown", result=result)
        return result


def _reconcile_git_checkpoint(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, _, lock_path, owner_ref, storage_kind = _checkpoint_paths(request)
    if storage_kind != "git":
        raise ProtocolError("checkpoint_identity_conflict", "Git reconciliation requires a Git project")
    _expect_keys(
        request,
        {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key", "checkpoint_id",
            "expected_checkpoint_revision",
        },
        "reconcile-git-checkpoint request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _checkpoint_record(records, _expect_string(request["checkpoint_id"], "checkpoint_id"))
        checkpoint = _checkpoint_data(record)
        if request["expected_checkpoint_revision"] != checkpoint["record_revision"] or checkpoint["state"] != "outcome-unknown":
            raise ProtocolError("checkpoint_identity_conflict", "checkpoint is not awaiting Git reconciliation")
        matches = _all_checkpoint_candidates(project, checkpoint)
        if len(matches) > 1:
            raise ProtocolError("checkpoint_history_ambiguous", "more than one commit fully matches the frozen checkpoint")
        next_state = "completed" if matches else "prepared"
        checkpoint["state"] = next_state
        checkpoint["record_revision"] += 1
        if matches:
            checkpoint["published_identity"] = matches[0]["commit_id"]
            checkpoint["tree_id"] = matches[0]["tree_id"]
            checkpoint_ref = (
                "refs/codex/design-discussion/checkpoints/"
                + checkpoint["checkpoint_id"].lower()
            )
            _git(project, ["update-ref", checkpoint_ref, matches[0]["commit_id"]])
            checkpoint["checkpoint_ref"] = checkpoint_ref
        _store_checkpoint(record, checkpoint)
        next_revision = ledger_revision + 1
        result = {
            "ok": True, "state": next_state, "idempotent_replay": False,
            "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision, "record_revision": topic_revision,
            "checkpoint_id": checkpoint["checkpoint_id"], "checkpoint_record_revision": checkpoint["record_revision"],
            "commit_id": matches[0]["commit_id"] if matches else None,
            "retry_allowed": not matches, "match_count": len(matches),
            "checkpoint_ref": checkpoint.get("checkpoint_ref"),
        }
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="git-checkpoint-reconciled", result=result)
        return result


def _publish_non_git_checkpoint(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, topic_path, lock_path, owner_ref, storage_kind = _checkpoint_paths(request)
    if storage_kind != "non-git":
        raise ProtocolError("checkpoint_identity_conflict", "snapshot publication requires a non-Git project")
    _expect_keys(
        request,
        {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key", "checkpoint_id",
            "expected_checkpoint_revision",
        },
        "publish-non-git-checkpoint request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        active_gc = _active_checkpoint_gc(records)
        if active_gc is not None:
            raise ProtocolError(
                "checkpoint_gc_reconciliation_required",
                "checkpoint GC must be reconciled before publishing a snapshot",
                context={"gc_operation_id": active_gc["result_id"]},
            )
        record = _checkpoint_record(records, _expect_string(request["checkpoint_id"], "checkpoint_id"))
        checkpoint = _checkpoint_data(record)
        if request["expected_checkpoint_revision"] != checkpoint["record_revision"] or checkpoint["state"] != "prepared":
            raise ProtocolError(
                "checkpoint_identity_conflict",
                "checkpoint is not the expected prepared intent",
                context=_checkpoint_authority_context(request, ledger_revision, checkpoint),
            )
        paths = json.loads(checkpoint["paths_json"])
        digests = json.loads(checkpoint["document_digests_json"])
        document = _require_regular_nosymlink(topic_path, "topic document")
        if _sha256(document) != digests[paths[0]]:
            raise ProtocolError("checkpoint_changed_draft", "topic document bytes differ from the frozen checkpoint intent")
        if _checkpoint_decision_digest(records, request["actor_topic_id"]) != checkpoint["decision_digest"]:
            raise ProtocolError("checkpoint_changed_draft", "topic decisions differ from the frozen checkpoint intent")
        snapshot_bytes = base64.b64decode(checkpoint["snapshot_bytes_b64"], validate=True)
        snapshot_digest = checkpoint["snapshot_digest"]
        if _sha256(snapshot_bytes) != snapshot_digest:
            raise ProtocolError("state_corrupt", "frozen snapshot bytes do not match their prepared digest")
        snapshot_path = ledger_path.parents[4] / "checkpoints" / "sha256" / snapshot_digest[:2] / snapshot_digest
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        _inject_failure("snapshot-before-create")
        if snapshot_path.exists():
            try:
                existing = _require_regular_nosymlink(
                    snapshot_path, "checkpoint snapshot"
                )
            except ProtocolError as error:
                raise ProtocolError(
                    "checkpoint_snapshot_corrupt",
                    "existing content-addressed snapshot is not a safe immutable object",
                    cause=error.message,
                ) from error
            if _sha256(existing) != snapshot_digest or existing != snapshot_bytes:
                raise ProtocolError("checkpoint_snapshot_corrupt", "existing content-addressed snapshot does not match its digest")
            reused = True
        else:
            _atomic_replace(snapshot_path, snapshot_bytes, mode=0o400)
            snapshot_path.chmod(0o400)
            reused = False
        _inject_failure("snapshot-after-create-before-result-record")
        checkpoint["state"] = "completed"
        checkpoint["record_revision"] += 1
        checkpoint["published_identity"] = snapshot_digest
        checkpoint["snapshot_path"] = str(snapshot_path)
        _store_checkpoint(record, checkpoint)
        next_revision = ledger_revision + 1
        result = {
            "ok": True, "state": "completed", "idempotent_replay": False,
            "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision, "record_revision": topic_revision,
            "checkpoint_id": checkpoint["checkpoint_id"], "checkpoint_record_revision": checkpoint["record_revision"],
            "snapshot_digest": snapshot_digest, "snapshot_path": str(snapshot_path), "snapshot_reused": reused,
            "stage_entry_phase": checkpoint.get("stage_entry_phase"),
            "stage_entry_phase_result_id": checkpoint.get("stage_entry_phase_result_id"),
        }
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="non-git-checkpoint-published", result=result)
        return result


def _reconcile_non_git_checkpoint(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, topic_path, lock_path, owner_ref, storage_kind = _checkpoint_paths(request)
    if storage_kind != "non-git":
        raise ProtocolError("checkpoint_identity_conflict", "snapshot reconciliation requires a non-Git project")
    _expect_keys(
        request,
        {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key", "checkpoint_id",
            "expected_checkpoint_revision",
        },
        "reconcile-non-git-checkpoint request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _checkpoint_record(records, request["checkpoint_id"])
        checkpoint = _checkpoint_data(record)
        if request["expected_checkpoint_revision"] != checkpoint["record_revision"] or checkpoint["state"] != "outcome-unknown":
            raise ProtocolError("checkpoint_identity_conflict", "checkpoint is not awaiting snapshot reconciliation")
        snapshot_bytes = base64.b64decode(checkpoint["snapshot_bytes_b64"], validate=True)
        snapshot_digest = checkpoint["snapshot_digest"]
        if _sha256(snapshot_bytes) != snapshot_digest:
            raise ProtocolError("state_corrupt", "frozen snapshot bytes do not match their prepared digest")
        snapshot_path = ledger_path.parents[4] / "checkpoints" / "sha256" / snapshot_digest[:2] / snapshot_digest
        if snapshot_path.exists():
            existing = _require_regular_nosymlink(snapshot_path, "checkpoint snapshot")
            if existing != snapshot_bytes:
                raise ProtocolError("checkpoint_snapshot_corrupt", "uncertain snapshot object does not match frozen bytes")
            next_state = "completed"
            checkpoint["published_identity"] = snapshot_digest
            checkpoint["snapshot_path"] = str(snapshot_path)
        else:
            next_state = "prepared"
        checkpoint["state"] = next_state
        checkpoint["record_revision"] += 1
        _store_checkpoint(record, checkpoint)
        next_revision = ledger_revision + 1
        result = {
            "ok": True, "state": next_state, "idempotent_replay": False,
            "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision, "record_revision": topic_revision,
            "checkpoint_id": checkpoint["checkpoint_id"], "checkpoint_record_revision": checkpoint["record_revision"],
            "snapshot_digest": snapshot_digest if next_state == "completed" else None,
            "snapshot_path": str(snapshot_path), "retry_allowed": next_state == "prepared",
        }
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="non-git-checkpoint-reconciled", result=result)
        return result


def _mark_checkpoint_broken(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, _, lock_path, owner_ref, _ = _checkpoint_paths(request)
    _expect_keys(
        request,
        {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key", "checkpoint_id",
            "expected_checkpoint_revision", "broken_identity", "reason",
        },
        "mark-checkpoint-broken request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    broken_identity = _expect_string(request["broken_identity"], "broken_identity", max_bytes=512)
    reason = _expect_string(request["reason"], "reason", max_bytes=2048)
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _checkpoint_record(records, request["checkpoint_id"])
        checkpoint = _checkpoint_data(record)
        if request["expected_checkpoint_revision"] != checkpoint["record_revision"] or checkpoint["state"] != "completed" or checkpoint["published_identity"] != broken_identity:
            raise ProtocolError("checkpoint_identity_conflict", "broken checkpoint fact does not match the completed record")
        checkpoint["state"] = "broken"
        checkpoint["record_revision"] += 1
        checkpoint["broken_identity"] = broken_identity
        checkpoint["break_reason"] = reason
        _store_checkpoint(record, checkpoint)
        next_revision = ledger_revision + 1
        result = {
            "ok": True, "state": "broken", "idempotent_replay": False,
            "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision, "record_revision": topic_revision,
            "checkpoint_id": checkpoint["checkpoint_id"], "checkpoint_record_revision": checkpoint["record_revision"],
            "broken_identity": broken_identity, "original_fact_preserved": True,
        }
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="checkpoint-broken", result=result)
        return result


def _repair_checkpoint(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, _, lock_path, owner_ref, storage_kind = _checkpoint_paths(request)
    if storage_kind != "git":
        raise ProtocolError("checkpoint_identity_conflict", "repair-checkpoint currently applies to Git checkpoint commits")
    _expect_keys(
        request,
        {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key", "checkpoint_id",
            "expected_checkpoint_revision", "replacement_commit",
            "replacement_base_ref",
        },
        "repair-checkpoint request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    replacement = _expect_string(request["replacement_commit"], "replacement_commit", max_bytes=128)
    replacement_base_ref = _expect_string(request["replacement_base_ref"], "replacement_base_ref", max_bytes=512)
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _checkpoint_record(records, request["checkpoint_id"])
        checkpoint = _checkpoint_data(record)
        if request["expected_checkpoint_revision"] != checkpoint["record_revision"] or checkpoint["state"] != "broken":
            raise ProtocolError("checkpoint_identity_conflict", "checkpoint is not the expected broken fact")
        replacement_parent = _verify_git_base(project, replacement_base_ref)
        match = (
            _commit_matches_checkpoint(
                project,
                replacement,
                checkpoint,
                expected_parent=replacement_parent,
            )
            if replacement_parent is not None
            else None
        )
        matches = (
            _all_checkpoint_candidates(
                project, checkpoint, expected_parent=replacement_parent
            )
            if replacement_parent is not None
            else []
        )
        if match is None or [item["commit_id"] for item in matches] != [replacement]:
            raise ProtocolError("checkpoint_repair_not_unique", "replacement commit is not the unique fully verified checkpoint")
        _inject_failure("repair-before-result-record")
        checkpoint["state"] = "completed"
        checkpoint["record_revision"] += 1
        checkpoint["replacement_identity"] = replacement
        checkpoint["repaired_from"] = checkpoint["broken_identity"]
        checkpoint["published_identity"] = replacement
        checkpoint["tree_id"] = match["tree_id"]
        checkpoint["replacement_parent"] = replacement_parent
        checkpoint_ref = checkpoint.get("checkpoint_ref")
        if checkpoint_ref:
            _git(project, ["update-ref", checkpoint_ref, replacement])
        _store_checkpoint(record, checkpoint)
        next_revision = ledger_revision + 1
        result = {
            "ok": True, "state": "completed", "idempotent_replay": False,
            "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision, "record_revision": topic_revision,
            "checkpoint_id": checkpoint["checkpoint_id"], "checkpoint_record_revision": checkpoint["record_revision"],
            "broken_identity": checkpoint["broken_identity"], "replacement_identity": replacement,
            "original_fact_preserved": True,
        }
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="checkpoint-repaired", result=result)
        return result


def _checkpoint_gc_candidates(ledger_path: Path, records: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    snapshot_root = ledger_path.parents[4] / "checkpoints" / "sha256"
    if not snapshot_root.exists():
        return []
    referenced = {
        checkpoint.get("published_identity")
        for checkpoint in (_checkpoint_data(record) for record in records["Checkpoints"])
        if checkpoint.get("state") not in {"cancelled", "superseded"}
    }
    uncertain = {
        checkpoint.get("published_identity")
        for checkpoint in (_checkpoint_data(record) for record in records["Checkpoints"])
        if checkpoint.get("state") in {"outcome-unknown", "broken"}
    }
    uncertain_objects: set[str] = set()
    for checkpoint in (
        _checkpoint_data(record) for record in records["Checkpoints"]
    ):
        if (
            checkpoint.get("storage_kind") != "non-git"
            or checkpoint.get("state") not in CHECKPOINT_ACTIVE_STATES
        ):
            continue
        uncertain_objects.add(checkpoint["snapshot_digest"])
    candidates = []
    for path in sorted(snapshot_root.glob("*/*")):
        if not path.is_file():
            continue
        digest = path.name
        if digest in referenced or digest in uncertain or digest in uncertain_objects:
            continue
        candidates.append({"digest": digest, "path": str(path)})
    return candidates


def _canonical_checkpoint_snapshot_path(ledger_path: Path, digest: str) -> Path:
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ProtocolError("checkpoint_snapshot_corrupt", "GC candidate digest is invalid")
    root = (ledger_path.parents[4] / "checkpoints" / "sha256").resolve()
    path = root / digest[:2] / digest
    if path.parent != root / digest[:2] or not path.is_absolute():
        raise ProtocolError("checkpoint_snapshot_corrupt", "GC candidate path is invalid")
    return path


def _validate_checkpoint_gc_candidate_path(
    ledger_path: Path, item: dict[str, Any]
) -> Path:
    if set(item) != {"digest", "path"}:
        raise ProtocolError("checkpoint_snapshot_corrupt", "GC candidate shape is invalid")
    expected = _canonical_checkpoint_snapshot_path(ledger_path, item["digest"])
    supplied = Path(_expect_string(item["path"], "GC candidate path", max_bytes=4096))
    if supplied != expected:
        raise ProtocolError(
            "checkpoint_snapshot_corrupt",
            "GC candidate path is outside the canonical snapshot root",
        )
    return expected


def _checkpoint_gc_dry_run(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, _, lock_path, owner_ref = _evolution_paths(request, query=True)
    _, storage_kind = _coordination_root(project)
    if storage_kind != "non-git":
        raise ProtocolError("checkpoint_identity_conflict", "snapshot GC requires a non-Git project")
    _expect_keys(request, {"protocol_version", "operation", "project_path", "project_id", "tree_id", "actor_topic_id", "actor_conversation_ref"}, "checkpoint-gc-dry-run request")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        active_gc = _active_checkpoint_gc(records)
        if active_gc is not None:
            raise ProtocolError(
                "checkpoint_gc_reconciliation_required",
                "checkpoint GC must be reconciled before another dry-run",
                context={"gc_operation_id": active_gc["result_id"]},
            )
        candidates = _checkpoint_gc_candidates(ledger_path, records)
        candidate_digest = _sha256(_canonical_json(candidates).encode("utf-8"))
        return {
            "ok": True, "state": "dry-run", "project_id": request["project_id"], "tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"], "ledger_revision": int(frontmatter["ledger_revision"]),
            "record_revision": _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")["record_revision"],
            "candidates": candidates, "candidate_digest": candidate_digest, "confirmation_required": True,
        }


def _checkpoint_gc_confirm(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, _, lock_path, owner_ref, storage_kind = _checkpoint_paths(request)
    if storage_kind != "non-git":
        raise ProtocolError("checkpoint_identity_conflict", "snapshot GC requires a non-Git project")
    _expect_keys(
        request,
        {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key", "candidate_digest", "candidates",
        },
        "checkpoint-gc-confirm request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    supplied_candidates = request["candidates"]
    if not isinstance(supplied_candidates, list) or any(not isinstance(item, dict) or set(item) != {"digest", "path"} for item in supplied_candidates):
        raise ProtocolError("invalid_request", "candidates must be the exact dry-run candidate objects")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        active_gc = _active_checkpoint_gc(records)
        if active_gc is not None:
            raise ProtocolError(
                "checkpoint_gc_reconciliation_required",
                "checkpoint GC must be reconciled before another confirmation",
                context={"gc_operation_id": active_gc["result_id"]},
            )
        actual = _checkpoint_gc_candidates(ledger_path, records)
        actual_digest = _sha256(_canonical_json(actual).encode("utf-8"))
        if actual != supplied_candidates or request["candidate_digest"] != actual_digest:
            raise ProtocolError("checkpoint_gc_confirmation_mismatch", "GC confirmation does not bind the current exact candidates")
        deleted = []
        for item in actual:
            path = _validate_checkpoint_gc_candidate_path(ledger_path, item)
            content = _require_regular_nosymlink(path, "checkpoint snapshot GC candidate")
            if _sha256(content) != item["digest"]:
                raise ProtocolError("checkpoint_snapshot_corrupt", "GC candidate digest does not match its object name")
        _inject_failure("gc-before-delete")
        gc_operation_id = _checkpoint_gc_id(request["idempotency_key"])
        gc_operation = {
            "gc_operation_id": gc_operation_id,
            "record_revision": 1,
            "state": "outcome-unknown",
            "candidate_digest": actual_digest,
            "candidates_json": _canonical_json(actual),
            "confirmation_ledger_revision": ledger_revision,
        }
        gc_record = {
            "result_id": gc_operation_id,
            "result_kind": "checkpoint-gc",
            "state": "outcome-unknown",
            "record_revision": 1,
            "data_json": _canonical_json(gc_operation),
        }
        records["Phase Results"].append(gc_record)
        outcome_revision = ledger_revision + 1
        outcome_result = {
            "ok": True,
            "state": "outcome-unknown",
            "idempotent_replay": False,
            "project_id": request["project_id"],
            "tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"],
            "ledger_revision": outcome_revision,
            "record_revision": topic_revision,
            "gc_operation_id": gc_operation_id,
            "gc_record_revision": 1,
            "candidate_digest": actual_digest,
            "candidates": actual,
            "reconcile_required": True,
        }
        _write_ledger_transaction(
            ledger_path,
            frontmatter,
            records,
            request,
            ledger_revision=outcome_revision,
            event_type="checkpoint-gc-outcome-unknown",
            result=outcome_result,
        )
        _inject_failure("gc-after-outcome-unknown-record")
        for item in actual:
            _validate_checkpoint_gc_candidate_path(ledger_path, item).unlink()
            deleted.append(item)
            _inject_failure("gc-during-delete")
        _inject_failure("gc-after-delete-before-result-record")
        gc_operation["state"] = "completed"
        gc_operation["record_revision"] += 1
        gc_operation["deleted_json"] = _canonical_json(deleted)
        _store_checkpoint_gc(gc_record, gc_operation)
        next_revision = outcome_revision + 1
        result = {
            "ok": True, "state": "completed", "idempotent_replay": False,
            "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision, "record_revision": topic_revision,
            "gc_operation_id": gc_operation_id,
            "gc_record_revision": gc_operation["record_revision"],
            "deleted": deleted, "candidate_digest": actual_digest,
        }
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="checkpoint-gc-completed", result=result)
        return result


def _reconcile_checkpoint_gc(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, _, lock_path, owner_ref, storage_kind = _checkpoint_paths(request)
    if storage_kind != "non-git":
        raise ProtocolError("checkpoint_identity_conflict", "snapshot GC requires a non-Git project")
    _expect_keys(
        request,
        {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key", "gc_operation_id",
            "expected_gc_revision",
        },
        "reconcile-checkpoint-gc request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _checkpoint_gc_record(
            records, _expect_string(request["gc_operation_id"], "gc_operation_id")
        )
        operation = _checkpoint_gc_data(record)
        if (
            request["expected_gc_revision"] != operation["record_revision"]
            or operation["state"] != "outcome-unknown"
        ):
            raise ProtocolError(
                "checkpoint_gc_confirmation_mismatch",
                "checkpoint GC is not the expected outcome-unknown operation",
            )
        candidates = json.loads(operation["candidates_json"])
        if operation["candidate_digest"] != _sha256(
            _canonical_json(candidates).encode("utf-8")
        ):
            raise ProtocolError("state_corrupt", "checkpoint GC frozen candidates are invalid")
        already_deleted = []
        deleted = []
        for item in candidates:
            path = _validate_checkpoint_gc_candidate_path(ledger_path, item)
            if not path.exists():
                already_deleted.append(item)
                continue
            content = _require_regular_nosymlink(path, "checkpoint snapshot GC candidate")
            if _sha256(content) != item["digest"]:
                raise ProtocolError(
                    "checkpoint_snapshot_corrupt",
                    "remaining GC candidate digest does not match its object name",
                )
            path.unlink()
            deleted.append(item)
        operation["state"] = "completed"
        operation["record_revision"] += 1
        operation["deleted_json"] = _canonical_json(candidates)
        _store_checkpoint_gc(record, operation)
        next_revision = ledger_revision + 1
        result = {
            "ok": True,
            "state": "completed",
            "idempotent_replay": False,
            "project_id": request["project_id"],
            "tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision,
            "record_revision": topic_revision,
            "gc_operation_id": operation["gc_operation_id"],
            "gc_record_revision": operation["record_revision"],
            "candidate_digest": operation["candidate_digest"],
            "deleted": candidates,
            "deleted_during_reconciliation": deleted,
            "already_deleted": already_deleted,
            "recovered_outcome_unknown": True,
        }
        _write_ledger_transaction(
            ledger_path,
            frontmatter,
            records,
            request,
            ledger_revision=next_revision,
            event_type="checkpoint-gc-reconciled",
            result=result,
        )
        return result


def _completed_checkpoint(
    records: dict[str, list[dict[str, Any]]], checkpoint_id: Any
) -> dict[str, Any]:
    record = _checkpoint_record(
        records, _expect_string(checkpoint_id, "source_checkpoint_id", max_bytes=64)
    )
    checkpoint = _checkpoint_data(record)
    if (
        checkpoint.get("state") != "completed"
        or checkpoint.get("purpose") != "stage-entry"
        or not checkpoint.get("published_identity")
    ):
        raise ProtocolError(
            "phase_checkpoint_invalid",
            "wrapper phase source must be a completed stage checkpoint",
        )
    return checkpoint


def _validate_checkpoints(
    project: Path,
    records: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    checkpoints = []
    for record in records["Checkpoints"]:
        checkpoint = _checkpoint_data(record)
        try:
            creation_key = str(uuid.UUID(checkpoint["creation_idempotency_key"]))
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise ProtocolError(
                "state_corrupt", "checkpoint creation identity is invalid"
            ) from error
        if (
            record.get("checkpoint_id") != checkpoint.get("checkpoint_id")
            or record.get("state") != checkpoint.get("state")
            or record.get("record_revision") != checkpoint.get("record_revision")
            or checkpoint["checkpoint_id"] != _checkpoint_id(creation_key)
            or not isinstance(checkpoint.get("creation_fingerprint"), str)
            or not isinstance(checkpoint.get("creation_result_json"), str)
        ):
            raise ProtocolError("state_corrupt", "checkpoint record envelope does not match its data")
        paths = json.loads(checkpoint["paths_json"])
        digests = json.loads(checkpoint["document_digests_json"])
        if (
            not isinstance(paths, list)
            or not paths
            or sorted(paths) != paths
            or set(paths) != set(digests)
            or checkpoint["path_set_digest"] != _sha256(_canonical_json(paths).encode("utf-8"))
        ):
            raise ProtocolError("state_corrupt", "checkpoint frozen paths or digests are invalid")
        if checkpoint["state"] == "completed":
            if checkpoint["storage_kind"] == "git":
                expected_parent = checkpoint.get("replacement_parent")
                if checkpoint.get("checkpoint_ref"):
                    ref_commit = _git(project, ["rev-parse", "--verify", checkpoint["checkpoint_ref"]]).decode("ascii").strip()
                    if ref_commit != checkpoint["published_identity"]:
                        raise ProtocolError("checkpoint_history_mismatch", "checkpoint ref does not resolve to published identity")
                if _commit_matches_checkpoint(project, checkpoint["published_identity"], checkpoint, expected_parent=expected_parent) is None:
                    raise ProtocolError("checkpoint_history_mismatch", "completed Git checkpoint no longer fully verifies")
            else:
                snapshot_path = Path(checkpoint["snapshot_path"])
                snapshot = _require_regular_nosymlink(snapshot_path, "checkpoint snapshot")
                if _sha256(snapshot) != checkpoint["published_identity"]:
                    raise ProtocolError("checkpoint_snapshot_corrupt", "completed snapshot digest no longer verifies")
        checkpoints.append(dict(checkpoint))
    return sorted(checkpoints, key=lambda item: item["checkpoint_id"])

