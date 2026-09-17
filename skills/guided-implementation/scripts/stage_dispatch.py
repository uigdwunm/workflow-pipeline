#!/usr/bin/env python3
"""One launch/receive transaction, persisted by the existing checkpoint owner.

The caller authenticates native/task receipts. This module never invokes a
native tool, interprets an arbitrary receipt string as authentication, or waits.
"""
from __future__ import annotations

import copy
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import entry_prepare as entry
import discussion_protocol
import stage_handoff as handoff
import workflow_control as control
import workflow_control_git as control_git

PROTOCOL = handoff.PROTOCOL
require = handoff.require


def checkpoint(port, request, action, evidence):
    """Apply the existing authority, never a transport-record shadow ledger.

Discussion mutation envelopes are supplied from the controller's saved intent;
their exact idempotency key and revisions must be retained on an unknown result.
"""
    entry.fields(port, {"context", "discussion"})
    context = port["context"]
    control.validate_context(context)
    require(context["controller_ref"] == request["controller_ref"], "identity_mismatch", "wrong checkpoint controller")
    if port["discussion"] is not None:
        envelope = port["discussion"]
        entry.fields(envelope, {"protocol_version", "project_path", "project_id", "tree_id", "actor_topic_id",
                               "actor_conversation_ref", "expected_ledger_revision", "expected_topic_revision", "idempotency_key"})
        require(envelope["actor_conversation_ref"] == context["controller_ref"] and
                envelope["actor_topic_id"] == context["topic_ref"], "identity_mismatch", "discussion checkpoint owner differs")
        actual = discussion_protocol.handle({**envelope, "operation": "workflow-control", "action": action, "evidence": evidence})
        return actual["control"], actual
    # Downstream Phase Runs own lifecycle, while execution control stays in the
    # caller checkpoint. Attached dedicated-stage mutations must use the ledger.
    require(context["topic_ref"] is None or context["stage"] >= 2, "authority_missing", "attached dedicated control requires its ledger adapter")
    payload = {"schema_version": 1, "actor_ref": context["controller_ref"], "context": context,
               "action": action, "evidence": evidence}
    if context["stage"] >= 2 or action == "receive":
        repository = request["binding"]["repository"] if action == "closure-result" else request["expected_entry"]["repository"]["root"]
        result = control_git.verified_transition({"repository": repository,
                   "baseline": request["scope"]["baseline"], "request": payload})
    else:
        result = control.transition(payload)
    return result, None


def port_result(result, applied):
    return {"context": result["context"], "discussion_receipt": applied}


