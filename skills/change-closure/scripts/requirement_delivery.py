#!/usr/bin/env python3
"""Deliver frozen Stage-1 Markdown; callers persist the transaction identity."""
from __future__ import annotations
import os
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import entry_prepare as entry
import requirement_prepare as requirement
import stage_handoff as handoff
import discussion_protocol
from discussion_core.state import _requirement_phase_attempts
from supervision_protocol import PUBLICATION_LOCK_FILENAME, _flock_with_timeout

PROTOCOL = "requirement-delivery-v1"


def user_work(root, paths):
    names = set()
    for args in (("diff", "--name-only", "-z"), ("diff", "--cached", "--name-only", "-z"),
                 ("ls-files", "--others", "--exclude-standard", "-z")):
        names.update(os.fsdecode(p) for p in entry.git(root, *args).stdout.split(b"\0") if p)
    result = {}
    for name in sorted(names - set(paths)):
        path = root / name
        if path.is_symlink():
            work = {"link": os.readlink(path)}
        elif path.is_file():
            hasher = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(65536), b""):
                    hasher.update(chunk)
            work = {"sha256": hasher.hexdigest(), "mode": path.stat().st_mode & 0o777}
        else:
            work = {"kind": "directory" if path.is_dir() else "absent"}
        result[name] = {"index": requirement.index_entries(root, [name]).decode(), "work": work}
    return result


def tree(root, revision, path):
    row = entry.git(root, "ls-tree", "-z", revision, "--", path).stdout
    if not row:
        return None
    mode, kind, blob = row.split(b"\t", 1)[0].decode().split()
    entry.require(mode == "100644" and kind == "blob", "invalid_document", "ordinary non-executable Markdown required")
    return {"blob": blob, "mode": mode}


def target(current, value):
    entry.fields(value, {"repository", "branch"})
    facts = entry.repository_facts(value["repository"])
    entry.require(facts["kind"] == "git" and facts["root"] == value["repository"] and facts["cwd"] == facts["root"]
                  and facts["branch"] == value["branch"] and value["branch"] is not None,
                  "target_mismatch", "explicit checked-out target branch required")
    entry.require(facts["git_common_dir"] == current["repository"]["git_common_dir"], "target_mismatch", "same Git repository required")
    return Path(facts["root"]), facts["head"]


def source(current, evidence, paths):
    entry.require(current["entry"]["stage"] == 1, "invalid_stage", "only Stage 1 delivers requirements")
    identity, commit = handoff.source(current, evidence, 2)
    entry.require(commit is not None, "git_required", "non-Git checkpoint cannot authorize Git delivery")
    if evidence.get("source_kind") == "discussion":
        entry.require(paths == [identity["path"]], "invalid_paths", "delivery is limited to the completed checkpoint path")
        topic = entry.topic_read(entry.discussion_root(current), current["requirement"]["attachment"])
        entry.require(topic["derived_gate_state"] == "open", "gate_closed", "discussion gate must be open for delivery")
        _, records = discussion_protocol._load_records(Path(current["discussion_project"]["ledger_path"]))
        attachment = current["requirement"]["attachment"]
        actor = attachment["actor_conversation_ref"]
        writers = _requirement_phase_attempts(records, attachment["actor_topic_id"])
        if writers:
            entry.require(len(writers) == 1 and writers[0]["carrier_ref"] == actor
                          and writers[0]["state"] in {"active", "completion-claimed", "completion-pending"},
                          "identity_mismatch", "delivery must retain the authorized phase carrier")
        else:
            entry.require(topic["current_phase"] == 1, "authority_missing", "Stage 1 at Phase 0 requires its active wrapper carrier")
            discussion_protocol._verify_topic_owner(records, attachment["actor_topic_id"], actor, operation="prepare-checkpoint")
    else:
        requirement.bind(current, evidence["entry"])
        entry.require(set(paths) <= set(evidence.get("owned_paths", [identity["path"]])), "invalid_paths", "paths exceed frozen ownership")
        entry.require(entry.git(current["repository"]["root"], "merge-base", "--is-ancestor", commit, "HEAD", check=False).returncode == 0,
                      "source_changed", "frozen source no longer belongs to source history")
    return identity, commit


