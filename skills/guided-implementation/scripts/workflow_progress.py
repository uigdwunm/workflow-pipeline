#!/usr/bin/env python3
"""Durable foreground progression. Host calls remain authenticated adapter work.

The outer checkpoint belongs to the existing conversation/runner. Only the
workflow_progress member is owned here; discussion authority stays in its ledger.
No host tools, daemon, Git publisher or governance-private records live here.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager

sys.path.insert(0, str(Path(__file__).resolve().parent))
import entry_prepare as entry
import requirement_prepare as requirement
import stage_handoff as handoff
import stage_dispatch as dispatch
import workflow_control as control
import skill_preflight
import supervision_protocol as supervision

PROTOCOL = "workflow-progress-v2"
KEY = "workflow_progress"
CHECKPOINT_LOCK_TIMEOUT = 5.0
require = entry.require


@contextmanager
def record_lock(path, *, timeout=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + ".lock").open("a+") as stream:
        deadline = time.monotonic() + (CHECKPOINT_LOCK_TIMEOUT if timeout is None else timeout)
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as error:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise entry.PreparationError("checkpoint_busy", "checkpoint lock wait timed out; retain the current operation") from error
                time.sleep(min(0.05, remaining))
        yield


def read_record(path):
    if not Path(path).exists():
        return {}
    # Checkpoints contain accumulated raw evidence, unlike bounded single inputs.
    with Path(path).open(encoding="utf-8") as stream:
        value = json.load(stream)
    require(isinstance(value, dict), "invalid_checkpoint", "checkpoint must be an object")
    return value


def atomic_save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix="." + path.name, delete=False) as stream:
            temporary = stream.name
            stream.write((json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            os.unlink(temporary)


def pending_decision(kind, subject, question):
    entry.nonempty(kind); entry.nonempty(question)
    return {"decision_id": str(uuid.uuid4()), "kind": kind, "subject": copy.deepcopy(subject), "question": question}


def decision_matches(pending, decision):
    entry.fields(decision, {"decision_id", "subject", "answer", "reference"})
    require(pending is not None and decision["decision_id"] == pending["decision_id"] and
            decision["subject"] == pending["subject"], "stale_decision", "answer does not name the current pending matter")
    entry.nonempty(decision["reference"]); entry.nonempty(decision["answer"])


def stage_boundary(mode, stage, accepted, next_stage=None):
    require(mode in {"continuous", "stepwise"}, "invalid_mode", "explicit flow mode required")
    return None if mode == "continuous" or stage == 4 else pending_decision(
        "stage-entry", {"stage": next_stage if next_stage is not None else stage + 1, "predecessor": accepted["digest"]},
        "Confirm entry to Stage " + str(next_stage if next_stage is not None else stage + 1))


def validate_state(state):
    require(isinstance(state, dict) and state.get("protocol") == PROTOCOL,
            "legacy_run_requires_original_runtime", "use the original runtime; no checkpoint migration")
    require(type(state.get("revision")) is int and state["revision"] >= 0,
            "invalid_checkpoint", "checkpoint revision is invalid")
    require(state.get("mode") in {"stepwise", "continuous"}, "invalid_checkpoint", "invalid mode")
    for pin in state["packages"].values():
        skill_preflight.verify_identity(pin)
    return state


def retain_entry_pins(request, retained):
    entry_request = request["entry"]
    if retained:
        registry_request = {"stage": entry_request["stage"], "action": entry_request["action"],
            **{key: entry_request[key] for key in ("target_stages", "required_skills") if key in entry_request}}
        registry_request.update({"registry": entry_request["registry"]} if "registry" in entry_request else
                                {"registry_query": {"cwd": os.getcwd()}})
        registered = skill_preflight.preflight(registry_request)
        current_pins = {name: retained.get(name, identity) for name, identity in registered["packages"].items()}
        entry_request["pinned_packages"] = current_pins
        if "expected_entry" in request:
            require(request["expected_entry"]["packages"] == current_pins, "package_changed", "entry must retain originally pinned package roots")
    if "expected_entry" not in request:
        request["expected_entry"] = entry.resolve(entry_request)


def _view(state, next_action=None, acknowledged=False):
    if next_action is None and state.get("recovery_action"):
        next_action = state["recovery_action"]
    if next_action is None and state["step"] == "host-response" and state.get("action"):
        next_action = {"operation": "lookup-exact-action", "action": state["action"]}
    if next_action is None and state["step"] == "publication-ready":
        ready = state["publication_candidate"]
        data = {"candidate_commit": ready["payload"]["candidate_commit"], "reference": ready["decision"]["reference"]}
        if state["stage"] == 4:
            data["expected_target_head"] = ready["target_head"]
        next_action = {"operation": "publication", "data": data}
    if next_action is None and state["step"] == "publication-complete":
        next_action = {"operation": "receive-publication", "data": {}}
    result = {"protocol": PROTOCOL, "revision": state["revision"], "status": state["status"],
              "step": state["step"], "pending": state.get("pending"), "next_action": next_action,
              "downstream_ready": state["status"] == "accepted" and state.get("phase_complete", True) and
                  bool((state.get("accepted") or {}).get("downstream_ready")), "acknowledged": acknowledged}
    if state["status"] == "accepted" and state.get("accepted") is not None and state["accepted"]["downstream_ready"] and state.get("phase_complete", True):
        if state["stage"] == 4:
            result["workflow_completion"] = verify_completion(state)
        result["completion"] = handoff.render(state["accepted"])
    elif state["status"] == "accepted" and not state.get("phase_complete", True) and next_action is None:
        result["next_action"] = {"operation": "complete-original-phase", "phase": state["handoff"]["authorization"].get("phase")}
    if state.get("error"):
        result["error"] = state["error"]
    return result


def verify_phase_completed(state):
    saved = state["handoff"]
    phase = saved["authorization"].get("phase")
    if phase is None:
        # 0/1 wrapper identity lives in the original selected control plan.
        selected = control.selected_control(state["control"]["context"],
            {"attempt": state["dispatch"]["request"]["attempt"]})
        authority = (selected.get("handoff_progress") or {}).get("plan", {}).get("entry_authority", {})
        if authority.get("kind") != "wrapper-phase-run":
            return
        phase = {"run_id": authority["run_id"], "attempt_id": authority["attempt_id"]}
    source = saved["entry"]["source"]
    require(source["kind"] == "discussion", "authority_missing", "phase requires its original discussion attachment")
    root = (saved.get("binding") or {}).get("repository", saved["expected_entry"]["repository"]["root"])
    result = entry.discussion_protocol.handle({"protocol_version": 1, "operation": "read-phase-run", "project_path": root,
        **source["attachment"], "phase_run_id": phase["run_id"]})
    run = result["phase_run"]
    require(run["state"] == "completed" and any(a["attempt_id"] == phase["attempt_id"] and a["state"] == "completed" for a in run["attempts"]),
            "phase_pending", "complete and finalize the original phase before advancing")


def verify_completion(state):
    """Re-read final Git, B/control acceptance and the original phase authority."""
    validate_state(state)
    require(state["stage"] == 4 and state["status"] == "accepted", "result_incomplete", "final B acceptance is required")
    accepted = state["accepted"]
    handoff.unseal(accepted)
    require(state["dispatch"]["acceptance"] == accepted, "result_changed", "final acceptance differs from the original dispatch")
    saved, payload = accepted["handoff"], accepted["payload"]
    verify_phase_completed(state)
    handoff.verify_result(4, payload, saved["binding"], saved["scope"], saved["binding"]["repository"], accepted["role_ref"])
    owner = state["control"]["context"]["handoff_progress"]
    require(owner["state"] == "completed" and owner["merge"] == payload["merge_commit"],
            "control_pending", "original closure control has not completed")
    publication = state.get("publication")
    require(publication is not None and publication.get("result") is not None, "publication_pending", "original publication is not reconciled")
    actual = supervision.reconcile_publication({key: publication[key] for key in ("operation", "request", "facts")})
    require(actual["state"] == "completed" and actual["merge_commit"] == payload["merge_commit"] and
            publication["completion_payload"] == payload, "publication_pending", "publication or cleanup differs from the accepted delivery")
    # Historical worktrees are gone. Verify immutable predecessor links and their
    # saved acceptances/phases, never demand the old worktree HEAD again.
    previous = saved.get("predecessor")
    while previous is not None:
        handoff.unseal(previous)
        prior = previous["handoff"]
        matches = [item for item in state.get("history", []) if (item.get("accepted") or {}).get("digest") == previous["digest"]]
        if matches:
            require(len(matches) == 1 and matches[0]["status"] == "accepted", "result_changed", "ambiguous prior acceptance")
            verify_phase_completed(matches[0])
        elif prior["authorization"].get("phase") is not None:
            verify_phase_completed({"handoff": prior})
        if prior["stage"] == 2:
            root = saved["binding"]["repository"]
            handoff.ancestor(root, previous["payload"]["planning_commit"], previous["payload"]["planning_merge_commit"])
            handoff.ancestor(root, previous["payload"]["planning_merge_commit"], payload["merge_commit"])
        previous = prior.get("predecessor")
    return {"completed": True, "merge_commit": payload["merge_commit"], "cleanup": actual["cleanup"],
            "acceptance": accepted["digest"], "phase_complete": True}


class Progress:
    def __init__(self, path, outer):
        self.path, self.outer = Path(path), outer
        self.state = outer.get(KEY)

    def save(self):
        self.state["revision"] += 1
        self.outer[KEY] = self.state
        atomic_save(self.path, self.outer)

    def stop_intent(self):
        s = self.state
        operation = self.outer.get("runner_request", {}).get("operation")
        if operation == "cancel" or s.get("stop_requested") == "cancelling" or s["status"] in {"cancelling", "cancelled"}:
            return "cancelling"
        if operation == "pause" or s.get("stop_requested") == "pausing" or s["status"] in {"pausing", "paused"}:
            return "pausing"
        return None

    def require_not_stopping(self):
        require(self.stop_intent() is None, "progression_suspended",
                "stop intent permits reconciliation and stopping, not new work or continuation")

    def advance_stop(self, intent):
        s = self.state
        if s["status"] in {"cancelled", "accepted"}:
            return _view(s, {"operation": "await-controller-recovery"} if s["status"] == "accepted" else None)
        if s.get("stop_requested") != intent:
            return self.suspend(cancel=intent == "cancelling")
        if s["status"] == "paused":
            return _view(s)
        if s["status"] != intent:
            s["status"] = intent
            self.save()
        if s["step"] == "host-response":
            return _view(s, {"operation": "lookup-exact-action", "action": s["action"],
                             "request": s["dispatch"]["request"], "receipts": s["observations"]})
        if intent == "cancelling" and s.get("stop_effects"):
            return _view(s, {"operation": "reconcile-stopped-writers", "effects": s["stop_effects"]})
        if s["dispatch"] and not s["stopped"] and self.bound_ref() is not None:
            return self.effect("stop-host", {"subject": self.subject(), "ref": self.bound_ref()})
        return _view(s)

    def effect(self, operation, payload):
        # An issued mutation is never reissued by advance. Exact lookup is safe.
        if operation in {"invoke-host", "continue-host", "source-lifecycle", "carrier-lifecycle"}:
            self.require_not_stopping()
        if operation == "continue-host" and self.state.get("closure_effects"):
            payload = {**payload, "remaining_actions": self.state["closure_effects"],
                       "publication_receipt": self.state["closure_receipt"]}
        action = {"action_id": str(uuid.uuid4()), "operation": operation, "payload": copy.deepcopy(payload),
                  "checkpoint": str(self.path)}
        self.state["action"] = action
        self.state["step"] = "host-response"
        self.save()
        return _view(self.state, action)

    def subject(self):
        record = self.state.get("dispatch")
        return {"stage": self.state["stage"], "attempt": record["request"]["attempt"] if record else None,
                "handoff": self.state.get("handoff", {}).get("digest")}

    def block(self, error):
        if self.state["status"] != "blocked":
            self.state["blocked_from"] = self.state["status"]
        self.state["status"] = "blocked"
        self.state["error"] = {"code": getattr(error, "code", "progress_failed"),
            "message": entry.error_message(error), "completed_evidence": getattr(error, "completed_evidence", []),
            "downstream_ready": False, "recovery": "resume the saved operation; do not recreate or republish"}
        self.save()
        return _view(self.state)

    def start(self, data):
        entry.fields(data, {"handoff"}, {"control", "next_stage", "requirement_transaction"})
        request = copy.deepcopy(data["handoff"])
        require(request.get("operation") == "prepare", "invalid_request", "handoff prepare input required")
        old = self.state
        retry = old is not None and old["status"] == "cancelled" and (old.get("dispatch") or {}).get("status") == "not-created" and not old.get("stop_requested")
        if old is not None:
            validate_state(old)
            require(old["status"] == "accepted" or retry, "run_active", "finish the current stage or reconcile non-creation before a new attempt")
            if not retry:
                verify_phase_completed(old)
            predecessor = old["handoff"]["predecessor"] if retry else old["accepted"]
            request.setdefault("predecessor", predecessor)
            request.setdefault("requirement", old["handoff"]["requirement"] if retry or old["stage"] >= 2 else old["accepted"]["payload"]["requirement"])
            require(request["predecessor"] == predecessor, "predecessor_changed", "retain the original predecessor")
            require(request["stage"] == (old["stage"] if retry else old["next_stage"]), "illegal_route", "use the authorized stage")
            require(old.get("pending") is None, "decision_required", "confirm the saved stage boundary first")
            if retry:
                require(request["authorization"]["reference"] != old["handoff"]["authorization"]["reference"],
                        "decision_required", "a new controller retry decision must distinguish the failed request")
                require(all(request[key] == old["handoff"][key] for key in ("role", "scope", "target", "binding", "semantic")),
                        "scope_changed", "non-creation retry cannot widen or replace the original work")
        else:
            request.setdefault("predecessor", None)
            if self.outer.get("confirmed", {}).get("requirement") is not None:
                request.setdefault("requirement", self.outer["confirmed"]["requirement"])
        if "requirement_transaction" in data:
            transaction = self.outer.get("workflow_requirements", {}).get(data["requirement_transaction"])
            require(transaction is not None and transaction["result"] is not None and transaction["error"] is None,
                    "requirement_incomplete", "exact successful requirement transaction required")
            require("requirement" not in data["handoff"] or data["handoff"]["requirement"] == transaction["result"],
                    "source_changed", "requirement differs from saved A transaction")
            request["requirement"] = transaction["result"]
        retained = copy.deepcopy(old["packages"] if old else {})
        for identity in self.outer.get("confirmed", {}).get("packages", {}).values():
            if "name" in identity:
                retained[identity["name"]] = identity
        retain_entry_pins(request, retained)
        # A resolves/rechecks actual cwd and complete host registration before B.
        resolved = entry.resolve({**request["entry"], "operation": "verify", "expected": request["expected_entry"]})
        mode = request["authorization"]["flow_mode"]
        require(old is None or mode == old["mode"], "authorization_changed", "mode cannot change without its exact decision")
        for root in (resolved["repository"]["root"], (request.get("binding") or {}).get("worktree")):
            require(root is None or not self.path.resolve().is_relative_to(Path(root).resolve()),
                    "unsafe_checkpoint", "checkpoint must live outside repository and disposable worktree")
        saved = handoff.handle(request)
        confirmed = self.outer.get("confirmed")
        if confirmed is not None:
            require(self.outer.get("current_stage") == "stage" + str(saved["stage"]),
                    "route_owner_mismatch", "only the runner advances the foreground stage")
            if "stages" in confirmed:
                stage_name = self.outer["current_stage"]
                actor, configuration = saved["expected_entry"]["actor"], saved["expected_entry"]["configuration"]
                require(actor["role"] == "scripted-carrier" and self.outer["sessions"].get(stage_name) == actor["thread_id"],
                        "carrier_unbound", "the runner must bind this exact CLI carrier before native dispatch")
                require(all(configuration[key] == confirmed["stages"][stage_name][key] for key in ("model", "reasoning_effort")),
                        "configuration_changed", "carrier differs from its confirmed runner configuration")
            require(saved["controller_ref"] == confirmed["controller_ref"] and mode == confirmed["flow_mode"],
                    "authorization_changed", "carrier must retain runner controller and flow mode")
            frozen = confirmed["frozen_requirement"]
            require(saved["source_commit"] == frozen["commit"] and saved["requirement_identity"]["sha256"] == frozen["sha256"] and
                    saved["requirement"]["absolute_path"] == frozen["path"], "source_changed", "carrier must consume the runner's exact frozen source")
            require(all(saved["binding"][key] == confirmed[key] for key in ("repository", "worktree", "git_common_dir", "target_branch")),
                    "binding_changed", "carrier worktree differs from the runner's authorized target")
            if "authority_scope" in confirmed:
                allowed = confirmed["authority_scope"]["allowed_paths"]
                require(all(supervision._path_matches(path, allowed) for path in
                            saved["scope"]["implementation_paths"] + saved["scope"]["closure_paths"]),
                        "scope_changed", "carrier scope exceeds the runner's authority")
        port = data.get("control")
        if port is None:
            require(old is not None and old["stage"] >= 2 and request["stage"] >= 2 and
                    old["control"]["context"].get("successor_control") is None,
                    "authority_missing", "initial/dedicated entry requires its original control port")
            context = copy.deepcopy(old["control"]["context"])
            context.update(stage=request["stage"], carrier=None, handoff_progress=None,
                           requirement_identity=old["handoff"]["requirement_identity"] if retry else old["accepted"]["requirement_identity"])
            port = {"context": context, "discussion": None}
        control.validate_context(port["context"])
        next_stage = data.get("next_stage", old["next_stage"] if retry else 2 if request["stage"] == 0 and mode == "continuous" else request["stage"] + 1)
        require(request["stage"] == 4 or (request["stage"], next_stage) in {(0, 1), (0, 2), (1, 2), (2, 3), (3, 4)},
                "illegal_route", "select a legal next stage")
        phase_required = "phase" in request["authorization"]
        if request["stage"] < 2:
            selector = request["authorization"].get("control_plan_id")
            selected = control.selected_control(port["context"], {"control_plan_id": selector} if selector else {})
            phase_required = (selected.get("handoff_progress") or {}).get("plan", {}).get("entry_authority", {}).get("kind") == "wrapper-phase-run"
        self.state = {"protocol": PROTOCOL, "revision": old["revision"] if old else 0,
            "mode": mode, "stage": request["stage"], "status": "active", "step": "prepare-dispatch",
            "next_stage": next_stage, "phase_complete": not phase_required,
            "packages": {**retained, **resolved["packages"]}, "handoff": saved, "control": copy.deepcopy(port),
            "dispatch": None, "accepted": None, "action": None, "pending": None,
            "events": {}, "observations": [], "transaction": None, "stopped": False,
            "history": (old["history"] + [{k: copy.deepcopy(value) for k, value in old.items() if k != "history"}]) if old else []}
        self.save()
        return self.advance()

    def call(self, operation, **extra):
        s = self.state
        request = {"protocol": handoff.PROTOCOL, "operation": operation, "control": s["control"], **extra}
        if operation == "prepare":
            request["handoff"] = s["handoff"]
        else:
            request["record"] = s["dispatch"]
        # Persist the exact discussion envelope before a ledger mutation.
        s["transaction"] = request
        self.save()
        result = dispatch.handle(copy.deepcopy(request))
        self.apply(result)
        return result

    def apply(self, result):
        s = self.state
        if "checkpoint" in result:
            s["control"]["context"] = result["checkpoint"]["context"]
            s["control_receipt"] = result["checkpoint"].get("discussion_receipt")
            self.next_envelope(s["control_receipt"])
        if "record" in result:
            s["dispatch"] = result["record"]
        if "accepted" in result:
            s["accepted"] = result["accepted"]
        s["transaction"] = None
        s["last_result"] = result
        self.save()

    def next_envelope(self, receipt):
        if receipt is not None and self.state["control"]["discussion"] is not None:
            # New logical mutations get a new key; a saved transaction keeps its
            # original envelope until its idempotent result has been recovered.
            self.state["control"]["discussion"] = {**self.state["control"]["discussion"],
                "expected_ledger_revision": receipt["ledger_revision"],
                "expected_topic_revision": receipt["record_revision"], "idempotency_key": str(uuid.uuid4())}

    def advance(self):
        s = self.state
        intent = self.stop_intent()
        if intent is not None:
            return self.advance_stop(intent)
        self.recover_publication_intake_step()
        if s["status"] == "accepted" and not s.get("phase_complete", True):
            return self.phase_action(completing=True)
        if s["status"] in {"blocked", "paused", "cancelled", "needs_input", "accepted"}:
            return _view(s)
        if s["step"] == "prepare-dispatch":
            if s["dispatch"] is None:
                result = self.call("prepare")
                if "record" not in result:
                    return self.block(entry.PreparationError("outcome_unknown", "recover the original dispatch request from its checkpoint owner"))
            s["step"] = "launch"
            self.save()
        if s["step"] == "launch":
            self.verify_launch()
            return self.effect("invoke-host", s["dispatch"]["request"])
        if s["step"] == "host-response":
            return _view(s, {"operation": "lookup-exact-action", "action": s["action"],
                             "request": s["dispatch"]["request"], "receipts": s["observations"]})
        if s["status"] == "cancelling" and s.get("stop_effects"):
            return _view(s, {"operation": "reconcile-stopped-writers", "effects": s["stop_effects"]})
        if s["step"] == "bound":
            if s.get("stop_requested") in {"pausing", "cancelling"}:
                return self.effect("stop-host", {"subject": self.subject(), "ref": self.bound_ref()})
            if s["handoff"]["authorization"].get("phase") and s["stage"] == 2:
                action = self.phase_action()
                if action is not None:
                    return action
            return _view(s, {"operation": "wait-host", "ref": self.bound_ref(), "request": s["dispatch"]["request"]})
        if s["step"] == "continue":
            current = handoff.refresh(s["handoff"]["entry"], s["handoff"]["expected_entry"], after_work=True)
            if s["stage"] >= 2:
                handoff.source(current, s["handoff"]["requirement"], s["stage"])
            else:
                handoff.phase_evidence(s["handoff"])
            if s["handoff"]["authorization"].get("phase") and s["stage"] == 2:
                action = self.phase_action()
                if action is not None:
                    return action
            return self.effect("continue-host", {"ref": self.bound_ref(), "subject": self.subject(),
                "result": s.get("last_observation"), "decision": s.get("last_decision"), "intent": s.get("resume_intent")})
        if s["step"] == "received":
            subject = {**self.subject(), "delivery_digest": s["dispatch"]["delivery"]["digest"]}
            if s["pending"] is None:
                s["pending"] = pending_decision("acceptance", subject, "Controller acceptance of the verified candidate")
            else:
                require(s["pending"]["kind"] == "acceptance" and s["pending"]["subject"] == subject,
                        "decision_conflict", "retain the original pending acceptance")
            s["status"] = "needs_input"
            self.save()
        return _view(s)

    def phase_action(self, completing=False):
        s, saved = self.state, self.state["handoff"]
        phase = saved["authorization"].get("phase")
        if phase is None:
            selected = control.selected_control(s["control"]["context"], {"attempt": s["dispatch"]["request"]["attempt"]})
            authority = selected["handoff_progress"]["plan"]["entry_authority"]
            phase = {"run_id": authority["run_id"], "attempt_id": authority["attempt_id"]}
        root = (saved.get("binding") or {}).get("repository", saved["expected_entry"]["repository"]["root"])
        attachment = saved["entry"]["source"]["attachment"]
        base = {"protocol_version": 1, "project_path": root, **attachment}
        run = entry.discussion_protocol.handle({**base, "operation": "read-phase-run", "phase_run_id": phase["run_id"]})["phase_run"]
        attempts = [a for a in run["attempts"] if a["attempt_id"] == phase["attempt_id"]]
        require(len(attempts) == 1, "identity_mismatch", "original phase attempt is missing")
        attempt = attempts[0]
        require(attempt["state"] not in {"failed", "cancelled", "superseded", "outcome-unknown", "blocked"},
                "phase_requires_reconciliation", "original phase attempt is terminal or uncertain; do not wait or replace blindly")
        require(completing or attempt["state"] in {"setup-pending", "ready", "active"},
                "phase_requires_reconciliation", "completion-claimed phase cannot resume substantive work")
        if completing and run["state"] == "completed" and attempt["state"] == "completed":
            s["phase_complete"] = True
            s["pending"] = stage_boundary(s["mode"], s["stage"], s["accepted"], s["next_stage"])
            self.save()
            return _view(s)
        operation = ({"active": "claim-phase-completion", "completion-claimed": "complete-phase-run", "completion-pending": "finalize-phase-run"}.get(attempt["state"])
                     if completing else "phase-activate" if attempt["state"] == "ready" else None)
        if operation is None:
            if not completing and attempt["state"] == "active":
                return None
            return _view(s, {"operation": "wait-phase-completion" if completing else "wait-phase-ready",
                             "phase": phase, "ref": attempt.get("carrier_ref"), "state": attempt["state"]})
        if (s.get("action") or {}).get("operation") in {"source-lifecycle", "carrier-lifecycle"} and not s["action"].get("receipt"):
            return _view(s, {"operation": "reconcile-source-lifecycle", "action": s["action"]})
        topic = entry.topic_read(root, attachment)
        request = {**base, "operation": operation, "expected_ledger_revision": topic["ledger_revision"],
                   "expected_topic_revision": topic["record_revision"], "idempotency_key": str(uuid.uuid4()),
                   "phase_run_id": phase["run_id"], "attempt_id": phase["attempt_id"],
                   "evidence": attempt.get("output_evidence", attempt.get("working_evidence", run["evidence"])) if completing else run["evidence"]}
        actor = attachment["actor_conversation_ref"]
        if operation == "claim-phase-completion":
            actor = attempt["carrier_ref"]
            request.update(actor_conversation_ref=actor, carrier_ref=actor)
        # Only the authenticated source executes this envelope. A CLI/native
        # carrier must return it to that source, never impersonate its identity.
        return self.effect("carrier-lifecycle" if operation == "claim-phase-completion" else "source-lifecycle", {"actor_ref": actor, "request": request,
                                                "return_step": s["step"]})

    def verify_launch(self):
        s, saved = self.state, self.state["handoff"]
        if saved["stage"] >= 2 or saved["entry"]["source"]["kind"] != "discussion":
            handoff.verify(saved)
            return
        # reserve-launch legitimately changed the original ledger control view.
        # Bind owner/settings/pins with B's post-mutation verifier, then require
        # unchanged source/Git facts and *exact* readback of our saved reservation.
        current = handoff.refresh(saved["entry"], saved["expected_entry"], after_work=True)
        identity, revision = handoff.source(current, saved["requirement"], saved["stage"])
        require(identity == saved["requirement_identity"] and revision == saved["source_commit"],
                "source_changed", "source changed after the saved reservation")
        require(current["repository"] == saved["expected_entry"]["repository"], "entry_changed", "repository changed after reservation")
        handoff.phase_evidence(saved)
        topic = entry.topic_read(current["repository"]["root"], saved["entry"]["source"]["attachment"])
        require(topic["workflow_control"] == s["control"]["context"], "authority_changed", "reservation is no longer current")

    def bound_ref(self):
        carrier = control.selected_control(self.state["control"]["context"],
                    {"attempt": self.state["dispatch"]["request"]["attempt"]})["carrier"]
        if self.state["handoff"]["role"] == "execution-agent":
            ref, _, _ = dispatch.matching(self.state["dispatch"], self.state["control"]["context"])
            return ref
        return carrier["ref"]

    def observe(self, data):
        entry.fields(data, {"event_id", "receipt"}, {"result", "action_id", "closure", "publication_candidate"})
        s, receipt = self.state, data["receipt"]
        entry.nonempty(data["event_id"])
        digest = entry.digest(data)
        previous = s["events"].get(data["event_id"])
        if previous is not None:
            require(previous["digest"] == digest, "event_conflict", "event ID reused with different content")
            if previous["applied"]:
                return _view(s, acknowledged=True)
        # Keep even invalid/late raw receipts for exact recovery, not as authority.
        if previous is None:
            s["events"][data["event_id"]] = {"digest": digest, "applied": False}
            s["observations"].append(copy.deepcopy(data))
            self.save()
        if "action_id" in data:
            require(s["action"] is not None and data["action_id"] == s["action"]["action_id"],
                    "stale_action", "observation belongs to another host action")
        dispatch.receipt_for(s["dispatch"], receipt, {"create", "lookup", "result"})
        message_key = entry.digest({"request": s["dispatch"]["request"]["digest"], "ref": receipt["ref"], "result": data["result"]}) if "result" in data else None
        if message_key in s.get("observed_messages", {}):
            s["events"][data["event_id"]]["applied"] = True
            self.save()
            return _view(s, acknowledged=True)
        if receipt["event"] in {"create", "lookup"}:
            result = self.call("bind" if receipt["event"] == "create" else "reconcile", receipt=receipt)
            if result["status"] == "bound":
                s["step"] = "bound"
            elif result["status"] == "not-created":
                s["status"], s["step"], s["stopped"] = "cancelled", "not-created", True
            else:
                s["step"] = "host-response"
        else:
            require(receipt["ref"] == self.bound_ref(), "identity_mismatch", "observation must name the bound role")
            require(receipt["status"] in {"running", "idle", "turn-completed", "stopped", "unknown"},
                    "invalid_result", "invalid host execution state")
            if s["status"] == "cancelled":
                raise entry.PreparationError("attempt_ended", "cancelled attempt cannot accept late results")
            if "publication_candidate" in data:
                require("result" not in data and "closure" not in data, "invalid_result", "candidate readiness is separate from completed delivery")
                self.publication_candidate(data["publication_candidate"], receipt)
                s["events"][data["event_id"]]["applied"] = True
                self.save()
                return _view(s)
            if "closure" in data:
                require(s["stage"] == 4, "invalid_result", "closure observation belongs to Stage 4")
                publication = s.get("publication") or {}
                if publication and data["closure"].get("implementation_problem") is not None:
                    observed_publication = supervision.reconcile_publication(self.publication_transaction())
                    require(observed_publication["state"] in {"not-published", "prepared"},
                            "already_published", "published or uncertain actions cannot return to implementation")
                known_merge = (publication.get("result") or {}).get("merge_commit") or (
                    ((publication.get("error") or {}).get("error") or {}).get("context") or {}).get("merge_commit")
                require(not (known_merge and data["closure"].get("implementation_problem") is not None),
                        "already_published", "published merge permits cleanup-only, never implementation replay")
                # Existing Git adapter determines actual ancestry and cleanup.
                result, applied = dispatch.checkpoint(s["control"], s["handoff"], "closure-result", data["closure"])
                s["control"]["context"] = result["context"]
                s["control_receipt"] = applied
                self.next_envelope(applied)
                s["closure_effects"] = result["effects"]
                s["closure_receipt"] = copy.deepcopy(data)
                self.save()
                if result.get("return_stage") == 3:
                    s["recovery_action"] = {"operation": "implementation-recovery", "stage": 3,
                        "ref": s["handoff"]["predecessor"]["role_ref"], "binding": s["handoff"]["binding"],
                        "problem": data["closure"]["implementation_problem"]}
                    return self.block(entry.PreparationError("implementation_required", "return the retained flow to its original controller's implementation recovery; do not wait on closure"))
            s["stopped"] = receipt["status"] == "stopped"
            if s.get("stop_requested") in {"pausing", "cancelling"}:
                if s["stop_requested"] == "pausing" and "result" in data:
                    s["deferred_observation"] = copy.deepcopy(data)
                if s["stopped"]:
                    if s["stop_requested"] == "cancelling":
                        self.cancel_control()
                    else:
                        s["status"] = "paused"
                s["step"] = "bound"
            elif "result" in data:
                if data["result"]["status"] == "continue":
                    require(receipt["status"] in {"idle", "turn-completed"}, "cannot_continue", "host must verify a resumable turn")
                    fingerprint = entry.digest(data["result"]["payload"])
                    repeats = s.get("continue_repeats", 0) + 1 if fingerprint == s.get("continue_fingerprint") else 0
                    require(repeats < 2, "no_progress", "repeated continuation has no new progress evidence")
                    s["continue_fingerprint"], s["continue_repeats"] = fingerprint, repeats
                result = self.call("receive", receipt=receipt, result=data["result"])
                s["last_observation"] = copy.deepcopy(data)
                s["step"] = {"received": "received", "continue": "continue", "needs_input": "decision",
                             "technical_error": "technical-error", "accepted": "accepted"}[result["status"]]
                if result["status"] == "needs_input":
                    question = data["result"]["payload"].get("question")
                    s["pending"] = pending_decision("user-decision", self.subject(), question)
                    s["status"] = "needs_input"
                elif result["status"] == "technical_error":
                    s["status"] = "blocked"
                    s["error"] = {"code": "technical_error", "message": data["result"]["payload"], "downstream_ready": False}
                elif result["status"] == "continue":
                    require(receipt["status"] in {"idle", "turn-completed"}, "cannot_continue", "host must verify a resumable turn; stopped identity requires recovery")
            elif s.get("awaiting_resume") and receipt["status"] in {"idle", "turn-completed"}:
                s.pop("awaiting_resume")
                s["status"], s["step"] = "active", "continue"
            elif receipt["status"] in {"idle", "turn-completed", "stopped", "unknown"}:
                s["status"] = "blocked"
                s["error"] = {"code": "business_result_missing", "downstream_ready": False,
                              "message": "Host state alone does not establish business completion or permission to resume"}
            else:
                s["step"] = "bound"
        s["events"][data["event_id"]]["applied"] = True
        if message_key is not None and s.get("deferred_observation") != data:
            s.setdefault("observed_messages", {})[message_key] = data["event_id"]
        if s["status"] == "blocked":
            # A newly validated result can resolve an observation/transport error.
            # Technical business outcomes remain blocked until exact recovery.
            if ("result" in data or receipt["event"] in {"create", "lookup"}) and data.get("result", {}).get("status") != "technical_error" and s["step"] != "technical-error":
                s["status"] = s.get("stop_requested", "active")
                s.pop("error", None)
        self.save()
        return self.advance()

    def decide(self, data):
        s = self.state
        prior = s.get("decisions", {}).get(data.get("decision_id"))
        if prior is not None:
            require(prior == data, "decision_conflict", "decision cannot be replaced")
            return _view(s, acknowledged=True)
        decision_matches(s.get("pending"), data)
        pending = s["pending"]
        deferred = s.get("deferred_decisions", {}).get(data["decision_id"])
        intent = self.stop_intent()
        if deferred is not None:
            if deferred["disposition"] == "cancelled" and intent is None:
                if deferred["decision"] == data:
                    return _view(s, acknowledged=True)
                require(data["reference"] != deferred["decision"]["reference"], "decision_conflict",
                        "cancelled input needs a fresh controller decision after authorized recovery")
            else:
                require(deferred["decision"] == data, "decision_conflict", "saved answer cannot be replaced")
        if intent is not None:
            disposition = "cancelled" if intent == "cancelling" else "paused"
            saved = {"decision": copy.deepcopy(data), "disposition": disposition}
            if deferred != saved:
                s.setdefault("deferred_decisions", {})[data["decision_id"]] = saved
                self.save()
            # Recording a valid reply is not acceptance, continuation or resume.
            result = self.advance_stop(intent)
            result.update(decision_deferred=True, acknowledged=deferred == saved)
            return result
        if pending["kind"] == "publication-readiness":
            require(data["answer"] == "accept", "decision_required", "original controller readiness acceptance required")
            s["publication_candidate"]["decision"] = copy.deepcopy(data)
            s["pending"], s["status"], s["step"] = None, "active", "publication-ready"
        elif pending["kind"] == "acceptance":
            require(data["answer"] == "accept", "decision_required", "acceptance requires the controller's accept decision")
            if (s.get("publication") or {}).get("intake") is not None:
                original = s["publication"].get("acceptance_decision")
                require(original is None or original == data, "decision_conflict", "retain the original publication acceptance decision")
                if original is None:
                    s["publication"]["acceptance_decision"] = copy.deepcopy(data)
                    self.save()
            self.call("accept", decision={"reference": data["reference"], "delivery_digest": pending["subject"]["delivery_digest"]})
            s["status"], s["step"] = "accepted", "accepted"
            s["pending"] = stage_boundary(s["mode"], s["stage"], s["accepted"], s["next_stage"]) if s["accepted"]["downstream_ready"] and s["phase_complete"] else None
        elif pending["kind"] == "stage-entry":
            require(data["answer"] in {"confirm", "continuous"}, "decision_required", "confirm or continuous required")
            if data["answer"] == "continuous":
                s["mode"] = "continuous"
            s["pending"] = None
        else:
            s["pending"], s["status"], s["step"] = None, "active", "continue"
            if s["stopped"]:
                s["step"], s["awaiting_resume"] = "bound", True
        s.setdefault("decisions", {})[data["decision_id"]] = copy.deepcopy(data)
        s["last_decision"] = copy.deepcopy(data)
        self.save()
        return self.advance()

    def cancel_control(self):
        s = self.state
        selector = s["handoff"]["authorization"].get("control_plan_id")
        evidence = {"control_plan_id": selector} if selector else {}
        result, applied = dispatch.checkpoint(s["control"], s["handoff"], "cancel", evidence)
        s["control"]["context"], s["control_receipt"] = result["context"], applied
        self.next_envelope(applied)
        # Every old writer must still be reconciled by the original control recovery.
        s["stop_effects"] = [e for e in result["effects"] if not (s["stopped"] and e.get("ref") == self.bound_ref())]
        s["status"] = "cancelled" if not s["stop_effects"] else "cancelling"

    def publication_candidate(self, data, receipt):
        s, saved = self.state, self.state["handoff"]
        self.require_not_stopping()
        require(s["stage"] in {2, 4} and not s.get("publication"), "already_published", "reconcile the original publication")
        entry.fields(data, {"candidate_commit", "artifacts", "checks"})
        handoff.commit(data["candidate_commit"])
        handoff.strings(data["artifacts"]); handoff.strings(data["checks"])
        require(receipt["status"] == "stopped", "host_evidence_missing", "publication requires the original writer's stopped receipt")
        self.verify_publication_authority(data["candidate_commit"], before=True)
        s["publication_candidate"] = {"payload": copy.deepcopy(data), "receipt": copy.deepcopy(receipt),
                                      "handoff_digest": saved["digest"], "decision": None,
                                      "target_head": supervision._branch_oid(Path(saved["binding"]["repository"]), saved["binding"]["target_branch"])}
        s["stopped"] = True
        s["pending"] = pending_decision("publication-readiness", {**self.subject(), "candidate": entry.digest(data)},
                                        "Record the original controller's candidate readiness decision")
        s["status"], s["step"] = "needs_input", "publication-readiness"

    def verify_publication_authority(self, candidate, *, before):
        s, saved = self.state, self.state["handoff"]
        handoff.unseal(saved)
        control.validate_context(s["control"]["context"])
        ref, state, owner = dispatch.matching(s["dispatch"], s["control"]["context"])
        require(ref == self.bound_ref() and s["control"]["context"]["controller_ref"] == saved["controller_ref"] and
                owner["binding"] == saved["binding"] and owner["git_baseline_commit"] == saved["scope"]["baseline"] and
                s["control"]["context"]["requirement_identity"] == saved["requirement_identity"],
                "authority_changed", "publication owner, baseline or binding changed")
        require(state in ({"designing"} if s["stage"] == 2 else {"closing", "cleanup-pending", "completed"}),
                "authority_changed", "publication control is no longer active")
        for pin in s["packages"].values():
            skill_preflight.verify_identity(pin)
        # Never substitute another cwd for the pinned entry after removal.
        if Path(saved["binding"]["worktree"]).exists():
            current = handoff.refresh(saved["entry"], saved["expected_entry"], after_work=True)
            identity, revision = handoff.source(current, saved["requirement"], s["stage"])
            require(identity == saved["requirement_identity"] and revision == saved["source_commit"],
                    "source_changed", "publication source changed")
        handoff.phase_evidence(saved, receiving=True)
        scope, binding = saved["scope"], saved["binding"]
        root = binding["repository"]
        if s["stage"] == 4:
            predecessor = handoff.unseal(saved["predecessor"])
            accepted = predecessor["payload"]["candidate_commit"]
            require(owner["candidate"] == accepted and owner["closure_paths"] == scope["closure_paths"] and
                    owner["implementation_paths"] == scope["implementation_paths"] and
                    owner["protected_paths"] == scope["protected_paths"], "scope_changed", "closure authority changed")
            control.validate_review(accepted, predecessor["payload"]["review"], predecessor["payload"]["verification"], predecessor["role_ref"])
            handoff.ancestor(root, accepted, candidate)
            changed = supervision._changed_paths(Path(root), accepted, candidate)
            require(set(changed) <= set(scope["closure_paths"]), "scope_changed", "closure candidate changes implementation or unapproved documents")
        else:
            require(owner["allowed_paths"] == scope["owned_paths"] and owner["protected_paths"] == scope["protected_paths"],
                    "scope_changed", "planning authority changed")
        for relative in scope["protected_paths"]:
            require(handoff.blob(root, scope["baseline"], relative) == handoff.blob(root, candidate, relative) and
                    handoff.mode(root, scope["baseline"], relative) == handoff.mode(root, candidate, relative),
                    "source_changed", "publication changed protected source")
        if before:
            report = supervision.verify_worktree({"binding": binding, "platform_cwd": binding["worktree"]})
            require(report["current_commit"] == candidate and not supervision._full_status(Path(binding["worktree"])),
                    "candidate_changed", "publication requires the exact clean candidate")

    def publication_transaction(self):
        publication = self.state["publication"]
        return {key: copy.deepcopy(publication[key]) for key in ("operation", "request", "facts")}

    def record_publication_fact(self, fact):
        self.state["publication"]["facts"].append(copy.deepcopy(fact))
        self.save()

    def verify_publication_readiness(self):
        s = self.state
        ready = s.get("publication_candidate")
        require(ready is not None and ready["decision"] is not None and ready["handoff_digest"] == s["handoff"]["digest"],
                "candidate_not_accepted", "receive the stopped candidate and record its original readiness decision first")
        dispatch.receipt_for(s["dispatch"], ready["receipt"], {"result"})
        require(ready["receipt"]["status"] == "stopped" and ready["receipt"]["ref"] == self.bound_ref(),
                "host_evidence_missing", "exact original writer stop proof required")
        matching = [o["receipt"] for o in s["observations"] if o["receipt"].get("ref") == self.bound_ref()]
        require(matching and matching[-1]["status"] == "stopped", "writer_active", "writer has resumed since readiness")
        self.verify_publication_authority(ready["payload"]["candidate_commit"], before=False)
        return ready

    def publication_payload(self, result):
        s, ready = self.state, self.state["publication_candidate"]["payload"]
        payload = {key: copy.deepcopy(ready[key]) for key in ("artifacts", "checks")}
        if s["stage"] == 2:
            payload.update(planning_commit=ready["candidate_commit"], planning_merge_commit=result["merge_commit"],
                           planning_paths=s["publication"]["request"]["allowed_paths"])
        else:
            binding, scope = s["handoff"]["binding"], s["handoff"]["scope"]
            payload.update(candidate_commit=s["handoff"]["predecessor"]["payload"]["candidate_commit"],
                           merge_commit=result["merge_commit"], cleanup=supervision._cleanup_facts(binding),
                           changed_paths=supervision._changed_paths(Path(binding["repository"]), scope["baseline"], result["merge_commit"]))
        return payload

    def finish_publication(self, result):
        s = self.state
        s["publication"]["result"], s["publication"]["error"] = copy.deepcopy(result), None
        s["publication"]["completion_payload"] = self.publication_payload(result)
        if s["status"] == "blocked":
            s["status"] = "active"
            s.pop("error", None)
        s["step"] = "publication-complete"
        self.save()
        return _view(s, {"operation": "publication-receipt", "publication": s["publication"]})

    def reconcile_publication(self, *, resume=False):
        s = self.state
        require(s.get("publication") is not None, "publication_missing", "no original publication to reconcile")
        if resume:
            self.require_not_stopping()
            self.verify_publication_readiness()
        try:
            result = supervision.reconcile_publication(self.publication_transaction(), resume=resume,
                                                       record=self.record_publication_fact if resume else None)
        except supervision.ProtocolError as error:
            if not resume:
                return _view(s, {"operation": "publication-observation", "result": supervision._error_response(error, "reconcile-publication")})
            s["publication"]["error"] = supervision._error_response(error, "reconcile-publication")
            self.save()
            return self.block(entry.PreparationError(error.code, error.message))
        if resume:
            return self.finish_publication(result)
        return _view(s, {"operation": "publication-observation", "result": result})

    def recover_publication_intake_step(self):
        """Finish C's local consumption after B's result has already been saved."""
        s = self.state
        intake = (s.get("publication") or {}).get("intake")
        record = s.get("dispatch") or {}
        if intake is None or record.get("delivery") is None:
            return
        require(record["delivery"]["message"] == intake["result"], "delivery_conflict", "retain the saved publication intake")
        require(record["status"] in {"received", "accepted"}, "result_incomplete", "B intake is not complete")
        if record["status"] == "accepted" and s["status"] != "accepted" and s["step"] != "accepted":
            decision = s["publication"].get("acceptance_decision")
            require(decision is not None and s["accepted"] == record["acceptance"],
                    "decision_required", "recover the original saved acceptance decision")
            decision_matches(s["pending"], decision)
            require(s["accepted"]["decision"] == {"reference": decision["reference"],
                    "delivery_digest": decision["subject"]["delivery_digest"]},
                    "decision_conflict", "B acceptance differs from the saved controller decision")
            s.setdefault("decisions", {})[decision["decision_id"]] = copy.deepcopy(decision)
            s["last_decision"] = copy.deepcopy(decision)
            s["status"], s["step"] = "accepted", "accepted"
            s["pending"] = stage_boundary(s["mode"], s["stage"], s["accepted"], s["next_stage"]) if s["accepted"]["downstream_ready"] and s["phase_complete"] else None
            s.pop("error", None)
            self.save()
            return
        if s["step"] != "publication-complete":
            return
        # Existing accepted state and its pending successor decision are untouched.
        s["step"] = "accepted" if record["status"] == "accepted" else "received"
        self.save()

    def receive_publication(self):
        s = self.state
        self.require_not_stopping()
        require(s.get("publication") is not None and s["publication"].get("result") is not None,
                "publication_pending", "reconcile publication before receiving it")
        publication = s["publication"]
        actual = supervision.reconcile_publication(self.publication_transaction())
        require(actual["state"] in {"planning_published", "completed"} and
                actual["merge_commit"] == publication["result"]["merge_commit"],
                "publication_pending", "reverify the original publication before B intake")
        message = {"delivery_id": "publication:" + entry.digest(publication["request"]),
                   "status": "completed", "payload": publication["completion_payload"]}
        if s["dispatch"].get("delivery") is not None:
            require(s["dispatch"]["delivery"]["message"] == message, "delivery_conflict", "retain the original B delivery")
            if s["status"] == "accepted":
                return _view(s, acknowledged=True)
            result = self.advance()
            result["acknowledged"] = True
            return result
        # This is stage-owner composition, not a new host observation or a claim
        # that the stopped child subsequently ran Git. Preserve both sources.
        publication["intake"] = {"kind": "stage-owner-publication", "owner": s["handoff"]["expected_entry"]["actor"],
                                 "native_candidate": copy.deepcopy(s["publication_candidate"]),
                                 "git_facts": copy.deepcopy(actual), "result": copy.deepcopy(message)}
        self.save()
        transaction = s.get("transaction")
        if transaction is not None:
            require(transaction["operation"] == "receive" and transaction["result"] == message and
                    transaction["receipt"] == s["publication_candidate"]["receipt"],
                    "transaction_pending", "recover the original B operation first")
            self.apply(dispatch.handle(copy.deepcopy(transaction)))
        else:
            self.call("receive", receipt=s["publication_candidate"]["receipt"], result=message)
        s["step"] = "accepted" if s["dispatch"]["status"] == "accepted" else "received"
        self.save()
        return self.advance()

    def publication(self, data):
        entry.fields(data, {"candidate_commit", "reference"}, {"expected_target_head", "planning_paths"})
        self.require_not_stopping()
        entry.nonempty(data["reference"])
        s = self.state
        require(s["stage"] in {2, 4} and s["dispatch"] is not None and s["dispatch"]["status"] == "bound" and
                s["status"] not in {"paused", "pausing", "cancelling", "cancelled"}, "authority_missing", "publication requires its active bound stage")
        ready = self.verify_publication_readiness()
        require(data["candidate_commit"] == ready["payload"]["candidate_commit"] and data["reference"] == ready["decision"]["reference"],
                "candidate_changed", "publication must consume the original readiness decision")
        scope, binding = s["handoff"]["scope"], s["handoff"]["binding"]
        if s["stage"] == 2:
            paths = data.get("planning_paths", scope["owned_paths"])
            control.paths(paths)
            require(set(paths) <= set(scope["owned_paths"]), "scope_changed", "planning publication exceeds approved paths")
            request = {"binding": binding, "planning_commit": data["candidate_commit"], "allowed_paths": paths,
                       "protected_paths": scope["protected_paths"]}
            operation = "publish-planning"
        else:
            require(data.get("expected_target_head") == ready["target_head"], "target_changed", "retain the target bound to readiness")
            require(not (s["control"]["context"].get("handoff_progress") or {}).get("merge"),
                    "already_published", "published merge permits cleanup-only")
            request = {"binding": binding, "candidate_commit": data["candidate_commit"],
                       "expected_target_head": data.get("expected_target_head"), "scope_base_commit": scope["baseline"],
                       "allowed_paths": sorted(set(scope["implementation_paths"] + scope["closure_paths"])),
                       "protected_paths": scope["protected_paths"]}
            operation = "complete-worktree"
        existing = s.get("publication")
        if existing:
            require(existing["request"] == request, "publication_conflict", "reconcile the original publication before changing its candidate")
            return _view(s, {"operation": "publication-receipt" if existing.get("result") else "reconcile-publication",
                             "publication": existing}, acknowledged=True)
        s["publication"] = {"operation": operation, "request": request, "reference": data["reference"],
                            "result": None, "error": None, "issued": True, "facts": []}
        self.save()
        try:
            # Existing primitives own Git behavior, locks, merge and cleanup.
            result = (supervision.publish_planning if s["stage"] == 2 else supervision.complete_worktree)(request, record=self.record_publication_fact)
        except supervision.ProtocolError as error:
            s["publication"]["error"] = supervision._error_response(error, operation)
            self.save()
            return self.block(entry.PreparationError(error.code, "publication requires original-protocol reconciliation; do not republish"))
        return self.finish_publication(result)

    def control_action(self, data):
        entry.fields(data, {"action", "evidence", "receipt"})
        require(data["action"] in {"successor-ready", "archive", "archive-result", "execution-result",
                    "accept-execution", "execution-dispatch-result", "recover-dispatch"},
                "invalid_operation", "use the original bounded control recovery/closure operation")
        require(isinstance(data["receipt"], dict) and data["receipt"], "host_evidence_missing", "original authenticated host/controller evidence required")
        s = self.state
        identity = entry.digest(data)
        transactions = s.setdefault("control_transactions", {})
        previous = transactions.get(identity)
        if previous and previous.get("result") is not None:
            return _view(s, {"operation": "control-effects", "result": previous["result"]}, acknowledged=True)
        transactions.setdefault(identity, {"request": copy.deepcopy(data), "port": copy.deepcopy(s["control"]),
                                            "status": s["status"], "result": None})
        self.save()
        # Retain exact ledger envelope on a lost response; never promote slots by hand.
        result, applied = dispatch.checkpoint(transactions[identity]["port"], s["handoff"], data["action"], data["evidence"])
        s["control"]["context"], s["control_receipt"] = result["context"], applied
        self.next_envelope(applied)
        transactions[identity]["result"] = result
        if s["status"] == "blocked":
            s["status"] = transactions[identity]["status"]
            s.pop("error", None)
        if data["action"] == "recover-dispatch":
            s.pop("stop_requested", None)
            s.pop("error", None)
            s.update(status="active", step="bound", stopped=False)
            s["recovered_request"] = self.outer.get("runner_request")
        elif s.get("stop_requested") == "cancelling" and s["stopped"]:
            remaining = [e for e in (s["control"]["context"].get("handoff_progress") or {}).get("executions", []) if not e["stopped"]]
            if not remaining:
                s["status"], s["stop_effects"] = "cancelled", []
        self.save()
        return _view(s, {"operation": "control-effects", "result": result})

    def allocation(self, data):
        """B transport records share the dispatcher's existing control roster."""
        entry.fields(data, {"allocation_id", "operation"}, {"handoff", "receipt", "result", "decision"})
        identity = entry.nonempty(data["allocation_id"])
        operation = data["operation"]
        require(operation in {"prepare", "bind", "reconcile", "receive", "accept"}, "invalid_operation", "unknown allocation operation")
        s = self.state
        require(s["stage"] == 3 and s["handoff"]["role"] == "implementation-dispatcher", "role_mismatch", "allocation belongs to the Stage-3 dispatcher")
        slots = s.setdefault("allocations", {})
        slot = slots.get(identity)
        if operation == "prepare":
            entry.fields(data, {"allocation_id", "operation", "handoff"})
            if slot is not None:
                require(slot["input"] == data["handoff"], "allocation_conflict", "retain the original allocation intent")
                if slot["record"] is not None:
                    if slot.get("action") is None:
                        self.require_allocation_active()
                        handoff.verify(slot["handoff"])
                        return self.issue_allocation(identity, slot)
                    if slot["record"]["status"] in {"received", "accepted", "not-created"}:
                        return _view(s, {"operation": "allocation-result", "allocation_id": identity, "result": slot["result"]}, acknowledged=True)
                    return _view(s, {"operation": "lookup-exact-allocation", "allocation_id": identity,
                                     "request": slot["record"]["request"], "checkpoint": str(self.path)}, acknowledged=True)
            else:
                self.require_allocation_active()
                request = copy.deepcopy(data["handoff"])
                require(request["role"] == "execution-agent" and request["stage"] == 3, "role_mismatch", "execution-agent input required")
                request.setdefault("requirement", s["handoff"]["requirement"])
                request.setdefault("predecessor", None)
                request.setdefault("binding", s["handoff"]["binding"])
                request.setdefault("target", s["handoff"]["target"])
                request.setdefault("delivery", s["handoff"]["delivery"])
                require(request["binding"] == s["handoff"]["binding"] and request["target"] == s["handoff"]["target"],
                        "binding_changed", "allocation must retain its dispatcher's flow")
                retain_entry_pins(request, s["packages"])
                actor = request["expected_entry"]["actor"]
                require(actor["role"] == "implementation-dispatcher" and self.bound_ref() in
                        {actor["thread_id"], "codex-thread:" + actor["thread_id"], actor.get("actor_ref")},
                        "identity_mismatch", "only the bound native dispatcher allocates execution agents")
                saved = handoff.handle(request)
                parent_scope, scope = s["handoff"]["scope"], saved["scope"]
                require(scope["baseline"] == parent_scope["baseline"] and
                        scope["implementation_paths"] == parent_scope["implementation_paths"] and
                        scope["closure_paths"] == parent_scope["closure_paths"] and
                        set(scope["owned_paths"]) <= set(parent_scope["implementation_paths"]) and
                        set(parent_scope["protected_paths"]) <= set(scope["protected_paths"]),
                        "scope_changed", "allocation must retain parent baseline and protections")
                slot = {"input": copy.deepcopy(data["handoff"]), "handoff": saved, "record": None, "owner_ref": self.bound_ref(),
                        "transaction": None, "observations": [], "result": None}
                slots[identity] = slot
                self.save()
        else:
            require(slot is not None and slot["record"] is not None, "allocation_missing", "exact saved allocation required")
            required = {"allocation_id", "operation", "decision"} if operation == "accept" else {"allocation_id", "operation", "receipt"}
            if operation == "receive": required.add("result")
            entry.fields(data, required)
            slot["observations"].append(copy.deepcopy(data))
            self.save()
        if operation == "prepare":
            self.require_allocation_active()
        request = {"protocol": handoff.PROTOCOL, "operation": operation, "control": copy.deepcopy(s["control"])}
        if operation == "prepare":
            request["handoff"] = slot["handoff"]
        else:
            request["record"] = slot["record"]
            request.update({key: data[key] for key in ("receipt", "result", "decision") if key in data})
            if operation == "accept":
                entry.fields(data["decision"], {"reference"}, {"delivery_digest"})
                require(slot["record"]["delivery"] is not None, "result_incomplete", "receive this allocation's result first")
                request["decision"] = {"delivery_digest": slot["record"]["delivery"]["digest"], **data["decision"]}
        # These are the same B operations/authority, not an alternate allocator.
        slot["transaction"] = copy.deepcopy(request)
        self.save()
        result = dispatch.handle(request)
        if "checkpoint" in result:
            s["control"]["context"] = result["checkpoint"]["context"]
            s["control_receipt"] = result["checkpoint"].get("discussion_receipt")
            self.next_envelope(s["control_receipt"])
        slot["record"] = result.get("record", slot["record"])
        slot["transaction"], slot["result"] = None, result
        if s.get("stop_requested") == "cancelling" and s["stopped"]:
            if all(item["stopped"] for item in s["control"]["context"]["handoff_progress"]["executions"]):
                s["status"], s["stop_effects"] = "cancelled", []
        self.save()
        if operation == "prepare":
            require(slot["record"] is not None, "outcome_unknown", "recover the original allocation reservation")
            return self.issue_allocation(identity, slot)
        else:
            action = {"operation": "allocation-result", "allocation_id": identity, "result": result,
                      "checkpoint": str(self.path)}
        return _view(s, action, acknowledged=result.get("acknowledged", False))

    def issue_allocation(self, identity, slot):
        self.require_allocation_active()
        action = {"action_id": str(uuid.uuid4()), "operation": "invoke-host", "allocation_id": identity,
                  "payload": slot["record"]["request"], "checkpoint": str(self.path)}
        slot["action"] = action
        self.save()
        return _view(self.state, action)

    def require_allocation_active(self):
        self.require_not_stopping()

    def suspend(self, cancel=False):
        s = self.state
        if cancel:
            for saved in s.get("deferred_decisions", {}).values():
                saved["disposition"] = "cancelled"
        if s["status"] == "cancelled":
            if cancel and s.get("stop_requested") != "cancelling":
                s["stop_requested"] = "cancelling"
                self.save()
            return _view(s, acknowledged=True)
        if s.get("stop_requested") == "cancelling" or s["status"] == "cancelling":
            if s["status"] != "cancelling":
                s["status"] = "cancelling"
                self.save()
            return _view(s, acknowledged=True)
        if not cancel and s["status"] in {"pausing", "paused"}:
            return _view(s, acknowledged=True)
        require(s["status"] not in {"accepted", "cancelled"}, "attempt_ended", "accepted results cannot be cancelled or paused")
        s["suspended_step"] = s["step"]
        s["status"] = "cancelling" if cancel else "pausing"
        s["stop_requested"] = s["status"]
        if s["dispatch"] is None or s["step"] == "launch":
            if cancel and s["dispatch"] is not None:
                self.cancel_control()
            else:
                s["status"] = "cancelled" if cancel else "paused"
            s["stopped"] = True
        elif s["stopped"]:
            if cancel:
                self.cancel_control()
            else:
                s["status"] = "paused"
        elif self.bound_ref() is not None:
            s["step"] = "bound"
        self.save()
        return self.advance()

    def resume(self):
        s = self.state
        if self.outer.get("runner_request", {}).get("operation") in {"pause", "cancel"}:
            return self.advance_stop(self.stop_intent())
        if s.get("recovery_action"):
            return _view(s)
        for identity, slot in s.get("allocations", {}).items():
            if slot.get("transaction") is not None:
                request = slot["transaction"]
                operation = request["operation"]
                data = {"allocation_id": identity, "operation": operation}
                data.update({"handoff": slot["input"]} if operation == "prepare" else
                            {key: request[key] for key in ("receipt", "result", "decision") if key in request})
                return _view(s, {"operation": "allocation-recovery", "ref": slot["owner_ref"], "data": data,
                                 "checkpoint": str(self.path)})
        pending_control = [value for value in s.get("control_transactions", {}).values() if value["result"] is None]
        require(len(pending_control) <= 1, "control_outcome_unknown", "reconcile each exact outstanding control operation before advancing")
        if pending_control:
            return self.control_action(pending_control[0]["request"])
        if s.get("stop_requested") == "cancelling":
            if s["status"] == "cancelled":
                return _view(s, acknowledged=True)
            s["status"] = "cancelling"
            self.save()
            return self.advance()
        require(s["status"] not in {"cancelled", "cancelling"}, "attempt_ended", "cancelled writers require the existing controller recovery protocol")
        if self.stop_intent() is None:
            self.recover_publication_intake_step()
        if s["stage"] == 4 and s["status"] == "blocked" and s.get("blocked_from") == "accepted":
            verify_completion({**s, "status": "accepted"})
            s["status"] = "accepted"
            s.pop("error", None)
            self.save()
            return _view(s, acknowledged=True)
        if s["status"] == "accepted":
            return _view(s, acknowledged=True)
        if self.stop_intent() == "pausing" and s["status"] != "paused":
            return self.advance_stop("pausing")
        if (s["status"] == "needs_input" and s["transaction"] is None and
                (s.get("publication") or {}).get("intake") is not None and
                s["dispatch"]["status"] == "received" and (s.get("pending") or {}).get("kind") == "acceptance"):
            return _view(s, acknowledged=True)
        require(s["status"] != "needs_input" or s["transaction"] is not None, "decision_required", "answer the exact pending matter")
        if s["transaction"] is not None:
            transaction = copy.deepcopy(s["transaction"])
            intake = (s.get("publication") or {}).get("intake")
            if transaction["operation"] == "receive" and intake is not None and transaction["result"] == intake["result"]:
                return self.receive_publication()
            if transaction["operation"] in {"bind", "reconcile", "receive"}:
                matches = [o for o in s["observations"] if o["receipt"] == transaction["receipt"]]
                require(matches, "observation_missing", "recover the saved original host response")
                return self.observe(matches[-1])
            if transaction["operation"] == "accept":
                pending = s["pending"]
                require(pending is not None and pending["kind"] == "acceptance", "decision_required", "recover the original controller acceptance")
                return self.decide({"decision_id": pending["decision_id"], "subject": pending["subject"],
                                    "answer": "accept", "reference": transaction["decision"]["reference"]})
            # Replays retain the original ledger revisions/idempotency key.
            self.apply(dispatch.handle(transaction))
            operation = transaction["operation"]
            s["step"] = {"prepare": "launch", "receive": "received", "accept": "accepted",
                         "bind": "bound", "reconcile": "bound"}[operation]
            if operation == "prepare" and s["dispatch"] is None:
                return self.block(entry.PreparationError("outcome_unknown", "reserved launch has no saved request; recover original owner evidence"))
            if operation in {"bind", "reconcile"} and s["dispatch"]["status"] != "bound":
                s["step"] = "host-response"
            if operation == "accept":
                s["status"] = "accepted"
                s["pending"] = stage_boundary(s["mode"], s["stage"], s["accepted"])
        if s["status"] == "paused":
            s.pop("stop_requested", None)
            if s.get("publication", {}).get("acceptance_decision") and s["dispatch"]["status"] == "accepted":
                self.recover_publication_intake_step()
                return self.advance()
            if s.get("pending") is not None:
                s["status"] = "needs_input"
                self.save()
                deferred = s.get("deferred_decisions", {}).get(s["pending"]["decision_id"])
                if deferred is not None and deferred["disposition"] == "paused":
                    return self.decide(deferred["decision"])
                return _view(s)
            if s.get("deferred_observation") is not None:
                observed = s.pop("deferred_observation")
                s["events"][observed["event_id"]]["applied"] = False
                s["status"] = "active"
                self.save()
                return self.observe(observed)
            # Stopped is not resumable. Host must first prove the same carrier can continue.
            s["step"] = s.get("suspended_step", "bound")
            s["status"] = "active"
            if s["dispatch"] and s["step"] not in {"launch", "prepare-dispatch", "received", "publication-ready", "publication-complete"} and not s.get("publication"):
                s["step"] = "bound"
                s["awaiting_resume"] = True
                s["resume_intent"] = {"operation": "resume-original-paused-scope", "subject": self.subject()}
        if s.get("publication") is not None and s["publication"].get("result") is None:
            return self.reconcile_publication(resume=True)
        if s["step"] == "publication-complete":
            return self.receive_publication()
        if s["step"] == "publication-ready":
            return self.publication(_view(s)["next_action"]["data"])
        if s["step"] == "technical-error":
            s["step"] = "bound"
        if s["status"] != "accepted":
            s["status"] = "active"
        s.pop("error", None)
        self.save()
        return self.advance()