def prepared(request):
    entry.fields(request, {"protocol", "operation", "handoff", "control"})
    saved = handoff.verify(request["handoff"])
    actor = saved["expected_entry"]["actor"]
    require(actor["role"] in {"controller", "scripted-carrier", "implementation-dispatcher"},
            "role_mismatch", "only the authorized controller/carrier may prepare dispatch")
    require(actor["role"] != "implementation-dispatcher" or saved["role"] == "execution-agent",
            "role_mismatch", "dispatcher may only allocate execution agents")
    context = request["control"]["context"]
    control.validate_context(context)
    selected = context
    if saved["stage"] < 2:
        plan_id = saved["authorization"].get("control_plan_id")
        require(plan_id is not None or context.get("successor_control") is None,
                "plan_ambiguous", "two control slots require the handoff's exact control_plan_id")
        selector = {"control_plan_id": plan_id} if plan_id is not None else {}
        selected = control.selected_control(context, selector)
    require(selected["stage"] == saved["stage"] and selected["requirement_identity"] == saved["requirement_identity"],
            "source_changed", "control stage or requirement differs from the prepared input")
    require(context["controller_ref"] == saved["controller_ref"], "identity_mismatch", "control owner differs")
    role, scope = saved["role"], saved["scope"]
    if saved["stage"] < 2:
        # User choice and attached entry authority are prepared/decided by the
        # existing controller UI before this mechanical launch boundary.
        progress = selected["handoff_progress"]
        require(progress and progress["state"] == "creation-pending" and selected["carrier"]["ref"] is None,
                "authorization_missing", "a confirmed dedicated plan is required before launch")
        plan = progress["plan"]
        require(plan["requirement_identity"] == saved["requirement_identity"] and plan["configuration"] == saved["selection"],
                "authorization_changed", "confirmed dedicated plan differs from input or configuration")
        attempt = selected["carrier"]["attempt"]
        result, applied = checkpoint(request["control"], saved, "reserve-launch", {"attempt": attempt, "input_digest": saved["digest"]})
        if result.get("acknowledged"):
            return {"status": "unknown", "checkpoint": port_result(result, applied), "downstream_ready": False,
                    "next_action": "recover-saved-request-and-lookup-exact-attempt", "attempt": attempt}
    else:
        evidence = {"configuration": saved["configuration"]}
        if role == "solution-designer":
            action = "start-design"
            evidence.update(design_input=saved["digest"], binding=saved["binding"],
                            allowed_paths=scope["owned_paths"], protected_paths=scope["protected_paths"])
        elif role == "implementation-dispatcher":
            action = "start-dispatch"
            evidence.update(binding=saved["binding"], binding_verified=True, allowed_paths=scope["implementation_paths"],
                            protected_paths=scope["protected_paths"], authority_digest=saved["authorization"]["scope_digest"],
                            testing_basis=saved["semantic"]["testing_basis"])
        elif role == "execution-agent":
            action = "plan-execution"
            evidence.update(task_id=str(uuid.uuid4()), paths=scope["owned_paths"], read_only=scope["protected_paths"],
                            behavior=saved["semantic"]["objective"], tests=saved["semantic"]["completion_criteria"], git_operations=[])
        else:
            action = "start-closure"
            prior = saved["predecessor"]
            evidence.update(candidate=prior["payload"]["candidate_commit"], dispatcher_ref=prior["role_ref"],
                            review=prior["payload"]["review"], verification=prior["payload"]["verification"], binding=saved["binding"],
                            implementation_paths=scope["implementation_paths"], closure_paths=scope["closure_paths"], protected_paths=scope["protected_paths"])
        result, applied = checkpoint(request["control"], saved, action, evidence)
        attempt = result.get("allocation_digest", result["context"]["carrier"]["attempt"])
    payload = handoff.render(saved)["payload"]
    native = saved["stage"] >= 2
    launch = handoff.seal({"kind": "native" if native else "visible-task", "role": role,
                          "controller_ref": saved["controller_ref"], "attempt": attempt,
                          "input_digest": saved["digest"], "configuration": saved["selection"], "payload": payload})
    if not native:
        launch = handoff.seal({**handoff.unseal(launch), "dedicated_plan": plan})
    record = handoff.seal({"protocol": PROTOCOL, "kind": "dispatch", "handoff": saved, "request": launch,
                          "receipts": [], "delivery": None, "acceptance": None, "status": "prepared"})
    return {"status": "prepared", "record": record, "checkpoint": port_result(result, applied),
            "host_call": launch, "downstream_ready": False,
            "required_order": ["persist-record-and-checkpoint", "verify-input-and-target-configuration", "invoke-host-once", "save-receipt", "bind"]}


