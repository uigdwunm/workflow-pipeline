#!/usr/bin/env python3
"""Prepare, write and freeze one requirement using existing checkpoint ownership.

The controller persists complete returned intents in its existing checkpoint before
mutation. This adapter owns no registry or durable execution database.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import entry_prepare as entry
import discussion_protocol
import skill_preflight
from supervision_protocol import PUBLICATION_LOCK_FILENAME, _flock_with_timeout

PROTOCOL = "requirement-freeze-v1"


def sha(data):
    return None if data is None else hashlib.sha256(data).hexdigest()


def sealed(value):
    return {**value, "digest": entry.digest(value)}


def validate_seal(value):
    entry.require(isinstance(value, dict) and value.get("digest") == entry.digest({k: v for k, v in value.items() if k != "digest"}),
                  "invalid_intent", "complete original checkpoint intent required")


def index_entries(root, paths=None):
    args = ["ls-files", "--stage", "-z"]
    if paths is not None:
        args += ["--", *paths]
    return entry.git(root, *args).stdout


def unrelated(root, paths):
    excluded = {p.encode() for p in paths}
    index = b"\0".join(row for row in index_entries(root).split(b"\0") if row and row.split(b"\t", 1)[1] not in excluded)
    names = entry.git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").stdout.split(b"\0")
    files = {}
    for raw in set(names) - excluded - {b""}:
        name = os.fsdecode(raw)
        target = Path(root) / name
        if target.is_symlink():
            files[name] = {"symlink": os.readlink(target)}
        elif target.is_file():
            with target.open("rb") as stream:
                hasher = hashlib.sha256()
                for chunk in iter(lambda: stream.read(65536), b""):
                    hasher.update(chunk)
                h = hasher.hexdigest()
            files[name] = {"sha256": h, "mode": target.stat().st_mode & 0o777}
        elif target.is_dir():
            files[name] = {"kind": "directory"}
        else:
            files[name] = {"kind": "absent"}
    return {"index": sha(index), "files": entry.digest(files)}


def bind(current, original):
    for key in ("actor", "entry", "target"):
        entry.require(current[key] == original[key], "identity_changed", "preparation owner or target changed")
    for key in ("root", "git_common_dir", "branch", "kind"):
        entry.require(current["repository"].get(key) == original["repository"].get(key), "repository_changed", "repository binding changed")
    entry.require(current["requirement"].get("kind") == original["requirement"].get("kind"), "source_changed", "requirement source changed")
    for key in ("thread_id", "model", "reasoning_effort"):
        entry.require(current["configuration"][key] == original["configuration"][key], "configuration_changed", "preparation configuration changed")
    entry.require(set(current["packages"]) == set(original["packages"]), "package_changed", "package route changed")
    for name, identity in original["packages"].items():
        skill_preflight.verify_identity(identity)
        entry.require(current["packages"][name]["compatibility_key"] == identity["compatibility_key"], "package_changed", "package compatibility changed")


def standalone(current):
    entry.require(current["repository"]["kind"] == "git", "git_required", "standalone freeze requires Git")
    entry.require(current["requirement"]["kind"] in {"stage1", "conversation"}, "invalid_source", "mutable standalone requirement source required")
    target = current["target"]
    entry.require(target is not None and target["kind"] == "planning", "target_required", "explicit planning checkout required")
    entry.require(current["repository"]["cwd"] == current["repository"]["root"], "target_mismatch", "run document preparation at planning checkout root")
    return Path(current["repository"]["root"])


def previous_document(current, previous, path):
    if "digest" in previous:
        validate_seal(previous)
        entry.require(previous.get("protocol") == PROTOCOL, "invalid_source", "unknown document receipt")
        bind(current, previous["entry"])
    else:
        # Projection of the original trusted conversation checkpoint, supplied
        # by its controller, never reconstructed from arbitrary document text.
        entry.fields(previous, {"path", "sha256", "version", "write_owner", "repository", "receipt"})
        entry.nonempty(previous["receipt"])
        entry.require(previous["write_owner"] == current["actor"]["thread_id"]
                      and previous["repository"] == current["repository"]["root"]
                      and current["requirement"].get("path") == path,
                      "ownership_required", "existing checkpoint owner, repository and source path must match")
    entry.require(previous["path"] == path, "source_changed", "original requirement path required")


def safe_stage_paths(root, paths):
    for path in paths:
        data = entry.read_document(root, path)
        entry.require(data is not None, "document_missing", "stage-owned file is absent")
        rows = index_entries(root, [path]).split(b"\0")
        for row in filter(None, rows):
            metadata = row.split(b"\t", 1)[0].split()
            entry.require(metadata[2] == b"0" and metadata[0] == b"100644", "index_conflict", "regular non-conflicted documentation index required")
        status = entry.git(root, "diff", "--cached", "--name-only", "-z", "--", path).stdout
        if status:
            indexed = entry.git(root, "show", ":" + path, check=False)
            entry.require(indexed.returncode == 0 and indexed.stdout == data, "partial_staging", "stage-owned file has a different staged version")
        entry.require(entry.git(root, "check-ignore", "-q", "--", path, check=False).returncode == 1,
                      "ignored_document", "ignored requirement cannot be committed")
        entry.require(not (root / path).stat().st_mode & 0o111, "invalid_document", "executable requirement is unsupported")


def prepare(current, request):
    root = standalone(current)
    entry.fields(request, {"protocol", "operation", "entry", "purpose", "path", "version", "authorization"},
                 {"content", "previous", "owned_paths"})
    entry.nonempty(request["authorization"])
    entry.require(type(request["version"]) is int and request["version"] > 0, "invalid_version", "positive source version required")
    path = request["path"]
    # New paths are supplied from the repository convention by the controller;
    # all retries and updates must use the checkpoint's exact recorded path.
    target = entry.document_path(root, path)
    entry.require(path.endswith(".md"), "invalid_document", "Markdown requirement path required")
    source_path = current["requirement"].get("path")
    entry.require(source_path in (None, path), "source_changed", "entry names a different requirement")
    before = entry.read_document(root, path)
    previous = request.get("previous")
    if before is not None:
        entry.require(previous is not None, "ownership_required", "existing document needs its original checkpoint receipt")
    if previous is not None:
        previous_document(current, previous, path)
        entry.require(previous["sha256"] == sha(before), "document_changed", "document differs from its checkpoint")
        entry.require(request["version"] >= previous["version"], "invalid_version", "requirement version regressed")
    purpose = request["purpose"]
    entry.require(purpose in {"write", "freeze"}, "invalid_request", "prepare purpose must be write or freeze")
    intent = {"protocol": PROTOCOL, "kind": "intent", "operation_id": str(uuid.uuid4()),
              "purpose": purpose, "entry": current, "path": path, "absolute_path": str(target),
              "version": request["version"], "authorization": request["authorization"],
              "baseline": current["repository"]["head"], "before_sha256": sha(before)}
    if purpose == "write":
        entry.require("content" in request and "owned_paths" not in request, "invalid_request", "write needs content only")
        entry.require(isinstance(request["content"], str) and 0 < len(request["content"].encode()) <= entry.MAX_BYTES,
                      "invalid_document", "bounded nonempty document content required")
        content = request["content"].encode()
        entry.require(previous is None or sha(content) == previous["sha256"] or request["version"] > previous["version"],
                      "invalid_version", "changed requirement content needs a newer version")
        intent.update(content=request["content"], sha256=sha(content))
    else:
        entry.require(before is not None and "content" not in request, "invalid_request", "freeze requires an existing document")
        paths = request.get("owned_paths", [path])
        entry.require(isinstance(paths, list) and paths == sorted(set(paths)) and path in paths and len(paths) <= 32,
                      "invalid_paths", "sorted exact owned documentation paths required")
        for p in paths:
            entry.require(isinstance(p, str) and p.endswith(".md"), "invalid_document", "only exact Markdown documentation paths allowed")
        safe_stage_paths(root, paths)
        intent.update(paths=paths, sha256=sha(before),
                      hashes={p: sha(entry.read_document(root, p)) for p in paths},
                      unrelated=unrelated(root, paths))
        intent["reuse_commit"] = all(
            sha(entry.git(root, "show", intent["baseline"] + ":" + p, check=False).stdout) == intent["hashes"][p]
            and entry.git_text(root, "ls-tree", intent["baseline"], "--", p).startswith("100644 ")
            for p in paths)
    return sealed(intent)


def validate_intent(current, intent, purpose):
    validate_seal(intent)
    entry.require(intent.get("protocol") == PROTOCOL and intent.get("kind") == "intent" and intent.get("purpose") == purpose,
                  "invalid_intent", "wrong preparation intent")
    bind(current, intent["entry"])
    root = standalone(current)
    entry.require(str(entry.document_path(root, intent["path"])) == intent["absolute_path"], "source_changed", "document path changed")
    return root


def write(current, intent):
    root = validate_intent(current, intent, "write")
    entry.require(current["repository"]["head"] == intent["baseline"], "head_changed", "planning HEAD changed before document write")
    before = entry.read_document(root, intent["path"])
    content = intent["content"].encode()
    entry.require(sha(content) == intent["sha256"], "invalid_intent", "write content differs from intent")
    if sha(before) != intent["sha256"]:
        entry.require(sha(before) == intent["before_sha256"], "document_changed", "write baseline changed")
        target = entry.document_path(root, intent["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target = entry.document_path(root, intent["path"])
        fd, temporary = tempfile.mkstemp(prefix=".requirement-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            entry.require(sha(entry.read_document(root, intent["path"])) == intent["before_sha256"], "document_changed", "document changed during write")
            if before is None:
                os.chmod(temporary, 0o644)
                # An exclusive hard link publishes complete bytes atomically;
                # a process death cannot leave a partially created requirement.
                os.link(temporary, target)
            else:
                os.chmod(temporary, target.stat().st_mode & 0o777)
                os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    entry.require(entry.read_document(root, intent["path"]) == content, "write_unverified", "document write did not match intent")
    return sealed({"protocol": PROTOCOL, "kind": "document", "entry": current, "path": intent["path"],
                   "absolute_path": intent["absolute_path"], "version": intent["version"],
                   "sha256": intent["sha256"], "operation_id": intent["operation_id"]})


def commit_matches(root, commit, intent):
    parents = entry.git_text(root, "show", "-s", "--format=%P", commit).split()
    if parents != [intent["baseline"]]:
        return False
    changed = set(os.fsdecode(p) for p in entry.git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", commit).stdout.split(b"\0") if p)
    if not changed or not changed <= set(intent["paths"]):
        return False
    message = entry.git_text(root, "show", "-s", "--format=%B", commit)
    if "Codex-Requirement-Intent: " + intent["digest"] not in message.splitlines():
        return False
    for path, expected in intent["hashes"].items():
        blob = entry.git(root, "show", commit + ":" + path, check=False)
        mode = entry.git_text(root, "ls-tree", commit, "--", path).split()
        if blob.returncode or sha(blob.stdout) != expected or not mode or mode[0] != "100644":
            return False
    return True


def prior_freeze_commits(root, intent):
    # Include unreachable objects: HEAD may have been moved after a successful
    # commit whose receipt was lost. A trailer only selects candidates; complete
    # parent/path/blob verification below decides whether they match.
    objects = entry.git(root, "cat-file", "--batch-all-objects", "--batch-check=%(objectname) %(objecttype)").stdout
    commits = [row.split()[0] for row in objects.splitlines() if row.endswith(b" commit")]
    if not commits:
        return []
    candidates = entry.git(root, "log", "--no-walk", "--stdin", "--format=%H", "--fixed-strings",
                           "--grep=Codex-Requirement-Intent: " + intent["digest"], data=b"\n".join(commits) + b"\n").stdout
    return [commit.decode() for commit in candidates.splitlines() if commit_matches(root, commit.decode(), intent)]


def frozen_result(current, root, intent, commit):
    for path, expected in intent["hashes"].items():
        entry.require(sha(entry.read_document(root, path)) == expected, "document_changed", "committed source differs from work document")
    entry.require(unrelated(root, intent["paths"]) == intent["unrelated"], "workspace_changed", "unrelated workspace or index changed; preserve and reconcile")
    path = intent["path"]
    identity = {"path": path, "sha256": intent["sha256"], "version": intent["version"]}
    changed = [] if intent.get("reuse_commit") else [os.fsdecode(p) for p in entry.git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", commit).stdout.split(b"\0") if p]
    return sealed({"protocol": PROTOCOL, "kind": "frozen", "source_kind": current["requirement"]["kind"],
                   "entry": current, "path": path, "absolute_path": str(root / path), "version": intent["version"],
                   "sha256": intent["sha256"], "commit": commit,
                   "blob": entry.git_text(root, "rev-parse", commit + ":" + path),
                   "baseline": intent["baseline"], "changed_paths": changed,
                   "requirement_identity": identity, "operation_id": intent["operation_id"]})


def freeze(current, intent, reconcile=False):
    root = validate_intent(current, intent, "freeze")
    head = entry.git_text(root, "rev-parse", "HEAD")
    if intent.get("reuse_commit"):
        entry.require(head == intent["baseline"], "head_changed", "HEAD changed before source reuse")
        return frozen_result(current, root, intent, head)
    matches = prior_freeze_commits(root, intent)
    entry.require(len(matches) <= 1, "commit_ambiguous", "multiple commits match the original freeze intent")
    if matches:
        entry.require(matches == [head], "commit_detached", "matching freeze commit exists off HEAD; restore original lineage before retry")
        return frozen_result(current, root, intent, head)
    if head != intent["baseline"]:
        entry.require(commit_matches(root, head, intent), "head_changed", "HEAD is not the original baseline or matching freeze commit")
        return frozen_result(current, root, intent, head)
    for path, expected in intent["hashes"].items():
        entry.require(sha(entry.read_document(root, path)) == expected, "document_changed", "freeze bytes changed")
    safe_stage_paths(root, intent["paths"])
    entry.require(unrelated(root, intent["paths"]) == intent["unrelated"], "workspace_changed", "workspace differs from prepared baseline")
    if reconcile:
        return {"protocol": PROTOCOL, "state": "prepared", "intent": intent}
    # New files must be known to the real index for commit --only. Staging is
    # limited to the frozen paths; a failed hook preserves this recoverable state.
    entry.git(root, "add", "--", *intent["paths"])
    for path, expected in intent["hashes"].items():
        entry.require(sha(entry.git(root, "show", ":" + path).stdout) == expected,
                      "filtered_bytes_changed", "Git filter changed frozen document bytes")
    entry.require(entry.git_text(root, "rev-parse", "HEAD") == intent["baseline"], "head_changed", "HEAD changed before commit")
    message = "docs: freeze requirement\n\nCodex-Requirement-Intent: " + intent["digest"] + "\n"
    result = entry.git(root, "commit", "--only", "-m", message, "--", *intent["paths"], check=False)
    head = entry.git_text(root, "rev-parse", "HEAD")
    entry.require(commit_matches(root, head, intent), "commit_unverified", "freeze commit failed or differs from prepared intent; reconcile before retry")
    entry.require(result.returncode == 0, "commit_outcome_unknown", "commit exists but command failed; reconcile the same intent")
    return frozen_result(current, root, intent, head)


def verify(current, evidence):
    entry.require(current["requirement"]["kind"] == "frozen", "invalid_source", "select the existing frozen source explicitly")
    root = Path(current["repository"]["root"])
    original = "digest" in evidence
    if original:
        validate_seal(evidence)
        entry.require(evidence.get("protocol") == PROTOCOL and evidence.get("kind") == "frozen", "invalid_source", "frozen requirement receipt required")
        entry.require(current["repository"].get("git_common_dir") == evidence["entry"]["repository"].get("git_common_dir"),
                      "repository_changed", "source belongs to a different Git repository")
    else:
        entry.fields(evidence, {"path", "commit", "sha256", "version", "owner_ref", "receipt"})
        entry.nonempty(evidence["owner_ref"])
        entry.nonempty(evidence["receipt"])
        entry.require(type(evidence["version"]) is int and evidence["version"] > 0, "invalid_version", "positive frozen requirement version required")
    commit, path = evidence["commit"], evidence["path"]
    entry.require(current["requirement"].get("path") == path, "source_changed", "entry must name the exact frozen path")
    entry.document_path(root, path)
    entry.require(isinstance(commit, str) and re.fullmatch("[0-9a-f]{40}", commit), "invalid_commit", "full commit required")
    entry.require(entry.git_text(root, "ls-tree", commit, "--", path).startswith("100644 "), "invalid_document", "committed requirement must be a regular document")
    data = entry.git(root, "show", commit + ":" + path).stdout
    entry.require(sha(data) == evidence["sha256"] == sha(entry.read_document(root, path)), "source_changed", "commit, source document and digest differ")
    blob = entry.git_text(root, "rev-parse", commit + ":" + path)
    entry.require(not original or blob == evidence["blob"], "source_changed", "source blob differs")
    entry.git(root, "merge-base", "--is-ancestor", commit, "HEAD")
    if original:
        return evidence
    return sealed({"protocol": PROTOCOL, "kind": "frozen", "source_kind": "frozen", "entry": current,
                   **evidence, "absolute_path": str(root / path), "blob": blob,
                   "requirement_identity": {k: evidence[k] for k in ("path", "sha256", "version")}})


def attached_snapshot(current, checkpoint_id):
    topic = entry.topic_read(current["repository"]["root"], current["requirement"]["attachment"])
    matches = [c for c in topic["checkpoints"] if c["checkpoint_id"] == checkpoint_id
               and c["topic_id"] == current["requirement"]["attachment"]["actor_topic_id"]]
    entry.require(len(matches) == 1, "checkpoint_missing", "exact source checkpoint required")
    return topic, matches[0]


def attached_result(current, checkpoint_id):
    topic, checkpoint = attached_snapshot(current, checkpoint_id)
    entry.require(checkpoint["state"] == "completed", "checkpoint_unfinished", "source checkpoint is not completed")
    paths = json.loads(checkpoint["paths_json"])
    entry.require(len(paths) == 1 and checkpoint["purpose"] == "stage-entry", "invalid_source", "one requirement stage-entry checkpoint required")
    path = paths[0]
    expected = json.loads(checkpoint["document_digests_json"])[path]
    entry.require(topic["pending_document_write_count"] == 0 and topic["topic_document_sha256"] == expected,
                  "source_changed", "topic has pending writes or differs from frozen checkpoint")
    identity = {"path": path, "sha256": expected,
                "version": json.loads(checkpoint["creation_result_json"])["record_revision"]}
    return {"protocol": PROTOCOL, "source_kind": "discussion", "checkpoint": checkpoint,
            "attachment": current["requirement"]["attachment"], "absolute_path": str(Path(current["repository"]["root"]) / path),
            "requirement_identity": identity,
            "commit": checkpoint["published_identity"] if checkpoint["storage_kind"] == "git" else None,
            "blob": json.loads(checkpoint["blob_ids_json"]).get(path)}


def attached(current, request):
    # Read-only prepare returns an intent; ledger mutations remain in write,
    # freeze and reconcile. The original ledger is the sole durable authority.
    operation = request["operation"]
    if operation == "verify":
        entry.fields(request, {"protocol", "operation", "entry", "checkpoint_id"})
        return attached_result(current, request["checkpoint_id"])
    if operation == "prepare":
        entry.fields(request, {"protocol", "operation", "entry", "purpose", "authorization"}, {"mutation", "base_ref"})
        entry.nonempty(request["authorization"])
        purpose = request["purpose"]
        entry.require(purpose in {"write", "freeze"} and current["entry"]["stage"] in {0, 1},
                      "invalid_operation", "only source Stages 0/1 may prepare discussion requirements")
        topic = current["requirement"]["topic"]
        payload = {"protocol_version": 1, "project_path": current["repository"]["root"],
                   **current["requirement"]["attachment"],
                   "expected_ledger_revision": topic["ledger_revision"],
                   "expected_topic_revision": topic["record_revision"], "idempotency_key": str(uuid.uuid4())}
        if purpose == "write":
            entry.require("mutation" in request and "base_ref" not in request, "invalid_request", "discussion write needs its semantic mutation")
            payload.update(operation="prepare-topic-update", mutation=request["mutation"])
        else:
            entry.require("base_ref" in request and "mutation" not in request, "invalid_request", "checkpoint needs its base ref")
            base = request["base_ref"]
            entry.nonempty(base)
            if current["repository"]["kind"] == "git":
                base = entry.git_text(current["repository"]["root"], "rev-parse", "--verify", base + "^{commit}")
            payload.update(operation="prepare-checkpoint", purpose="stage-entry", base_ref=base)
        return sealed({"protocol": PROTOCOL, "kind": "discussion-intent", "purpose": purpose,
                       "entry": current, "authorization": request["authorization"], "payload": payload,
                       "completion_key": str(uuid.uuid4())})
    entry.fields(request, {"protocol", "operation", "entry", "intent"})
    intent = request["intent"]
    validate_seal(intent)
    entry.require(intent.get("kind") == "discussion-intent" and intent.get("protocol") == PROTOCOL,
                  "invalid_intent", "original discussion preparation intent required")
    bind(current, intent["entry"])
    entry.require(current["requirement"]["attachment"] == intent["entry"]["requirement"]["attachment"],
                  "identity_changed", "discussion attachment changed")
    entry.require(current["entry"]["stage"] in {0, 1}, "invalid_operation", "discussion source is read-only at this stage")
    entry.require(operation in {intent["purpose"], "reconcile"}, "invalid_operation", "operation differs from intent")
    prepared = discussion_protocol.handle(intent["payload"])
    base = {"protocol_version": 1, "project_path": current["repository"]["root"],
            **current["requirement"]["attachment"]}
    if intent["purpose"] == "write":
        result = discussion_protocol.handle({**base, "operation": "apply-document-write",
                    "expected_ledger_revision": prepared["ledger_revision"],
                    "expected_topic_revision": prepared["record_revision"],
                    "idempotency_key": intent["completion_key"], "document_write_id": prepared["document_write_id"]})
        return {"protocol": PROTOCOL, "source_kind": "discussion", "discussion": result}
    checkpoint_id = prepared["checkpoint_id"]
    topic, checkpoint = attached_snapshot(current, checkpoint_id)
    if checkpoint["state"] == "completed":
        return attached_result(current, checkpoint_id)
    entry.require(checkpoint["state"] in {"prepared", "outcome-unknown"}, "checkpoint_unfinished", "checkpoint cannot be published")

    def transition(name):
        nonlocal topic, checkpoint
        token = entry.digest([intent["completion_key"], name, topic["ledger_revision"], checkpoint["record_revision"]])
        result = discussion_protocol.handle({**base, "operation": name,
                    "expected_ledger_revision": topic["ledger_revision"],
                    "expected_topic_revision": topic["record_revision"],
                    "idempotency_key": str(uuid.UUID(hex=token[:32], version=4)),
                    "checkpoint_id": checkpoint_id, "expected_checkpoint_revision": checkpoint["record_revision"]})
        topic, checkpoint = attached_snapshot(current, checkpoint_id)
        return result

    # A prepared CP may already have an object after a lost publication result.
    # The existing protocol reconciles objects before another publication.
    storage = "git" if checkpoint["storage_kind"] == "git" else "non-git"
    if prepared.get("idempotent_replay") or checkpoint["state"] == "outcome-unknown":
        if checkpoint["state"] == "prepared":
            transition("record-checkpoint-outcome-unknown")
        transition("reconcile-" + storage + "-checkpoint")
    if checkpoint["state"] != "completed":
        if operation == "reconcile":
            return {"protocol": PROTOCOL, "source_kind": "discussion", "state": "prepared", "intent": intent}
        transition("publish-" + storage + "-checkpoint")
    return attached_result(current, checkpoint_id)


def handle(request):
    entry.fields(request, {"protocol", "operation", "entry"},
                 {"purpose", "path", "version", "authorization", "content", "previous", "owned_paths", "intent", "evidence", "checkpoint_id", "mutation", "base_ref"})
    entry.require(request["protocol"] == PROTOCOL, "unsupported_protocol", "unsupported requirement protocol")
    entry.require(isinstance(request["entry"], dict), "invalid_request", "entry request must be an object")
    entry_request = dict(request["entry"])
    retained = request.get("intent", request.get("previous"))
    if retained is not None and "digest" in retained:
        validate_seal(retained)
        entry_request["pinned_packages"] = retained["entry"]["packages"]
    current = entry.resolve(entry_request)
    if current["requirement"]["kind"] == "discussion":
        return attached(current, request)
    operation = request["operation"]
    if operation == "prepare":
        return prepare(current, request)
    if operation == "verify":
        entry.fields(request, {"protocol", "operation", "entry", "evidence"})
        return verify(current, request["evidence"])
    entry.fields(request, {"protocol", "operation", "entry", "intent"})
    standalone(current)
    # Share the existing repository publication lock; this is not a new state
    # store. Native/user Git writers still require the existing sole-writer rule.
    lock_path = Path(current["repository"]["git_common_dir"]) / PUBLICATION_LOCK_FILENAME
    with os.fdopen(os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600), "a+b") as lock:
        _flock_with_timeout(lock)
        current = entry.resolve(entry_request)
        return mutate(current, request)


def mutate(current, request):
    operation = request["operation"]
    if operation == "write":
        return write(current, request["intent"])
    if operation in {"freeze", "reconcile"}:
        if operation == "reconcile" and request["intent"].get("purpose") == "write":
            return write(current, request["intent"])
        return freeze(current, request["intent"], reconcile=operation == "reconcile")
    raise entry.PreparationError("invalid_operation", "unknown preparation operation")


if __name__ == "__main__":
    sys.exit(entry.cli(handle))