def prepare_requirement(path, outer, request):
    """Persist A's exact intent before writes, and reconcile that same intent."""
    entry.fields(request, {"request"})
    original = copy.deepcopy(request["request"])
    require(original.get("protocol") == requirement.PROTOCOL and original.get("operation") in {"prepare", "verify"},
            "invalid_request", "supply A's complete prepare or verify request")
    root = original["entry"]["host"]["project_path"]
    require(not Path(path).resolve().is_relative_to(Path(root).resolve()), "unsafe_checkpoint", "checkpoint must be outside the project")
    identity = entry.digest(original)
    records = outer.setdefault("workflow_requirements", {})
    saved = records.get(identity)
    if saved is None:
        saved = {"request": original, "intent": None, "result": None, "error": None}
        records[identity] = saved
        atomic_save(path, outer)
    try:
        if original["operation"] == "verify":
            saved["result"] = requirement.handle(original)
        else:
            if saved["intent"] is None:
                saved["intent"] = requirement.handle(original)
                atomic_save(path, outer)
            operation = "reconcile" if saved.get("issued") else original["purpose"]
            saved["issued"] = True
            atomic_save(path, outer)
            outcome = requirement.handle({"protocol": requirement.PROTOCOL, "operation": operation,
                            "entry": original["entry"], "intent": saved["intent"]})
            if operation == "reconcile" and outcome.get("state") == "prepared":
                outcome = requirement.handle({"protocol": requirement.PROTOCOL, "operation": original["purpose"],
                                "entry": original["entry"], "intent": saved["intent"]})
            saved["result"] = outcome
        saved["error"] = None
    except entry.ERROR_TYPES as error:
        saved["error"] = {"code": getattr(error, "code", "requirement_failed"), "message": entry.error_message(error),
                          "completed_evidence": getattr(error, "completed_evidence", []), "downstream_ready": False}
        atomic_save(path, outer)
        return {"protocol": PROTOCOL, "status": "blocked", "transaction": identity, "error": saved["error"], "downstream_ready": False}
    atomic_save(path, outer)
    return {"protocol": PROTOCOL, "status": "requirement-ready", "transaction": identity, "result": saved["result"]}