def record_for(request):
    record = handoff.unseal(request["record"])
    entry.fields(record, {"protocol", "kind", "handoff", "request", "receipts", "delivery", "acceptance", "status"})
    require(record["protocol"] == PROTOCOL and record["kind"] == "dispatch", "invalid_evidence", "dispatch record required")
    launch = handoff.unseal(record["request"])
    handoff.unseal(record["handoff"])
    require(launch["input_digest"] == record["handoff"]["digest"], "identity_mismatch", "request belongs to another input")
    control.validate_context(request["control"]["context"])
    require(request["control"]["context"]["controller_ref"] == launch["controller_ref"], "identity_mismatch", "checkpoint owner changed")
    if request["control"]["discussion"] is not None:
        envelope = request["control"]["discussion"]
        base = {k: envelope[k] for k in ("protocol_version", "project_path", "project_id", "tree_id", "actor_topic_id", "actor_conversation_ref")}
        current = discussion_protocol.handle({**base, "operation": "read-topic"})
        require(current["workflow_control"] is not None, "authority_missing", "discussion has no workflow control checkpoint")
        control.validate_context(current["workflow_control"])
        require(current["workflow_control"]["controller_ref"] == launch["controller_ref"], "identity_mismatch", "ledger controller changed")
        request["control"] = {**request["control"], "context": current["workflow_control"]}
    return record


def matching(record, context, *, reconcile_unbound=False):
    launch = record["request"]
    selected = control.selected_control(context, {"attempt": launch["attempt"]})
    if launch["role"] == "execution-agent":
        progress = selected["handoff_progress"] or {}
        matches = [item for item in progress.get("executions", []) if item["allocation_digest"] == launch["attempt"]]
        require(len(matches) == 1, "attempt_mismatch", "allocation is no longer eligible")
        item = matches[0]
        cancelled_lookup = (reconcile_unbound and progress["state"] == "cancelled" and item["agent_ref"] is None
                            and (item["state"] == "dispatch-pending" or item["state"] == "cancelled" and item["stopped"] is True))
        require(progress["state"] == "implementing" or cancelled_lookup, "attempt_mismatch", "allocation is no longer eligible")
        return item["agent_ref"], item["state"], item
    carrier = selected["carrier"]
    require(carrier is not None and carrier["attempt"] == launch["attempt"], "attempt_mismatch", "checkpoint belongs to another attempt")
    return carrier["ref"], selected["handoff_progress"]["state"], selected["handoff_progress"]


def receipt_for(record, receipt, event):
    entry.fields(receipt, {"adapter", "receipt_ref", "request_digest", "attempt", "role", "event", "status", "ref", "pending_id", "configuration", "raw"})
    entry.nonempty(receipt["adapter"]); entry.nonempty(receipt["receipt_ref"])
    require(isinstance(receipt["raw"], dict) and bool(receipt["raw"]), "host_evidence_missing", "retain the original tool response")
    launch = record["request"]
    if receipt["ref"] is not None:
        entry.fields(receipt["configuration"], {"model", "effort"})
        require(receipt["configuration"] == {k: launch["configuration"][k] for k in ("model", "effort")},
                "configuration_changed", "host configuration differs from the frozen selection; retain the created identity for recovery")
    else:
        require(receipt["configuration"] is None, "invalid_request", "unresolved identity cannot claim effective configuration")
    require(receipt["request_digest"] == launch["digest"] and receipt["attempt"] == launch["attempt"] and receipt["role"] == launch["role"],
            "identity_mismatch", "host receipt does not match this request and attempt")
    require(receipt["event"] in event, "host_evidence_missing", "wrong host observation type")
    for field in ("ref", "pending_id"):
        if receipt[field] is not None:
            entry.nonempty(receipt[field])