def prepare(current, request):
    entry.fields(request, {"protocol", "operation", "entry", "requirement", "target", "owned_paths", "authorization"}, {"previous"})
    entry.nonempty(request["authorization"])
    paths = request["owned_paths"]
    entry.require(isinstance(paths, list) and 0 < len(paths) <= 32 and all(isinstance(p, str) and p.endswith(".md") for p in paths)
                  and paths == sorted(set(paths)), "invalid_paths", "sorted unique Markdown paths required")
    identity, commit = source(current, request["requirement"], paths)
    entry.require(identity["path"] in paths, "invalid_paths", "requirement must be owned")
    root, head = target(current, request["target"])
    previous = None
    if request.get("previous") is not None:
        previous = handle({"protocol": PROTOCOL, "operation": "verify", "entry": request["entry"], "result": request["previous"]})
        entry.require(previous["target"] == request["target"] and previous["paths"] == paths and previous["delivery"] is not None,
                      "delivery_unverified", "previous delivery proof must own this exact target and path set")
    bases = entry.git_text(root, "merge-base", "--all", head, commit).split()
    entry.require(len(bases) == 1, "baseline_ambiguous", "one common content baseline required")
    before, documents = {}, {}
    for path in paths:
        entry.document_path(root, path)
        final = tree(root, commit, path)
        entry.require(final is not None, "document_missing", "frozen owned document missing")
        original = tree(root, head, path)
        entry.require(original in (tree(root, bases[0], path), final), "delivery_conflict", "target document diverged from frozen source")
        data = entry.read_document(root, path)
        indexed = requirement.index_entries(root, [path])
        expected_index = b"" if original is None else ("100644 " + original["blob"] + " 0\t" + path + "\0").encode()
        expected_data = None if original is None else entry.git(root, "cat-file", "blob", original["blob"]).stdout
        source_root = entry.discussion_root(current) if request["requirement"].get("source_kind") == "discussion" else current["repository"]["root"]
        desired = entry.git(root, "cat-file", "blob", final["blob"]).stdout
        source_index = ("100644 " + final["blob"] + " 0\t" + path + "\0").encode()
        owned_source = str(root) == source_root and data == desired and indexed in {expected_index, source_index}
        entry.require(indexed == expected_index and data == expected_data or owned_source,
                      "delivery_conflict", "owned target index or document differs from HEAD")
        entry.require(entry.git(root, "check-ignore", "-q", "--", path, check=False).returncode == 1, "ignored_document", "ignored document cannot be delivered")
        if data is not None:
            entry.require(not (root / path).stat().st_mode & 0o111, "invalid_document", "executable document unsupported")
        before[path] = {"tree": original, "index": indexed.decode(), "sha256": requirement.sha(data)}
        documents[path] = final
    return requirement.sealed({"protocol": PROTOCOL, "kind": "intent", "operation_id": str(uuid.uuid4()), "entry": current,
        "requirement": request["requirement"], "requirement_identity": identity, "source_commit": commit,
        "target": request["target"], "target_head": head, "baseline": bases[0], "paths": paths, "documents": documents,
        "before": before, "unrelated": requirement.unrelated(root, paths), "user_work": user_work(root, paths),
        "authorization": request["authorization"], "previous": previous})