def lifecycle(path, outer, data):
    """Retain original actor/envelope; only source completion chains finalize."""
    entry.fields(data, {"request"})
    request = copy.deepcopy(data["request"])
    allowed = {"claim-phase-carrier", "phase-ready", "phase-activate", "claim-phase-completion",
               "complete-phase-run", "finalize-phase-run", "reconcile-phase-run", "read-phase-run"}
    require(request.get("operation") in allowed, "invalid_operation", "use an existing phase carrier/source operation")
    root = request["project_path"]
    require(not Path(path).resolve().is_relative_to(Path(root).resolve()), "unsafe_checkpoint", "checkpoint must be outside the project")
    if request["operation"] in {"complete-phase-run", "finalize-phase-run"}:
        state = outer.get(KEY)
        require(state is not None and state["status"] == "accepted", "result_incomplete", "accept B's result before source phase completion")
        phase = state["handoff"]["authorization"].get("phase")
        if phase is not None:
            require(request["phase_run_id"] == phase["run_id"] and request["attempt_id"] == phase["attempt_id"],
                    "identity_mismatch", "phase completion must name the accepted stage attempt")
    records = outer.setdefault("workflow_lifecycle", {})
    identity = entry.digest(request)
    saved = records.setdefault(identity, {"request": request, "result": None, "finalize": None})
    atomic_save(path, outer)
    # The ledger authenticates actor, binding, revisions, gates and exact replay.
    # Replaying the same envelope reads its original idempotent receipt.
    saved["result"] = entry.discussion_protocol.handle(copy.deepcopy(saved["request"]))
    atomic_save(path, outer)
    if request["operation"] == "complete-phase-run":
        if saved["finalize"] is None:
            result = saved["result"]
            saved["finalize"] = {**request, "operation": "finalize-phase-run",
                "expected_ledger_revision": result["ledger_revision"], "expected_topic_revision": result["record_revision"],
                "idempotency_key": str(uuid.uuid4())}
            atomic_save(path, outer)
        saved["finalized"] = entry.discussion_protocol.handle(copy.deepcopy(saved["finalize"]))
        atomic_save(path, outer)
    if request["operation"] in {"complete-phase-run", "finalize-phase-run"}:
        verify_phase_completed(outer[KEY])
        if not outer[KEY]["phase_complete"]:
            state = outer[KEY]
            state["pending"] = stage_boundary(state["mode"], state["stage"], state["accepted"], state["next_stage"])
        outer[KEY]["phase_complete"] = True
        outer[KEY]["revision"] += 1
        atomic_save(path, outer)
    state = outer.get(KEY)
    action = (state or {}).get("action")
    if action and action["operation"] in {"source-lifecycle", "carrier-lifecycle"} and action["payload"]["request"] == request:
        action["receipt"] = copy.deepcopy(saved)
        state["step"] = "accepted" if state["status"] == "accepted" else action["payload"].get("return_step", "bound")
        state["revision"] += 1
        atomic_save(path, outer)
    return {"protocol": PROTOCOL, "status": "lifecycle-observed", "transaction": identity, "result": saved}