def bind_discussion(port, record, receipt):
    """Bind the exact already-authorized 0/1 carrier; never activate a phase."""
    saved = record["handoff"]
    if saved["stage"] == 2 and saved["entry"]["source"]["kind"] == "discussion":
        phase = handoff.phase_evidence(saved)
        known = phase["attempt"].get("carrier_ref")
        require(known is None or known == receipt["ref"], "identity_mismatch", "wrapper already binds another designer")
        if known is not None:
            require(phase["attempt"].get("authorization") is True, "authority_missing", "wrapper carrier authorization was revoked")
            return port, None
        binding = saved["authorization"]["phase"]
        applied = discussion_protocol.handle({"protocol_version": 1, "operation": "authorize-phase-carrier",
                     "project_path": saved["expected_entry"]["repository"]["root"], **saved["entry"]["source"]["attachment"],
                     "phase_run_id": binding["run_id"], "attempt_id": binding["attempt_id"], "carrier_ref": receipt["ref"],
                     "expected_ledger_revision": phase["topic"]["ledger_revision"], "expected_topic_revision": phase["topic"]["record_revision"],
                     "idempotency_key": str(uuid.UUID(hex=entry.digest([record["request"]["digest"], "authorize-designer"])[:32], version=4))})
        return port, applied
    if port["discussion"] is None or record["request"]["kind"] != "visible-task":
        return port, None
    envelope = port["discussion"]
    authority = record["request"]["dedicated_plan"]["entry_authority"]
    require(authority["kind"] in {"dedicated-stage", "wrapper-phase-run"}, "authority_missing", "attached launch requires the original typed authority")
    base = {k: envelope[k] for k in ("protocol_version", "project_path", "project_id", "tree_id", "actor_topic_id", "actor_conversation_ref")}
    if authority["kind"] == "dedicated-stage":
        read = discussion_protocol.handle({**base, "operation": "read-handoff", "handoff_id": authority["run_id"]})
        attempts = read["attempts"]
    else:
        read = discussion_protocol.handle({**base, "operation": "read-phase-run", "phase_run_id": authority["run_id"]})
        attempts = read["phase_run"]["attempts"]
    matches = [item for item in attempts if item["attempt_id"] == authority["attempt_id"]]
    require(len(matches) == 1, "attempt_mismatch", "original discussion attempt is missing")
    attempt = matches[0]
    field = "conversation_ref" if authority["kind"] == "dedicated-stage" else "carrier_ref"
    known = attempt.get(field)
    require(known is None or known == receipt["ref"], "identity_mismatch", "discussion already binds another identity")
    applied = None
    if known is None:
        if authority["kind"] == "dedicated-stage":
            operation = "bind-handoff"
            values = {"handoff_id": authority["run_id"], "attempt_id": authority["attempt_id"], "conversation_ref": receipt["ref"],
                      "verified_identity": {"project_id": authority["project_id"], "tree_id": authority["tree_id"],
                          "topic_id": authority["topic_id"], "handoff_id": authority["run_id"],
                          "attempt_id": authority["attempt_id"], "payload_sha256": attempt["payload_sha256"]}}
        else:
            operation = "authorize-phase-carrier"
            values = {"phase_run_id": authority["run_id"], "attempt_id": authority["attempt_id"], "carrier_ref": receipt["ref"]}
        applied = discussion_protocol.handle({**envelope, "operation": operation, **values})
    current = discussion_protocol.handle({**base, "operation": "read-topic"})
    # This is the second operation of one saved bind intent. Its key is distinct
    # from the lifecycle binding key; exact carrier readback handles lost output.
    updated = {**envelope, "expected_ledger_revision": current["ledger_revision"], "expected_topic_revision": current["record_revision"],
               "idempotency_key": str(uuid.UUID(hex=entry.digest([envelope["idempotency_key"], "creation-result", record["request"]["digest"]])[:32], version=4))}
    return {**port, "discussion": updated}, applied


