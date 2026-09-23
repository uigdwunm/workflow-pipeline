#!/usr/bin/env python3
"""Bounded stage inputs. Git proves bytes; the host authenticates provenance.

No task creation, publication, persistence, or route advancement occurs here.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import entry_prepare as entry
import requirement_prepare as requirement
import model_inventory
import workflow_control as control
import workflow_control_git as control_git

PROTOCOL = "workflow-stage-transfer-v5"
ROLES = {0: "dedicated-discussion", 1: "dedicated-problem-framing", 2: "solution-designer",
         3: "implementation-dispatcher", 4: "closure-agent"}
FIELDS = {"protocol", "entry", "expected_entry", "stage", "role", "requirement", "predecessor",
          "target", "delivery", "binding", "scope", "authorization", "configuration", "semantic"}


def require(condition, code, message):
    entry.require(condition, code, message)


def seal(value):
    result = copy.deepcopy(value)
    result["digest"] = entry.digest(result)
    return result


def unseal(value):
    require(isinstance(value, dict) and "digest" in value, "invalid_evidence", "complete saved evidence required")
    body = {k: v for k, v in value.items() if k != "digest"}
    require(value["digest"] == entry.digest(body), "invalid_evidence", "saved evidence changed")
    return body


def commit(value):
    require(isinstance(value, str) and re.fullmatch("[0-9a-f]{40}", value), "invalid_commit", "full commit required")
    return value


def strings(value):
    require(isinstance(value, list) and 0 < len(value) <= 128, "invalid_request", "nonempty bounded text list required")
    for item in value:
        entry.nonempty(item)
    return value


def paths(value, root):
    control.paths(value, empty=True)
    require(value == sorted(set(value)), "invalid_scope", "paths must be sorted and unique")
    for relative in value:
        entry.document_path(root, relative)
    return value


def blob(root, revision, relative):
    commit(revision)
    entry.document_path(root, relative)
    record = entry.git(root, "ls-tree", "-z", revision, "--", relative).stdout
    require(record.startswith(b"100644 blob ") or record.startswith(b"100755 blob "),
            "invalid_document", "committed regular file required")
    return entry.git(root, "show", revision + ":" + relative).stdout


def mode(root, revision, relative):
    commit(revision)
    entry.document_path(root, relative)
    return entry.git(root, "ls-tree", "-z", revision, "--", relative).stdout.split(b" ", 1)[0]


def ancestor(root, older, newer):
    entry.git(root, "merge-base", "--is-ancestor", commit(older), commit(newer))


def target_head(target, common):
    entry.fields(target, {"repository", "branch"})
    facts = entry.repository_facts(target["repository"])
    require(facts.get("git_common_dir") == common, "target_mismatch", "target belongs to another Git repository")
    branch = entry.nonempty(target["branch"])
    entry.git(target["repository"], "check-ref-format", "refs/heads/" + branch)
    return entry.git_text(target["repository"], "rev-parse", "refs/heads/" + branch)


def refresh(request, expected, *, after_work=False):
    """Only result intake permits the originally authorized work to move HEAD."""
    require(request.get("operation") == "resolve", "invalid_request", "original resolve request required")
    require(expected.get("evidence_digest") == entry.digest({k: v for k, v in expected.items() if k != "evidence_digest"}),
            "invalid_evidence", "complete A entry evidence required")
    if after_work:
        current = entry.resolve({**request, "pinned_packages": expected["packages"]})
        # A's existing immutable owner/config/package binding, without its
        # prepare-time HEAD/source-byte comparison (those legitimately change).
        requirement.bind(current, expected)
        require(current["entry"] == expected["entry"] and current["target"] == expected["target"],
                "entry_changed", "stage or target changed")
        require(current["requirement"]["kind"] == expected["requirement"]["kind"],
                "source_changed", "source kind changed")
    else:
        current = entry.resolve({**request, "operation": "verify", "expected": expected,
                                 "pinned_packages": expected["packages"]})
    return current


def source(current, evidence, stage):
    root = entry.discussion_root(current) if current["requirement"]["kind"] == "discussion" else current["repository"]["root"]
    if evidence is None and stage < 2 and current["requirement"]["kind"] == "discussion":
        topic = entry.topic_read(root, current["requirement"]["attachment"])
        require(topic["pending_document_write_count"] == 0, "source_changed", "reconcile pending discussion writes before dispatch")
        return {"path": Path(topic["topic_document_path"]).relative_to(root).as_posix(),
                "sha256": topic["topic_document_sha256"], "version": topic["record_revision"]}, None
    require(isinstance(evidence, dict) and evidence.get("protocol") == requirement.PROTOCOL,
            "invalid_source", "complete successful A requirement result required")
    if evidence.get("source_kind") == "discussion":
        actual = requirement.attached_result(current, evidence["checkpoint"]["checkpoint_id"])
        require(actual == evidence, "source_changed", "discussion checkpoint changed")
        return actual["requirement_identity"], actual["commit"]
    requirement.validate_seal(evidence)
    require(evidence.get("kind") in ({"document", "frozen"} if stage < 2 else {"frozen"}),
            "invalid_source", "downstream work requires a successful frozen source")
    require(evidence["entry"]["repository"].get("git_common_dir") == current["repository"].get("git_common_dir"),
            "repository_changed", "requirement belongs to another repository")
    identity = {k: evidence[k] for k in ("path", "sha256", "version")}
    require(type(identity["version"]) is int and identity["version"] > 0,
            "invalid_source", "positive requirement version required")
    require(current["requirement"].get("path") == identity["path"], "source_changed", "entry must select the exact requirement")
    data = entry.read_document(root, identity["path"])
    require(data is not None and hashlib.sha256(data).hexdigest() == identity["sha256"],
            "source_changed", "requirement bytes changed")
    revision = evidence.get("commit")
    if evidence["kind"] == "frozen":
        require(mode(root, revision, identity["path"]) == b"100644", "invalid_document", "requirement must retain its regular document mode")
        require(blob(root, revision, identity["path"]) == data, "source_changed", "source commit differs from frozen bytes")
        require(evidence["requirement_identity"] == identity, "source_changed", "frozen identity differs")
    return identity, revision


def verify_discussion_binding(current, binding):
    if current["requirement"]["kind"] != "discussion" or binding is None:
        return
    root = entry.discussion_root(current)
    facts = entry.repository_facts(binding["repository"])
    require(facts["kind"] == "git" and facts["git_common_dir"] == current["discussion_project"]["git_common_dir"]
            and binding["git_common_dir"] == facts["git_common_dir"],
            "discussion_identity_conflict", "Flow binding differs from discussion store")
    require(not Path(root).is_relative_to(Path(binding["worktree"])),
            "discussion_identity_conflict", "disposable Flow cannot own the persistent discussion")


def phase_evidence(saved, *, role_ref=None, receiving=False):
    source_request = saved["entry"]["source"]
    if source_request["kind"] != "discussion":
        require("phase" not in saved["authorization"], "authority_missing", "standalone input cannot claim a discussion phase")
        return None
    root = entry.discussion_root(saved["expected_entry"])
    verify_discussion_binding(saved["expected_entry"], saved.get("binding"))
    attachment = source_request["attachment"]
    require(attachment == saved["expected_entry"]["requirement"]["attachment"],
            "discussion_identity_conflict", "phase attachment differs from pinned entry")
    topic = entry.topic_read(root, attachment)
    require(topic["pending_document_write_count"] == 0, "source_changed", "pending discussion writes block stage transfer")
    if saved["stage"] < 2:
        require(topic["derived_gate_state"] == "open", "gate_closed", "discussion dependency gate is closed")
        return None
    phase = saved["authorization"].get("phase")
    entry.fields(phase, {"run_id", "attempt_id"})
    result = entry.discussion_protocol.handle({"protocol_version": 1, "operation": "read-phase-run", "project_path": root,
                                               **attachment, "phase_run_id": phase["run_id"]})
    run = result["phase_run"]
    matches = [a for a in run["attempts"] if a["attempt_id"] == phase["attempt_id"]]
    require(run["source_topic_id"] == attachment["actor_topic_id"] and run["to_phase"] == saved["stage"] and len(matches) == 1,
            "authority_missing", "phase run does not authorize this stage")
    attempt = matches[0]
    eligible = {"active", "completion-claimed", "completion-pending", "completed"} if receiving else {"setup-pending", "ready", "active"}
    require(attempt["state"] in eligible and run["state"] not in {"cancelled", "failed", "outcome-unknown"},
            "authority_missing", "phase attempt is not eligible for this action")
    if saved["stage"] == 2:
        require(run.get("wrapper_integration") is True and run["carrier_kind"] == "solution-designer" and
                run["source_checkpoint_id"] == saved["requirement"]["checkpoint"]["checkpoint_id"],
                "authority_missing", "designer must consume the exact authorized wrapper checkpoint")
        if role_ref is not None:
            require(attempt.get("authorization") is True and attempt.get("carrier_ref") == role_ref,
                    "identity_mismatch", "phase carrier differs from native result")
    else:
        actor = saved["expected_entry"]["actor"]
        require(attempt.get("authorization") is True and attempt.get("carrier_ref") in
                {actor["thread_id"], "codex-thread:" + actor["thread_id"], actor.get("actor_ref")},
                "identity_mismatch", "downstream phase must belong to the current stage carrier")
        require(receiving or attempt["state"] == "active", "authority_missing", "activate the stage before dispatching its executor")
    return {"run": run, "attempt": attempt, "topic": topic}


def delivery(request, current, identity, source_commit):
    """Read-only receiving contract; never cherry-pick or publish a source."""
    root = request["target"]["repository"]
    head = target_head(request["target"], current["repository"]["git_common_dir"])
    expected = identity["sha256"]
    require(mode(root, head, identity["path"]) == b"100644", "delivery_pending", "target requirement is absent or has an invalid mode")
    require(hashlib.sha256(blob(root, head, identity["path"])).hexdigest() == expected,
            "delivery_pending", "requirement is not delivered to the target branch")
    proof = request["delivery"]
    if proof is None:
        ancestor(root, source_commit, head)
        delivered = source_commit
    else:
        entry.fields(proof, {"commit", "paths", "receipt"})
        entry.nonempty(proof["receipt"])
        delivered = commit(proof["commit"])
        owned = paths(proof["paths"], root)
        require(identity["path"] in owned, "invalid_scope", "delivery must include the requirement")
        ancestor(root, delivered, head)
        parents = entry.git_text(root, "rev-list", "--parents", "-n", "1", delivered).split()
        require(len(parents) == 2, "delivery_unverified", "separate delivery must be a single-parent bounded commit")
        changed = set(entry.git(root, "diff", "--name-only", "-z", parents[1], delivered).stdout.decode().split("\0")) - {""}
        require(changed <= set(owned), "delivery_unverified", "delivery changes escape the owned document paths")
        for relative in owned:
            require(Path(relative).suffix.lower() == ".md", "invalid_scope", "requirement delivery owns Markdown only")
            require(blob(root, delivered, relative) == blob(root, source_commit, relative),
                    "delivery_unverified", "delivered document differs from the source")
            require(mode(root, delivered, relative) == mode(root, source_commit, relative),
                    "delivery_unverified", "delivered document mode differs from source")
    require(hashlib.sha256(blob(root, delivered, identity["path"])).hexdigest() == expected,
            "delivery_unverified", "delivery commit differs from frozen requirement")
    owned = proof["paths"] if proof else request["requirement"].get("owned_paths", [identity["path"]])
    requirement.verify_target_documents(root, head, source_commit, owned)
    return {"source_commit": source_commit, "delivery_commit": delivered, "target_head": head}


def verify_result(stage, payload, binding, scope, root, role_ref):
    """Verify completed Git facts. Semantic acceptance remains controller-owned."""
    common = {"artifacts", "checks"}
    extra = {0: {"requirement", "delivery"}, 1: {"requirement", "delivery"},
             2: {"planning_commit", "planning_merge_commit", "planning_paths"},
             3: {"candidate_commit", "review", "verification"},
             4: {"candidate_commit", "merge_commit", "changed_paths", "cleanup"}}[stage]
    entry.fields(payload, common | extra)
    strings(payload["artifacts"]); strings(payload["checks"])
    if stage < 2:
        return
    if stage != 4:
        control_git.verify_binding(Path(root), binding)
        require(not entry.git_text(root, "status", "--porcelain"), "dirty_worktree", "completed stage requires a clean Flow Worktree")
    head = target_head({"repository": binding["repository"], "branch": binding["target_branch"]}, binding["git_common_dir"])
    if stage == 2:
        planning = commit(payload["planning_commit"])
        merged = commit(payload["planning_merge_commit"])
        owned = paths(payload["planning_paths"], root)
        require(bool(owned) and set(owned) <= set(scope["owned_paths"]), "invalid_scope", "planning paths escape the prepared scope")
        ancestor(root, planning, merged); ancestor(root, merged, head)
        require(entry.git_text(root, "rev-parse", "HEAD") == merged, "result_changed", "Flow Worktree must retain the published planning commit")
        changed = set(entry.git(root, "diff", "--name-only", "-z", binding["base_commit"], planning).stdout.decode().split("\0")) - {""}
        require(changed <= set(owned), "invalid_scope", "planning commit escapes owned paths")
        for relative in owned:
            blob(root, planning, relative)
        protected_revision = merged
    elif stage == 3:
        candidate = commit(payload["candidate_commit"])
        require(entry.git_text(root, "rev-parse", "HEAD") == candidate, "result_changed", "candidate differs from actual HEAD")
        ancestor(root, binding["base_commit"], candidate)
        changed = set(entry.git(root, "diff", "--name-only", "-z", head, candidate).stdout.decode().split("\0")) - {""}
        require(changed <= set(scope["implementation_paths"]), "invalid_scope", "candidate escapes implementation scope")
        control.validate_review(candidate, payload["review"], payload["verification"], role_ref)
        require(payload["verification"]["expected_target_head"] == head, "target_changed", "validated target changed")
        control.validate_validation_plan(payload["verification"]["validation_plan"], binding)
        protected_revision = candidate
    else:
        primary = binding["repository"]
        candidate, merged = commit(payload["candidate_commit"]), commit(payload["merge_commit"])
        ancestor(primary, candidate, merged); ancestor(primary, merged, head)
        changed = set(entry.git(primary, "diff", "--name-only", "-z", candidate, merged).stdout.decode().split("\0")) - {""}
        require(changed <= set(scope["closure_paths"]), "invalid_scope", "closure changed implementation or protected paths")
        actual = sorted(set(entry.git(primary, "diff", "--name-only", "-z", entry.git_text(primary,"rev-parse",merged+"^1"), merged).stdout.decode().split("\0")) - {""})
        require(payload["changed_paths"] == actual, "result_changed", "closure paths differ from Git")
        registered = entry.git_text(primary, "worktree", "list", "--porcelain").splitlines()
        branch = entry.git(primary, "show-ref", "--verify", "--quiet", "refs/heads/" + binding["branch"], check=False)
        require(branch.returncode in {0, 1}, "git_failed", "cannot verify branch removal")
        cleanup = {"worktree_removed": not Path(binding["worktree"]).exists() and "worktree " + binding["worktree"] not in registered,
                   "branch_removed": branch.returncode == 1}
        require(payload["cleanup"] == cleanup and all(cleanup.values()), "cleanup_pending", "publication retained; cleanup is incomplete")
        root, protected_revision = primary, merged
    for relative in scope["protected_paths"]:
        require(blob(root, scope["baseline"], relative) == blob(root, protected_revision, relative) and
                mode(root, scope["baseline"], relative) == mode(root, protected_revision, relative),
                "source_changed", "protected source changed")


def prepare(request, selection_catalog=None):
    require(request.get("protocol") == PROTOCOL, "legacy_run_requires_original_runtime", "retain original stage-transfer runtime: " + str(request.get("protocol")))
    entry.fields(request, FIELDS | {"operation"})
    stage, role = request["stage"], request["role"]
    require(type(stage) is int and stage in ROLES and role in {ROLES[stage], "execution-agent" if stage == 3 else ROLES[stage]},
            "role_mismatch", "stage and role differ")
    current = refresh(request["entry"], request["expected_entry"])
    require(current["entry"]["stage"] == stage, "role_mismatch", "entry must be prepared for the target stage")
    verify_discussion_binding(current, request["binding"])
    scope = request["scope"]
    entry.fields(scope, {"baseline", "owned_paths", "protected_paths", "implementation_paths", "closure_paths"})
    root = current["repository"]["root"]
    for field in ("owned_paths", "protected_paths", "implementation_paths", "closure_paths"):
        paths(scope[field], root)
    active_writes = scope["owned_paths"] + scope["implementation_paths"]
    if stage != 3:
        active_writes += scope["closure_paths"]
    require(not set(scope["protected_paths"]) & set(active_writes),
            "invalid_scope", "write scope overlaps protected paths")
    require(not set(scope["implementation_paths"]) & set(scope["closure_paths"]), "invalid_scope", "implementation and closure ownership overlap")
    identity, source_commit = source(current, request["requirement"], stage)
    if stage >= 2:
        require(identity["path"] not in scope["closure_paths"], "invalid_scope", "frozen requirement is never closure-owned")
        if stage == 2:
            require(all(Path(p).suffix.lower() in {".md", ".mdx", ".rst", ".adoc", ".asciidoc", ".org", ".txt"}
                        or Path(p).name.lower() in {"readme", "changelog", "authors", "maintainers"} for p in scope["owned_paths"]),
                    "invalid_scope", "designer owns planning documents only")
        require(identity["path"] in scope["protected_paths"], "invalid_scope", "frozen requirement must be protected")
        commit(scope["baseline"])
        require(stage == 4 or current["repository"]["head"] == scope["baseline"], "baseline_changed", "scope baseline must be current HEAD")
        require(request["binding"] is not None and source_commit is not None, "binding_missing", "Git stages need an actual Flow Worktree and frozen Git source")
        control_git.verify_binding(Path(root), request["binding"])
        require(request["binding"]["repository"] == request["target"]["repository"] and
                request["binding"]["target_branch"] == request["target"]["branch"], "target_mismatch", "Flow Worktree and delivery target differ")
        delivered = delivery(request, current, identity, source_commit)
    else:
        require(request["binding"] is None and request["delivery"] is None, "invalid_request", "dedicated discussion preparation has no Flow Worktree delivery")
        delivered = None
    authority = request["authorization"]
    entry.fields(authority, {"reference", "flow_mode", "scope_digest"}, {"phase", "control_plan_id"})
    if "control_plan_id" in authority:
        require(stage < 2, "invalid_request", "control_plan_id selects a dedicated plan only")
        entry.nonempty(authority["control_plan_id"])
    entry.nonempty(authority["reference"])
    require(authority["flow_mode"] in {"stepwise", "continuous"} and authority["scope_digest"] == entry.digest(scope),
            "authorization_changed", "authorization must bind the exact scope and flow mode")
    semantic = request["semantic"]
    entry.fields(semantic, {"objective", "testing_basis", "completion_criteria", "constraints"}, {"validation_plan"})
    if stage == 3 and request["role"] == "implementation-dispatcher":
        control.validate_validation_plan(semantic.get("validation_plan"), request["binding"])
    entry.nonempty(semantic["objective"]); entry.nonempty(semantic["testing_basis"])
    strings(semantic["completion_criteria"]); strings(semantic["constraints"])
    phase_evidence(request)
    stage3_role = stage == 3 and role in {'implementation-dispatcher', 'execution-agent'}
    current_catalog = None
    if stage3_role:
        try:
            current_catalog = model_inventory.available_pairs()
        except model_inventory.ModelInventoryError as error:
            require(False, 'configuration_unavailable', str(error))
    if selection_catalog is not None:
        require(stage3_role and isinstance(selection_catalog, list) and
                all(isinstance(pair, list) and len(pair) == 2 and
                    all(isinstance(value, str) and value for value in pair)
                    for pair in selection_catalog),
                'invalid_evidence', 'invalid frozen Stage-3 model inventory')
    catalog_basis = (set(map(tuple, selection_catalog)) if selection_catalog is not None
                     else current_catalog)
    configuration = control.select_configuration(request["configuration"], catalog_basis)
    saved_catalog = None
    if stage3_role:
        advertised = current['actor'].get('supported_configurations')
        require(isinstance(advertised, list) and advertised,
                'configuration_unavailable', 'Stage-3 model selection requires the current native adapter inventory')
        available_pairs = {(item['model'], item['reasoning_effort']) for item in advertised}
        selected_pairs = {(item['model'], item['effort']) for item in request['configuration']['supported']}
        require(len(available_pairs) == len(advertised) and selected_pairs == available_pairs,
                'configuration_changed', 'selection must use the complete current native adapter inventory')
        require(request['configuration']['can_override'] or len(advertised) == 1,
                'configuration_changed', 'multiple native configurations require override support')
        saved_catalog = [list(pair) for pair in sorted(catalog_basis & available_pairs)]
        explicit_choice = (request['configuration']['user'] is not None or
                           configuration['source'] == 'confirmed')
        if request['operation'] == 'prepare' and not explicit_choice:
            eligible_defaults = control.stage3_eligible_defaults(
                request['configuration']['supported'],
                request['configuration']['required_capability'], current_catalog)
            require(not eligible_defaults or (configuration['model'], configuration['effort']) in eligible_defaults,
                    'configuration_changed', 'account-visible default requires a supported native choice')
        require((configuration['model'], configuration['effort']) in current_catalog,
                'configuration_unsupported', 'selected model is absent from the current account catalog')
    require(request["configuration"]["role"] == role and not configuration["needs_decision"],
            "configuration_changed", "role configuration requires a controller decision")
    predecessor = request["predecessor"]
    if predecessor is not None:
        prior = unseal(predecessor)
        require_current_protocol(prior)
        require(prior.get("status") == "accepted" and prior.get("downstream_ready") is True,
                "predecessor_incomplete", "an accepted B result is required")
        old = prior["handoff"]
        require(old["expected_entry"]["discussion_project"] == current["discussion_project"],
                "discussion_identity_conflict", "successor must retain the original discussion project")
        require((old["stage"], stage) in {(0, 1), (0, 2), (1, 2), (2, 3), (3, 4)}, "invalid_stage", "illegal stage transition")
        require(old["controller_ref"] == current["actor"]["controller_ref"] and prior["requirement_identity"] == identity,
                "source_changed", "predecessor controller or requirement differs")
        if old["stage"] >= 2:
            require(old["binding"] == request["binding"], "binding_changed", "successor must retain the exact Flow Worktree")
            verify_result(old["stage"], prior["payload"], old["binding"], old["scope"], root, prior["role_ref"])
            protected = set(old["scope"]["protected_paths"])
            if old["stage"] == 2:
                protected.update(prior["payload"]["planning_paths"])
                require(scope["closure_paths"] == old["scope"]["closure_paths"], "scope_changed", "retain the predecessor's authorized closure scope")
            if stage == 4:
                protected -= set(old["scope"]["closure_paths"])
            require(protected <= set(scope["protected_paths"]), "scope_changed", "successor must protect inherited planning and source paths")
            require(set(scope["implementation_paths"]) <= set(old["scope"]["implementation_paths"]),
                    "scope_changed", "implementation scope exceeds the accepted predecessor")
        if stage == 4:
            require(prior["payload"]["candidate_commit"] == current["repository"]["head"] and scope["baseline"] == old["scope"]["baseline"] and
                    old["scope"]["closure_paths"] == scope["closure_paths"], "scope_changed", "closure must retain accepted candidate and documentation scope")
    require(stage != 4 or predecessor is not None, "predecessor_incomplete", "inherited closure requires an accepted implementation result")
    return seal({**{k: copy.deepcopy(request[k]) for k in FIELDS}, "kind": "stage-input",
                 "controller_ref": current["actor"]["controller_ref"], "requirement_identity": identity,
                 "source_commit": source_commit, "delivery_facts": delivered, "selection": configuration,
                 "selection_catalog": saved_catalog})


def require_current_protocol(value):
    original = value.get('record') or value.get('handoff') or value
    saved = original.get('handoff', original)
    pins = {name:pin.get('bundle_digest') for name,pin in saved.get('expected_entry',{}).get('packages',{}).items()}
    require(value.get('protocol') == PROTOCOL, 'legacy_run_requires_original_runtime',
            'retain original stage-transfer runtime ' + str(value.get('protocol')) + '; package digests ' + str(pins))


def verify(saved):
    body = unseal(saved)
    require_current_protocol(body)
    entry.fields(body, FIELDS | {"kind", "controller_ref", "requirement_identity", "source_commit", "delivery_facts", "selection", "selection_catalog"})
    actual = prepare({**{k: body[k] for k in FIELDS}, "operation": "verify"}, body['selection_catalog'])
    require(actual == saved, "handoff_changed", "stage input facts changed; retain the original handoff")
    return actual


def render(saved):
    body = unseal(saved)
    require_current_protocol(body)
    if body.get("protocol") == PROTOCOL and body.get("status") == "accepted":
        original = body["handoff"]
        unseal(original)
        stage = original["stage"]
        require(body["downstream_ready"] is True and original["role"] != "execution-agent",
                "result_incomplete", "allocation acceptance is not stage completion")
        payload = copy.deepcopy(body["payload"])
        projection = {"controller_ref": original["controller_ref"], "role_ref": body["role_ref"],
                      "binding": original["binding"], "requirement_identity": body["requirement_identity"],
                      "source_commit": original["source_commit"], "accepted_transfer": saved,
                      "control_checkpoint": {"controller_ref": original["controller_ref"], "stage": stage,
                          "role_ref": body["role_ref"], "state": "completed", "role_kind": original["role"]}}
        projection.update({k: v for k, v in payload.items() if k not in {"artifacts", "checks"}})
        if stage == 2:
            projection.update(allowed_paths=original["scope"]["implementation_paths"], protected_paths=original["scope"]["protected_paths"])
        elif stage == 3:
            projection.update({k: original["scope"][k] for k in ("implementation_paths", "closure_paths", "protected_paths")})
            # This projection is the Stage-4 input. The accepted transfer above
            # retains Stage-3 read-only protection for implementation verification.
            projection["protected_paths"] = sorted(set(original["scope"]["protected_paths"]) - set(original["scope"]["closure_paths"]))
        elif stage == 4:
            projection["ancestor_verified"] = True
        text = json.dumps(projection, ensure_ascii=False, sort_keys=True, indent=2)
        result = {"payload": projection, "text": text, "downstream_ready": True}
        if stage >= 2:
            result["stage_result"] = {"result": "completed", "artifacts": payload["artifacts"], "evidence": payload["checks"],
                                      "handoff_json": text, "question": "", "message": "", "needs_input_kind": "none"}
        return result
    require(body.get("kind") == "stage-input" and body.get("protocol") == PROTOCOL, "invalid_evidence", "stage input required")
    # Stable machine payload is also the readable footer; no independently
    # reconstructed hashes, paths, or status assertions occur in templates.
    payload = {k: body[k] for k in ("stage", "role", "controller_ref", "requirement_identity", "source_commit",
                "target", "delivery_facts", "binding", "scope", "authorization", "selection", "semantic")}
    payload["packages"] = body["expected_entry"]["packages"]
    payload['registration_input'] = {key: copy.deepcopy(body['entry'][key]) for key in
        ('registry_input', 'registry', 'registry_context') if key in body['entry']}
    if not payload['registration_input']:
        payload['registration_input'] = {'registry': copy.deepcopy(body['expected_entry']['registry']),
            'registry_context': {k:body['expected_entry']['registration_context'][k] for k in
                ('project_path', 'project_id', 'controller_ref', 'receipt', 'registry_digest')}}
    payload['registration_input']['registration_identity'] = copy.deepcopy(body['expected_entry']['registration_context'])
    payload["discussion_project"] = body["expected_entry"]["discussion_project"]
    if payload["discussion_project"] is not None:
        payload["attachment"] = body["entry"]["source"]["attachment"]
    payload["input_digest"] = saved["digest"]
    if body['role'] == 'execution-agent':
        payload['write_authority'] = 'await-bound-release'
    return {"payload": payload, "text": json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
            "downstream_ready": False}


def handle(request):
    require(request.get("protocol") == PROTOCOL, "legacy_run_requires_original_runtime", "retain original stage-transfer runtime: " + str(request.get("protocol")))
    if request.get("operation") == "prepare":
        return prepare(request)
    entry.fields(request, {"protocol", "operation", "handoff"})
    require(request["operation"] in {"verify", "render"}, "invalid_operation", "unknown handoff operation")
    return verify(request["handoff"]) if request["operation"] == "verify" else render(request["handoff"])


def cli(handler):
    request = {}
    try:
        request = entry.decode(sys.stdin.buffer.read(entry.MAX_BYTES + 1))
        require(isinstance(request, dict), "invalid_request", "one JSON object required")
        result = handler(request)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, allow_nan=False))
        return 0
    except entry.ERROR_TYPES as error:
        operation = request.get("operation") if isinstance(request, dict) else None
        print(json.dumps({"ok": False, "error": {"code": getattr(error, "code", "verification_failed"),
              "operation": operation if operation in {"prepare", "verify", "render", "bind", "reconcile", "receive", "accept"} else None,
              "message": entry.error_message(error), "downstream_ready": False,
              "completed_evidence": getattr(error, "completed_evidence", []),
              "recovery": "Retain the original request, checkpoint and host receipts; reconcile the exact attempt before any new launch."}}))
        return 1


if __name__ == "__main__":
    sys.exit(cli(handle))