def handle(path, request):
    entry.fields(request, {"protocol", "operation", "expected_revision"}, {"data"})
    require(request["protocol"] == PROTOCOL, "unsupported_protocol", "unsupported progression protocol")
    require(type(request["expected_revision"]) is int, "invalid_request", "exact revision required")
    path = Path(path)
    require(path.is_absolute(), "invalid_checkpoint", "absolute checkpoint path required")
    with record_lock(path):
        outer = read_record(path)
        require(outer.get("version") not in {1, 2}, "legacy_run_requires_original_runtime", "retain the original runner and record")
        if request["operation"] == "prepare-requirement":
            return prepare_requirement(path, outer, request.get("data", {}))
        if request["operation"] == "lifecycle":
            return lifecycle(path, outer, request.get("data", {}))
        owner = Progress(path, outer)
        if owner.state is not None:
            validate_state(owner.state)
        actual = owner.state["revision"] if owner.state else 0
        operation = request["operation"]
        if operation == "inspect":
            require(owner.state is not None, "missing_checkpoint", "no progression checkpoint")
            return _view(owner.state)
        # Exact duplicate observations/decisions may ACK even with an old revision.
        data = request.get("data", {})
        duplicate = owner.state is not None and ((operation == "observe" and
            owner.state["events"].get(data.get("event_id"), {}).get("applied")) or
            (operation == "decide" and (data.get("decision_id") in owner.state.get("decisions", {}) or
             owner.state.get("deferred_decisions", {}).get(data.get("decision_id"), {}).get("decision") == data)))
        require(actual == request["expected_revision"] or duplicate, "stale_checkpoint", "read the current checkpoint before changing it")
        if operation == "start":
            return owner.start(data)
        require(owner.state is not None, "missing_checkpoint", "start a progression checkpoint first")
        try:
            if operation == "advance": return owner.advance()
            if operation == "observe": return owner.observe(data)
            if operation == "decide": return owner.decide(data)
            if operation == "publication": return owner.publication(data)
            if operation == "reconcile-publication":
                entry.fields(data, set())
                return owner.reconcile_publication()
            if operation == "receive-publication":
                entry.fields(data, set())
                return owner.receive_publication()
            if operation == "control": return owner.control_action(data)
            if operation == "allocation": return owner.allocation(data)
            if operation == "pause": return owner.suspend()
            if operation == "cancel": return owner.suspend(cancel=True)
            if operation == "resume": return owner.resume()
            raise entry.PreparationError("invalid_operation", "unknown progression operation")
        except entry.ERROR_TYPES + (control.ControlError,) as error:
            if operation == "decide" and owner.stop_intent() is not None and getattr(error, "code", None) in {"stale_decision", "decision_conflict", "invalid_request"}:
                raise
            if owner.state["status"] in {"accepted", "cancelled"} or getattr(error, "code", None) == "progression_suspended":
                raise
            return owner.block(error)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    args = parser.parse_args()
    try:
        request = entry.decode(sys.stdin.buffer.read(entry.MAX_BYTES + 1))
        result = handle(args.checkpoint, request)
        print(json.dumps({"ok": result["status"] != "blocked", "result": result}, ensure_ascii=False))
        return 1 if result["status"] == "blocked" else 0
    except entry.ERROR_TYPES + (control.ControlError,) as error:
        print(json.dumps({"ok": False, "error": {"code": getattr(error, "code", "progress_failed"),
            "message": entry.error_message(error), "downstream_ready": False}}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