def bound(request):
    entry.fields(request, {"protocol", "operation", "record", "control", "receipt"})
    record = record_for(request)
    receipt = request["receipt"]
    receipt_for(record, receipt, {"create"} if request["operation"] == "bind" else {"lookup"})
    require(receipt["status"] in {"ready", "pending", "unknown", "not-created"}, "invalid_request", "unknown launch outcome")
    require((receipt["status"] == "ready") == (receipt["ref"] is not None), "identity_mismatch", "only ready results carry usable identities")
    require(receipt["status"] != "ready" or receipt["pending_id"] is None, "identity_mismatch", "pending identity cannot be bound")
    reconcile_unbound = (request["operation"] == "reconcile" and record["request"]["role"] == "execution-agent"
                         and receipt["status"] in {"unknown", "pending", "not-created"})
    known, state, item = matching(record, request["control"]["context"], reconcile_unbound=reconcile_unbound)
    if reconcile_unbound and receipt["status"] == "not-created" and known is None and state == "cancelled" and item["stopped"] is True:
        record["status"] = "not-created"
        if receipt not in record["receipts"]:
            record["receipts"].append(copy.deepcopy(receipt))
        return {"status": "not-created", "record": handoff.seal(record), "acknowledged": True,
                "checkpoint": {"context": request["control"]["context"], "discussion_receipt": None}, "downstream_ready": False}
    if receipt in record["receipts"]:
        require(known == receipt["ref"] or known is None, "identity_mismatch", "replayed receipt conflicts with checkpoint")
        return {"status": record["status"], "record": request["record"], "acknowledged": True, "downstream_ready": False}
    require(state not in {"cancelled", "creation-failed", "accepted"}, "attempt_mismatch", "late receipt cannot revive an ended attempt")
    if known is not None:
        require(receipt["status"] == "ready" and known == receipt["ref"], "identity_mismatch", "bound identity cannot be replaced")
        record["receipts"].append(receipt)
        record["status"] = "bound"
        return {"status": "bound", "record": handoff.seal(record), "acknowledged": True,
                "checkpoint": {"context": request["control"]["context"], "discussion_receipt": None}, "downstream_ready": False}
    if record["receipts"]:
        require(request["operation"] == "reconcile", "outcome_unknown", "read back the original launch before retrying")
        pending = {r["pending_id"] for r in record["receipts"] if r["pending_id"] is not None}
        if receipt["pending_id"] is not None:
            require(not pending or receipt["pending_id"] in pending, "identity_mismatch", "pending identity changed")
    launch = record["request"]
    ready = receipt["status"] == "ready"
    if launch["kind"] == "visible-task":
        action = "creation-result"
        evidence = {"attempt": launch["attempt"], "ref": receipt["ref"],
                    "status": "failed" if receipt["status"] == "not-created" else receipt["status"]}
    elif launch["role"] == "execution-agent":
        action = "assign" if ready else "execution-dispatch-result"
        evidence = {"task_id": item["task_id"], "allocation_digest": launch["attempt"]}
        evidence.update({"agent_ref": receipt["ref"]} if ready else {"status": "not-created" if receipt["status"] == "not-created" else "unknown"})
    elif ready:
        action = {"solution-designer": "designer-bound", "implementation-dispatcher": "dispatcher-bound", "closure-agent": "closure-bound"}[launch["role"]]
        evidence = {"ref": receipt["ref"], "attempt": launch["attempt"]}
    else:
        action = "native-dispatch-result"
        evidence = {"attempt": launch["attempt"], "status": "not-created" if receipt["status"] == "not-created" else "unknown"}
    # No freshness check here: creation already happened. Save/bind its identity
    # even if source bytes changed; subsequent work must separately reverify.
    port, binding_receipt = request["control"], None
    try:
        if ready:
            port, binding_receipt = bind_discussion(port, record, receipt)
        result, applied = checkpoint(port, record["handoff"], action, evidence)
    except entry.ERROR_TYPES as error:
        raise entry.PreparationError("binding_incomplete", "Host creation evidence retained; reconcile this exact attempt, never create again.",
              completed_evidence=[{"kind": "host-creation", "receipt": receipt, "binding_receipt": binding_receipt, "downstream_ready": False}]) from error
    record["receipts"].append(copy.deepcopy(receipt))
    record["status"] = "bound" if ready else receipt["status"]
    return {"status": record["status"], "record": handoff.seal(record), "checkpoint": port_result(result, applied), "binding_receipt": binding_receipt,
            "downstream_ready": False, "next_action": "receive" if ready else "stop" if receipt["status"] == "not-created" else "lookup-exact-request",
            "lookup": None if ready else {"request_digest": launch["digest"], "attempt": launch["attempt"], "pending_id": receipt["pending_id"]}}