def validate(current, intent):
    requirement.validate_seal(intent)
    entry.fields(intent, {"protocol", "kind", "operation_id", "entry", "requirement", "requirement_identity",
        "source_commit", "target", "target_head", "baseline", "paths", "documents", "before", "unrelated",
        "user_work", "authorization", "previous", "digest"})
    entry.require(intent.get("protocol") == PROTOCOL and intent.get("kind") == "intent", "invalid_intent", "original delivery intent required")
    operation_id = intent["operation_id"]
    entry.require(isinstance(operation_id, str) and str(uuid.UUID(operation_id)) == operation_id
                  and uuid.UUID(operation_id).version == 4, "invalid_intent", "original UUIDv4 operation identity required")
    entry.nonempty(intent["authorization"])
    paths = intent["paths"]
    entry.require(isinstance(paths, list) and 0 < len(paths) <= 32
                  and all(isinstance(p, str) and p.endswith(".md") for p in paths)
                  and paths == sorted(set(paths)), "invalid_paths", "exact sorted owned Markdown paths required")
    requirement.bind(current, intent["entry"])
    identity, commit = source(current, intent["requirement"], paths)
    entry.require(identity == intent["requirement_identity"] and commit == intent["source_commit"], "source_changed", "frozen source changed")
    root, head = target(current, intent["target"])
    handoff.commit(intent["target_head"])
    handoff.commit(intent["baseline"])
    entry.require(isinstance(intent["documents"], dict) and set(intent["documents"]) == set(paths)
                  and isinstance(intent["before"], dict) and set(intent["before"]) == set(paths),
                  "invalid_intent", "complete original document states required")
    for path in paths:
        entry.document_path(root, path)
        entry.require(intent["documents"][path] == tree(root, commit, path), "invalid_intent", "intent document differs from frozen Git object")
        entry.fields(intent["before"][path], {"tree", "index", "sha256"})
        entry.require(intent["before"][path]["tree"] == tree(root, intent["target_head"], path)
                      and isinstance(intent["before"][path]["index"], str),
                      "invalid_intent", "original target state differs from Git evidence")
    entry.fields(intent["unrelated"], {"index", "files"})
    entry.require(isinstance(intent["user_work"], dict), "invalid_intent", "original user-work evidence required")
    return root, head


def complete(current, intent, commit, proof):
    try:
        return verified_result(current, intent, commit, proof)
    except entry.ERROR_TYPES as error:
        evidence = {"kind": "verified-delivery-object", "operation_id": intent["operation_id"], "intent_digest": intent["digest"],
                    "commit": commit, "paths": intent["paths"], "downstream_ready": False}
        raise entry.PreparationError(getattr(error, "code", "delivery_failed"), entry.error_message(error),
                                     completed_evidence=[evidence]) from error


def verified_result(current, intent, commit, proof):
    root, head = target(current, intent["target"])
    checked = handoff.delivery({"target": intent["target"], "delivery": proof, "requirement": intent["requirement"]},
                              current, intent["requirement_identity"], intent["source_commit"])
    for path in intent["paths"]:
        entry.require(tree(root, head, path) == intent["documents"][path], "delivery_pending", "target document differs from source")
        entry.require(entry.read_document(root, path) == entry.git(root, "cat-file", "blob", intent["documents"][path]["blob"]).stdout
                      and not (root / path).stat().st_mode & 0o111,
                      "delivery_pending", "target working document differs from source")
        expected_index = "100644 " + intent["documents"][path]["blob"] + " 0\t" + path + "\0"
        entry.require(requirement.index_entries(root, [path]).decode() == expected_index,
                      "delivery_pending", "target index differs from delivered document")
    # A later ordinary target commit may change unrelated committed files. The
    # original transaction checks user work immediately after its own commit.
    if head == commit:
        entry.require(requirement.unrelated(root, intent["paths"]) == intent["unrelated"], "workspace_changed", "unrelated workspace or index changed")
    entry.require(user_work(root, intent["paths"]) == intent["user_work"], "workspace_changed", "unrelated user work changed")
    return requirement.sealed({"protocol": PROTOCOL, "kind": "delivered", "state": "verified", "intent": intent,
        "operation_id": intent["operation_id"], "requirement_identity": intent["requirement_identity"], "paths": intent["paths"],
        "target": intent["target"], "delivery": proof, **checked})