def received(request):
    entry.fields(request, {"protocol", "operation", "record", "control", "receipt", "result"})
    record = record_for(request)
    receipt_for(record, request["receipt"], {"result"})
    ref, state, item = matching(record, request["control"]["context"])
    require(ref is not None and ref == request["receipt"]["ref"], "identity_mismatch", "result must come from the bound identity")
    message = request["result"]
    entry.fields(message, {"delivery_id", "status", "payload"})
    entry.nonempty(message["delivery_id"])
    require(message["status"] in {"completed", "continue", "needs_input", "technical_error"}, "invalid_result", "unknown business result")
    claim = handoff.seal({"message": message, "role_ref": ref, "request_digest": record["request"]["digest"]})
    if record["delivery"] is not None:
        require(record["delivery"] == claim, "delivery_conflict", "received delivery cannot be replaced")
        return {"status": record["status"], "record": request["record"], "acknowledged": True, "downstream_ready": record["status"] == "accepted"}
    require(state not in {"cancelled", "creation-failed"}, "attempt_mismatch", "result attempt ended")
    if message["status"] != "completed":
        return {"status": message["status"], "record": request["record"], "observation": request["receipt"], "result": message, "downstream_ready": False}
    require(request["receipt"]["status"] == "stopped", "host_evidence_missing", "completed work requires authenticated stopped evidence, not idle/turn completion")
    saved = record["handoff"]
    stage, payload = saved["stage"], message["payload"]
    if stage >= 2:
        handoff.phase_evidence(saved, role_ref=ref, receiving=True)
    root = saved["expected_entry"]["repository"]["root"]
    if stage != 4:
        current = handoff.refresh(saved["entry"], saved["expected_entry"], after_work=True)
        if stage >= 2:
            handoff.source(current, saved["requirement"], stage)
    if saved["role"] == "execution-agent":
        entry.fields(payload, {"changed_paths", "file_hashes", "tests"})
        evidence = {**payload, "agent_ref": ref, "stopped": True, "git_unchanged": True}
        action = "execution-result"
    else:
        handoff.verify_result(stage, payload, saved["binding"], saved["scope"], root, ref)
        if stage < 2:
            identity, revision = handoff.source(current, payload["requirement"], stage)
            require(identity["path"] == saved["requirement_identity"]["path"] and identity["version"] >= saved["requirement_identity"]["version"],
                    "source_changed", "requirement path changed or version regressed")
            if stage == 1:
                require(payload["requirement"].get("kind") == "frozen" or payload["requirement"].get("source_kind") == "discussion",
                        "result_incomplete", "Stage 1 completion requires a successful freeze")
                handoff.delivery({**saved, "requirement": payload["requirement"], "delivery": payload["delivery"]}, current, identity, revision)
            action = "receive"
            evidence = {"delivery_id": message["delivery_id"], "source_ref": ref, "attempt": record["request"]["attempt"],
                        "requirement_identity": identity, "commit": revision, "verified_commit_hash": identity["sha256"]}
        elif stage == 2:
            action, evidence = "design-result", {"ref": ref, "result_digest": claim["digest"]}
        elif stage == 3:
            action = "candidate"
            evidence = {"dispatcher_ref": ref, "commit": payload["candidate_commit"], "clean": True,
                        "changed_paths": [], "file_hashes": {}, "tests": payload["checks"], "binding": saved["binding"]}
        else:
            require(payload["candidate_commit"] == saved["predecessor"]["payload"]["candidate_commit"], "source_changed", "closure candidate differs from accepted input")
            action = "closure-result"
            evidence = {"ref": ref, "candidate": payload["candidate_commit"], "merge": payload["merge_commit"],
                        "ancestor_verified": True, "changed_paths": payload["changed_paths"], "binding": saved["binding"],
                        "checks": payload["checks"], **payload["cleanup"], "implementation_problem": None}
    result, applied = checkpoint(request["control"], saved, action, evidence)
    record.update(status="received", delivery=claim)
    record["receipts"].append(copy.deepcopy(request["receipt"]))
    return {"status": "received", "record": handoff.seal(record), "checkpoint": port_result(result, applied), "downstream_ready": False}


def accepted(request):
    entry.fields(request, {"protocol", "operation", "record", "control", "decision"})
    record = record_for(request)
    entry.fields(request["decision"], {"reference", "delivery_digest"})
    entry.nonempty(request["decision"]["reference"])
    require(record["delivery"] is not None and request["decision"]["delivery_digest"] == record["delivery"]["digest"],
            "authorization_changed", "acceptance must name the received delivery")
    saved, claim = record["handoff"], record["delivery"]
    ref, state, item = matching(record, request["control"]["context"])
    require(ref == claim["role_ref"] and state not in {"cancelled", "creation-failed"}, "identity_mismatch", "accepted identity is no longer bound")
    if record["acceptance"] is not None:
        require(record["acceptance"]["decision"] == request["decision"], "delivery_conflict", "accepted decision cannot be replaced")
        return {"status": "accepted", "record": request["record"], "accepted": record["acceptance"],
                "acknowledged": True, "downstream_ready": record["acceptance"]["downstream_ready"]}
    payload = claim["message"]["payload"]
    result = {"context": request["control"]["context"], "effects": []}
    applied = None
    if record["status"] != "accepted":
        require(record["status"] == "received", "result_incomplete", "receive before accepting")
        stage = saved["stage"]
        root = saved["expected_entry"]["repository"]["root"]
        if stage >= 2:
            handoff.phase_evidence(saved, role_ref=ref, receiving=True)
            if stage != 4:
                current = handoff.refresh(saved["entry"], saved["expected_entry"], after_work=True)
                handoff.source(current, saved["requirement"], stage)
        if saved["role"] == "execution-agent":
            result, applied = checkpoint(request["control"], saved, "accept-execution", {"agent_ref": ref, "file_hashes": payload["file_hashes"]})
        else:
            handoff.verify_result(stage, payload, saved["binding"], saved["scope"], root, ref)
            if stage < 2:
                current = handoff.refresh(saved["entry"], saved["expected_entry"], after_work=True)
                identity, revision = handoff.source(current, payload["requirement"], stage)
                if stage == 1:
                    handoff.delivery({**saved, "requirement": payload["requirement"], "delivery": payload["delivery"]}, current, identity, revision)
                result, applied = checkpoint(request["control"], saved, "accept", {"delivery_digest": item["delivery_digest"]})
            elif stage == 2:
                result, applied = checkpoint(request["control"], saved, "accept-design", {"ref": ref, "result_digest": claim["digest"]})
        record["status"] = "accepted"
    identity = saved["requirement_identity"]
    if saved["stage"] < 2:
        source = payload["requirement"]
        identity = source.get("requirement_identity", {k: source.get(k) for k in ("path", "sha256", "version")})
    outcome = handoff.seal({"protocol": PROTOCOL, "status": "accepted", "handoff": saved,
               "request_digest": record["request"]["digest"], "role_ref": ref, "attempt": record["request"]["attempt"],
               "requirement_identity": identity, "payload": payload, "delivery_digest": claim["digest"],
               "decision": request["decision"], "downstream_ready": saved["role"] != "execution-agent"})
    record["acceptance"] = outcome
    return {"status": "accepted", "record": handoff.seal(record), "checkpoint": port_result(result, applied),
            "accepted": outcome, "downstream_ready": outcome["downstream_ready"]}


def handle(request):
    request = copy.deepcopy(request)
    require(request.get("protocol") == PROTOCOL, "unsupported_protocol", "unsupported stage transfer protocol")
    operation = request.get("operation")
    require(operation in {"prepare", "bind", "reconcile", "receive", "accept"}, "invalid_operation", "unknown dispatch operation")
    return {"prepare": prepared, "bind": bound, "reconcile": bound, "receive": received, "accept": accepted}[operation](request)


if __name__ == "__main__":
    sys.exit(handoff.cli(handle))