def candidates(root, intent):
    objects = entry.git(root, "cat-file", "--batch-all-objects", "--batch-check=%(objectname) %(objecttype)").stdout
    commits = [row.split()[0] for row in objects.splitlines() if row.endswith(b" commit")]
    if not commits:
        return []
    trailer = "Codex-Requirement-Delivery: " + intent["digest"]
    selected = entry.git(root, "log", "--no-walk", "--stdin", "--format=%H", "--fixed-strings", "--grep=" + trailer,
                         data=b"\n".join(commits) + b"\n").stdout.decode().splitlines()
    for commit in selected:
        entry.require(trailer in entry.git_text(root, "show", "-s", "--format=%B", commit).splitlines(),
                      "delivery_unverified", "delivery trailer is not exact")
        entry.require(entry.git_text(root, "show", "-s", "--format=%P", commit).split() == [intent["target_head"]],
                      "delivery_unverified", "delivery parent differs from intent")
        changed = set(filter(None, entry.git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", commit).stdout.decode().split("\0")))
        entry.require(changed and changed <= set(intent["paths"]), "delivery_unverified", "delivery changes escape scope")
        for path in intent["paths"]:
            entry.require(tree(root, commit, path) == intent["documents"][path], "delivery_unverified", "delivery object differs from frozen document")
    return selected


def proof_for(intent, commit):
    return {"commit": commit, "paths": intent["paths"], "receipt": "requirement-delivery:" + intent["operation_id"] + ":" + intent["digest"]}


def check_workspace(root, intent):
    for path in intent["paths"]:
        data = entry.read_document(root, path)
        desired = entry.git(root, "cat-file", "blob", intent["documents"][path]["blob"]).stdout
        entry.require(requirement.sha(data) in {intent["before"][path]["sha256"], requirement.sha(desired)},
                      "delivery_conflict", "owned document differs from original and expected states")
        indexed = requirement.index_entries(root, [path]).decode()
        expected = "100644 " + intent["documents"][path]["blob"] + " 0\t" + path + "\0"
        entry.require(indexed in {intent["before"][path]["index"], expected}, "delivery_conflict", "owned index differs from original and expected states")
        if data is not None:
            entry.require(not (root / path).stat().st_mode & 0o111, "invalid_document", "executable document unsupported")
        entry.require(entry.git(root, "check-ignore", "-q", "--", path, check=False).returncode == 1,
                      "ignored_document", "ignored document cannot be delivered")
    entry.require(requirement.unrelated(root, intent["paths"]) == intent["unrelated"], "workspace_changed", "unrelated workspace or index changed")
    entry.require(user_work(root, intent["paths"]) == intent["user_work"], "workspace_changed", "unrelated user work changed")


def deliver(current, intent, *, readonly=False):
    root, head = validate(current, intent)
    source_commit = intent["source_commit"]
    if entry.git(root, "merge-base", "--is-ancestor", source_commit, head, check=False).returncode == 0:
        return complete(current, intent, source_commit, None)
    matches = candidates(root, intent)
    entry.require(len(matches) <= 1, "delivery_ambiguous", "multiple delivery objects match original intent")
    if matches:
        if entry.git(root, "merge-base", "--is-ancestor", matches[0], head, check=False).returncode:
            raise entry.PreparationError("delivery_detached", "original delivery exists outside target history",
                completed_evidence=[{"kind": "verified-delivery-object", "commit": matches[0], "operation_id": intent["operation_id"],
                                     "intent_digest": intent["digest"], "paths": intent["paths"], "downstream_ready": False}])
        return complete(current, intent, matches[0], proof_for(intent, matches[0]))
    if intent.get("previous") is not None:
        previous = intent["previous"]
        validate(current, previous["intent"])
        observed = deliver(current, previous["intent"], readonly=True)
        entry.require(observed.get("state") == "verified" and observed["delivery"] == previous["delivery"],
                      "delivery_unverified", "retained previous proof no longer verifies")
        return complete(current, intent, observed["delivery_commit"], observed["delivery"])
    if head != intent["target_head"]:
        entry.require(entry.git(root, "merge-base", "--is-ancestor", intent["target_head"], head, check=False).returncode == 0,
                      "target_changed", "target moved outside original lineage")
        for path in intent["paths"]:
            entry.require(tree(root, head, path) == intent["before"][path]["tree"]
                          and requirement.index_entries(root, [path]).decode() == intent["before"][path]["index"]
                          and requirement.sha(entry.read_document(root, path)) == intent["before"][path]["sha256"],
                          "target_changed", "target advanced after owned path changes or partial delivery")
        return {"protocol": PROTOCOL, "state": "refresh-required", "intent": intent, "target_head": head}
    check_workspace(root, intent)
    if readonly:
        return {"protocol": PROTOCOL, "state": "prepared", "intent": intent}
    changed = [p for p in intent["paths"] if intent["before"][p]["tree"] != intent["documents"][p]]
    entry.require(changed, "delivery_unverified", "identical bytes require original delivery evidence")
    for path in changed:
        destination = entry.document_path(root, path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        data = entry.git(root, "cat-file", "blob", intent["documents"][path]["blob"]).stdout
        fd, temporary = tempfile.mkstemp(prefix=".requirement-delivery-", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o644)
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    entry.git(root, "add", "--", *intent["paths"])
    for path in intent["paths"]:
        entry.require(entry.git(root, "show", ":" + path).stdout == entry.git(root, "cat-file", "blob", intent["documents"][path]["blob"]).stdout,
                      "filtered_bytes_changed", "Git filter changed frozen bytes")
    message = "docs: deliver frozen requirement\n\nCodex-Requirement-Delivery: " + intent["digest"] + "\n"
    entry.require(entry.git_text(root, "rev-parse", "HEAD") == intent["target_head"], "target_changed", "target changed before commit")
    command = entry.git(root, "commit", "--only", "-m", message, "--", *intent["paths"], check=False)
    commit = entry.git_text(root, "rev-parse", "HEAD")
    entry.require(candidates(root, intent) == [commit], "delivery_unverified", "commit failed or differs from original intent")
    entry.require(command.returncode == 0, "delivery_outcome_unknown", "commit exists but command failed; reconcile original intent")
    proof = proof_for(intent, commit)
    return complete(current, intent, commit, proof)


def handle(request):
    entry.fields(request, {"protocol", "operation", "entry"}, {"requirement", "target", "owned_paths", "authorization", "intent", "result", "previous"})
    entry.require(request["protocol"] == PROTOCOL, "unsupported_protocol", "unsupported delivery protocol")
    retained = request.get("intent")
    if request["operation"] == "verify":
        entry.fields(request, {"protocol", "operation", "entry", "result"})
        requirement.validate_seal(request["result"])
        retained = request["result"]["intent"]
    entry_request = dict(request["entry"])
    frozen = request.get("requirement")
    if request["operation"] == "prepare" and isinstance(frozen, dict) and frozen.get("kind") == "frozen":
        requirement.validate_seal(frozen)
        entry_request["pinned_packages"] = frozen["entry"]["packages"]
    if retained is not None:
        requirement.validate_seal(retained)
        entry_request["pinned_packages"] = retained["entry"]["packages"]
    current = entry.resolve(entry_request)
    if request["operation"] == "prepare":
        return prepare(current, request)
    if request["operation"] == "verify":
        validate(current, retained)
        result = deliver(current, retained, readonly=True)
        entry.require(result.get("state") == "verified" and result["delivery"] == request["result"]["delivery"],
                      "delivery_unverified", "original delivery no longer verifies")
        return result
    entry.fields(request, {"protocol", "operation", "entry", "intent"})
    entry.require(request["operation"] in {"deliver", "reconcile"}, "invalid_operation", "unsupported delivery action")
    root, _ = validate(current, request["intent"])
    lock_path = Path(current["repository"]["git_common_dir"]) / PUBLICATION_LOCK_FILENAME
    with os.fdopen(os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600), "a+b") as lock:
        _flock_with_timeout(lock)
        current = entry.resolve(entry_request)
        return deliver(current, request["intent"], readonly=request["operation"] == "reconcile")


if __name__ == "__main__":
    try:
        sys.exit(entry.cli(handle))
    except RecursionError:
        print(json.dumps({"ok": False, "error": {"code": "invalid_request", "operation": None,
            "message": "Delivery request nesting exceeds the supported JSON boundary.", "completed_evidence": [],
            "recovery": "Retain the original checkpoint and reconcile its bounded request."}}))
        sys.exit(1)
