#!/usr/bin/env python3
"""Bounded stage inputs. Git proves bytes; the host authenticates provenance.

No task creation, publication, persistence, or route advancement occurs here.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import entry_prepare as entry
import requirement_prepare as requirement
import stage_scope
import execution_plan
import workflow_control as control
import workflow_control_git as control_git
import supervision_protocol as supervision

PROTOCOL = "workflow-stage-transfer-v15"
ROLES = {0: "dedicated-discussion", 1: "dedicated-problem-framing", 2: "solution-designer",
         3: "implementation-dispatcher", 4: "closure-agent"}
FIELDS = {"protocol", "entry", "expected_entry", "stage", "role", "requirement", "predecessor",
          "target", "delivery", "binding", "scope", "authorization", "configuration", "semantic"}
OPTIONAL_FIELDS = {"readiness", "generated_outputs", "manifest_baseline", "inline_authority"}


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


def predecessor_digest(value):
    require(isinstance(value, dict), 'invalid_evidence', 'saved predecessor identity required')
    return value['accepted_digest'] if value.get('kind') in {'design-predecessor-ref', 'design-acceptance-ref', 'implementation-predecessor-ref', 'implementation-acceptance-ref'} else value['digest']


def predecessor_facts(value):
    """Resolve one explicitly named design; never hydrate an ancestor chain."""
    require(isinstance(value, dict), "invalid_evidence", "saved predecessor identity required")
    if value.get('kind') in {'implementation-predecessor-ref', 'implementation-acceptance-ref'}:
        import workflow_progress as progress
        return progress.implementation_acceptance(value)[0]
    if value.get('kind') == 'design-acceptance-ref':
        import workflow_progress as progress
        return progress.read_design_acceptance(value)
    if value.get('kind') != 'design-predecessor-ref':
        return unseal(value)
    import workflow_progress as progress
    return progress.read_design_predecessor(value, value['checkpoint_path'])['accepted']


def inline_input_digest(request, controller_ref, identity, source_commit, delivered, selection):
    """Seal the complete original B0 body before derived inline identity fields."""
    body = {k: copy.deepcopy(request[k]) for k in FIELDS}
    body["semantic"].pop("execution_plan", None)
    if "generated_outputs" in request:
        body["generated_outputs"] = copy.deepcopy(request["generated_outputs"])
    if "manifest_baseline" in request:
        body["manifest_baseline"] = request["manifest_baseline"]
    body.update(kind="stage-input", controller_ref=controller_ref,
                requirement_identity=copy.deepcopy(identity), source_commit=source_commit,
                delivery_facts=copy.deepcopy(delivered), selection=copy.deepcopy(selection))
    return seal(body)["digest"]


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


def blob(root, revision, relative, *, _documents=None):
    commit(revision)
    entry.document_path(root, relative)
    document = requirement._committed_document(root, revision, relative, _documents)
    record = document["tree"]
    require(record.startswith(b"100644 blob ") or record.startswith(b"100755 blob "),
            "invalid_document", "committed regular file required")
    return requirement._committed_bytes(root, revision, relative, document)


def mode(root, revision, relative, *, _documents=None):
    commit(revision)
    entry.document_path(root, relative)
    return requirement._committed_document(root, revision, relative, _documents)["tree"].split(b" ", 1)[0]


def ancestor(root, older, newer):
    entry.git(root, "merge-base", "--is-ancestor", commit(older), commit(newer))


def target_head(target, common, *, _binding_report=None):
    entry.fields(target, {"repository", "branch"})
    branch = entry.nonempty(target["branch"])
    if _binding_report is None:
        facts = entry.repository_facts(target["repository"])
        require(facts.get("git_common_dir") == common, "target_mismatch", "target belongs to another Git repository")
        entry.git(target["repository"], "check-ref-format", "refs/heads/" + branch)
    else:
        binding = _binding_report["binding"]
        require(binding["repository"] == target["repository"] and binding["target_branch"] == branch and
                binding["git_common_dir"] == common, "target_mismatch", "target differs from the verified Flow binding")
    return entry.git_text(target["repository"], "rev-parse", "refs/heads/" + branch)


def refresh(request, expected, *, after_work=False, _binding_to_verify=None):
    """Only result intake permits the originally authorized work to move HEAD."""
    require(request.get("operation") == "resolve", "invalid_request", "original resolve request required")
    require(expected.get("evidence_digest") == entry.digest({k: v for k, v in expected.items() if k != "evidence_digest"}),
            "invalid_evidence", "complete A entry evidence required")
    if after_work:
        current = entry.resolve({**request, "pinned_packages": expected["packages"]})
        # A's existing immutable owner/package binding, without its
        # prepare-time HEAD/source-byte comparison (those legitimately change).
        requirement.bind(current, expected)
        require(current["entry"] == expected["entry"] and current["target"] == expected["target"],
                "entry_changed", "stage or target changed")
        require(current["requirement"]["kind"] == expected["requirement"]["kind"],
                "source_changed", "source kind changed")
    else:
        verification = {**request, "operation": "verify", "expected": expected,
                        "pinned_packages": expected["packages"]}
        current = (entry._resolve(verification, binding_to_verify=_binding_to_verify)
                   if _binding_to_verify is not None else entry.resolve(verification))
    return current


def source(current, evidence, stage, *, attached_controller_entry=None):
    root = entry.discussion_root(current) if current["requirement"]["kind"] == "discussion" else current["repository"]["root"]
    if evidence is None and stage < 2 and current["requirement"]["kind"] == "discussion":
        topic = entry.topic_read(root, current["requirement"]["attachment"])
        require(topic["pending_document_write_count"] == 0, "source_changed", "reconcile pending discussion writes before dispatch")
        return {"path": Path(topic["topic_document_path"]).relative_to(root).as_posix(),
                "sha256": topic["topic_document_sha256"], "version": topic["record_revision"]}, None
    require(isinstance(evidence, dict) and evidence.get("protocol") == requirement.PROTOCOL,
            "invalid_source", "complete successful A requirement result required")
    if evidence.get("source_kind") == "discussion":
        original = current if current["requirement"]["kind"] == "discussion" else attached_controller_entry
        require(isinstance(original, dict) and original.get('requirement', {}).get('kind') == 'discussion' and
                original.get('repository', {}).get('git_common_dir') == current['repository'].get('git_common_dir'),
                'source_changed', 'attached requirement needs the original Controller source entry')
        actual = requirement.attached_result(original, evidence["checkpoint"]["checkpoint_id"])
        require(actual == evidence, "source_changed", "discussion checkpoint changed")
        if original is not current:
            identity = actual['requirement_identity']
            data = entry.read_document(current['repository']['root'], identity['path'])
            require(current['requirement'].get('kind') == 'frozen' and
                    current['requirement'].get('path') == identity['path'] and
                    current['requirement'].get('sha256') == identity['sha256'] and
                    actual['commit'] is not None and
                    data is not None and
                    hashlib.sha256(blob(current['repository']['root'], actual['commit'], identity['path'])).hexdigest() ==
                    identity['sha256'] and
                    mode(current['repository']['root'], actual['commit'], identity['path']) == b'100644' and
                    hashlib.sha256(data).hexdigest() == identity['sha256'],
                    'source_changed', 'Execution Agent frozen source differs from original attached checkpoint')
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


def phase_evidence(saved, *, role_ref=None, receiving=False, control_context=None):
    recovery = saved['authorization'].get('integration_recovery')
    if recovery is not None:
        entry.fields(recovery, {'closure', 'retained_commit', 'retained_paths'})
        closure = unseal(recovery['closure'])
        original = (None if closure.get('kind') == 'recovery-closure-facts' else predecessor_facts(closure['predecessor']))
        require(saved['stage'] == 3 and closure['stage'] == 4 and
                (original is None or original['handoff']['stage'] == 3) and closure['controller_ref'] == saved['controller_ref'] and
                closure['binding'] == saved['binding'] and
                closure['scope']['implementation_paths'] == saved['scope']['implementation_paths'] and
                closure['requirement_identity'] == saved['requirement_identity'],
                'authority_changed', 'final recovery must retain its exact closure authority')
        require(set(recovery['retained_paths']) <= set(closure['scope']['closure_paths']),
                'scope_changed', 'retained documents escape original closure scope')
        ancestor(saved['binding']['repository'], closure['candidate'] if original is None else original['payload']['candidate_commit'], recovery['retained_commit'])
        return phase_evidence(closure, receiving=receiving)
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
            carrier_ref = attempt.get("carrier_ref")
            # Downstream execution control is persisted in C, while this ledger
            # owns the original Phase Run. C/B pass their verified control slot;
            # no replacement is inferred from an unbound native identity.
            authority = control_context
            if authority is not None:
                control.validate_context(authority)
                slots = [authority] + ([authority['successor_control']] if authority.get('successor_control') else [])
                matching = [slot for slot in slots if slot['stage'] == 2 and
                    (slot.get('handoff_progress') or {}).get('design_input') == saved['digest']]
                require(len(matching) <= 1, "identity_mismatch", "ambiguous designer recovery authority")
                if matching:
                    selected = matching[0]
                    checkpoint = selected['handoff_progress']
                    recovery = checkpoint.get('recovery')
                    if recovery is not None:
                        require(selected['controller_ref'] == saved['controller_ref'] and
                                selected['topic_ref'] == attachment['actor_topic_id'] and
                                selected['requirement_identity'] == saved['requirement_identity'] and
                                checkpoint['binding'] == saved['binding'] and
                                checkpoint['allowed_paths'] == saved['scope']['owned_paths'] and
                                checkpoint['protected_paths'] == saved['scope']['protected_paths'],
                                "identity_mismatch", "designer recovery differs from original phase authority")
                        expected_attempt = control.digest({key: checkpoint[key] for key in
                            ('design_input', 'binding', 'allowed_paths', 'protected_paths', 'configuration')})
                        for recovered in [*checkpoint.get('recovery_history', []), recovery]:
                            require(recovered['state'] == 'activated' and recovered['dispatcher_ref'] == carrier_ref and
                                    recovered['attempt'] == expected_attempt and recovered['intent']['role'] == 'solution-designer',
                                    "identity_mismatch", "designer recovery lineage is not activated or contiguous")
                            carrier_ref = recovered['activation']['replacement_ref']
                            require(recovered['receipts'][-1]['status'] == 'ready' and
                                    recovered['receipts'][-1]['ref'] == carrier_ref,
                                    "identity_mismatch", "designer recovery lost the actual native replacement")
                            expected_attempt = control.digest([recovered['recovery_id'], carrier_ref])
                        require(selected['carrier'] == {'kind': 'solution-designer', 'ref': carrier_ref,
                                                       'attempt': expected_attempt},
                                "identity_mismatch", "designer recovery is not the current authorized carrier")
            require(attempt.get("authorization") is True and carrier_ref == role_ref,
                    "identity_mismatch", "phase carrier differs from native result")
    else:
        actor = saved["expected_entry"]["actor"]
        require(attempt.get("authorization") is True and attempt.get("carrier_ref") in
                {actor["thread_id"], "codex-thread:" + actor["thread_id"], actor.get("actor_ref")},
                "identity_mismatch", "downstream phase must belong to the current stage carrier")
        require(receiving or attempt["state"] == "active", "authority_missing", "activate the stage before dispatching its executor")
    return {"run": run, "attempt": attempt, "topic": topic}


def _replacement_view(root):
    """Object replacement refs/config are mutable even for a full commit ID."""
    flags = (os.environ.get("GIT_REPLACE_REF_BASE"), os.environ.get("GIT_NO_REPLACE_OBJECTS"))
    if flags[1] is not None:
        return flags, b"", None
    references = entry.git(root, "replace", "--format=medium", "--list").stdout
    setting = None
    if references:
        result = entry.git(root, "config", "--bool", "--get", "core.useReplaceRefs", check=False)
        require(result.returncode in (0, 1), "git_failed", "invalid Git replacement configuration")
        setting = (result.returncode, result.stdout)
    return flags, references, setting


def delivery(request, current, identity, source_commit, *, _binding_report=None):
    """Read-only receiving contract; never cherry-pick or publish a source."""
    root = request["target"]["repository"]
    head = (request['scope']['baseline'] if request.get('stage') == 3 else
            target_head(request["target"], current["repository"]["git_common_dir"], _binding_report=_binding_report))
    documents = {}
    replacements = _replacement_view(root)
    expected = identity["sha256"]
    require(mode(root, head, identity["path"], _documents=documents) == b"100644", "delivery_pending", "target requirement is absent or has an invalid mode")
    require(hashlib.sha256(blob(root, head, identity["path"], _documents=documents)).hexdigest() == expected,
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
            require(blob(root, delivered, relative, _documents=documents) == blob(root, source_commit, relative, _documents=documents),
                    "delivery_unverified", "delivered document differs from the source")
            require(mode(root, delivered, relative, _documents=documents) == mode(root, source_commit, relative, _documents=documents),
                    "delivery_unverified", "delivered document mode differs from source")
    require(hashlib.sha256(blob(root, delivered, identity["path"], _documents=documents)).hexdigest() == expected,
            "delivery_unverified", "delivery commit differs from frozen requirement")
    owned = proof["paths"] if proof else request["requirement"].get("owned_paths", [identity["path"]])
    requirement.verify_target_documents(root, head, source_commit, owned, _documents=documents)
    require(_replacement_view(root) == replacements, "source_changed", "Git object replacements changed during delivery")
    return {"source_commit": source_commit, "delivery_commit": delivered, "target_head": head}


def verify_result(stage, payload, binding, scope, root, role_ref, integration_recovery=None,
                  current_progress=None):
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
        current_head = entry.git_text(root, "rev-parse", "HEAD")
        require(current_head == merged, "result_changed", "Flow Worktree must retain the published planning commit")
        changed = set(entry.git(root, "diff", "--name-only", "-z", binding["base_commit"], planning).stdout.decode().split("\0")) - {""}
        require(changed <= set(owned), "invalid_scope", "planning commit escapes owned paths")
        for relative in owned:
            blob(root, planning, relative)
        protected_revision = merged
    elif stage == 3:
        candidate = commit(payload["candidate_commit"])
        control.keys(payload["verification"], control.DELIVERY_VERIFICATION_FIELDS)
        planning_paths = set()
        if current_progress is not None:
            require(current_progress['binding'] == binding and
                    current_progress['git_baseline_commit'] == scope['baseline'],
                    'scope_changed', 'current Controller authority belongs to another Flow')
            control.validate_deliverable(current_progress, candidate, payload['review'],
                                         payload['verification'], role_ref)
            effective = current_progress['execution_authority']['scope']
            scope = {**scope, 'implementation_paths': effective['implementation_paths'],
                     'closure_paths': effective['closure_paths'], 'downstream': effective['downstream']}
            planning_paths = control_git.verified_planning_paths(Path(root), current_progress)
        require(entry.git_text(root, "rev-parse", "HEAD") == candidate, "result_changed", "candidate differs from actual HEAD")
        ancestor(root, binding["base_commit"], candidate)
        review_base = commit(payload["verification"]["expected_target_head"])
        ancestor(root, scope["baseline"], review_base); ancestor(root, review_base, candidate)
        changed = set(entry.git(root, "diff", "--name-only", "-z", review_base, candidate).stdout.decode().split("\0")) - {""}
        retained_paths = set((integration_recovery or {}).get('retained_paths', []))
        changed -= retained_paths | planning_paths
        require(changed <= set(scope["implementation_paths"]), "invalid_scope", "candidate escapes implementation scope")
        history_paths = control_git.incoming_changed_paths(Path(root), review_base, candidate) - retained_paths - planning_paths
        retired_paths = set()
        if current_progress is not None and current_progress.get('plan_revisions'):
            retired_paths = control_git.verified_retired_history_paths(
                Path(root), current_progress, candidate, history_paths,
                current_progress['plan_revisions'][-1]['controller_ref'])
        require(history_paths - retired_paths <= set(scope["implementation_paths"]) and
                not history_paths & set(scope["protected_paths"]),
                "invalid_scope", "candidate history escapes implementation scope")
        control.validate_review(candidate, payload["review"], payload["verification"], role_ref)
        require(payload['verification']['coverage']['candidate'] == candidate, 'review_incomplete', 'Stage-3 result requires current complete review coverage')
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
        published = entry.git_text(primary, "rev-parse", merged + "^2")
        ancestor(primary, candidate, published)
        cleanup = supervision.verify_disposition(binding, published, merged)
        require(payload["cleanup"] == cleanup and cleanup["disposition_verified"],
                "cleanup_pending", "publication retained; workspace disposition is incomplete")
        root, protected_revision = primary, merged
    for relative in scope["protected_paths"]:
        if stage == 3 and relative in planning_paths:
            continue
        source_revision = (integration_recovery['retained_commit'] if integration_recovery is not None and
                           relative in integration_recovery['retained_paths'] else scope['baseline'])
        require(blob(root, source_revision, relative) == blob(root, protected_revision, relative) and
                mode(root, source_revision, relative) == mode(root, protected_revision, relative),
                "source_changed", "protected source changed")


def effective_scope(original_handoff, authority):
    """Check the sealed current scope against an immutable Stage-2 launch."""
    return effective_scope_facts(unseal(original_handoff), authority)


def effective_scope_facts(original, authority):
    """Check already authenticated launch facts; this does not unseal or grant authority."""
    require_current_protocol(original)
    require(original.get("kind") in {"stage-input", "design-launch-facts"} and original["stage"] == 2,
            "invalid_scope", "a Stage-2 input is required for scope finalization")
    require(isinstance(authority, dict) and authority.get("state") == "sealed" and
            type(authority.get("revision")) is int and authority["revision"] >= 1 and
            isinstance(authority.get("scope"), dict) and isinstance(authority.get("candidate_commit"), str),
            "scope_incomplete", "current sealed scope authority required; use scope-propose / scope-seal")
    scope = stage_scope.validate_scope(authority["scope"], original["expected_entry"]["repository"]["root"],
        stage=2, requirement_path=original["requirement_identity"]["path"],
        planning_paths=original["scope"]["owned_paths"], candidate_commit=authority["candidate_commit"],
        generated_outputs=authority_generated_outputs(authority))
    for key in ("baseline", "owned_paths", "protected_paths"):
        require(scope[key] == original["scope"][key], "scope_changed",
                "sealed scope changed the Stage-2 launch boundary: " + key)
    downstream = scope["downstream"]
    require(downstream["state"] == "sealed" and downstream["revision"] == authority["revision"] and
            downstream["seal_ref"] == authority.get("seal_ref") and
            downstream["proposal_ref"] == authority.get("proposal_digest"),
            "scope_changed", "sealed scope revision or references differ from current authority")
    if downstream["implementation_required"]:
        catalog = execution_plan.collect_execution_plan(
            original["expected_entry"]["repository"]["root"], authority["candidate_commit"],
            original["scope"]["owned_paths"], scope, original["requirement_identity"])
        require(execution_plan.current_catalog(authority) == catalog and
                authority.get("proposal", {}).get("execution_plan") ==
                {"plan_digest": catalog["plan_digest"], "contract_digest": catalog["contract_digest"]},
                "scope_changed", "sealed function plan differs from committed design")
    return scope


def current_readiness(readiness):
    """Read the original C checkpoint, never trust a caller's ready assertion."""
    import workflow_progress as progress
    return progress.read_scope_readiness(readiness)


def verify_inline_recovery_pin(request, controller_ref, identity, checkpoint_path):
    """Read the original C recovery and current inline authority, not a readiness claim."""
    import workflow_progress as progress
    require(isinstance(checkpoint_path, str), 'authority_missing',
            'inline final recovery requires the original Controller checkpoint')
    checkpoint = Path(checkpoint_path)
    require(checkpoint.is_absolute() and str(checkpoint.resolve()) == checkpoint_path,
            'invalid_checkpoint', 'canonical original Controller checkpoint required')
    state = progress.read_projection(checkpoint, projection='scope').record[progress.KEY]
    progress.validate_stage3_scope(state)
    recovery = request['authorization']['integration_recovery']
    current = state.get('integration_recovery')
    require(isinstance(current, dict), 'authority_missing', 'current recovery authority is missing')
    facts, _, _ = progress.read_implementation_delivery(checkpoint, current['predecessor'])
    carrier = current['carrier']
    require(state.get('checkpoint_path') == checkpoint_path and
            current['decision']['closure_handoff'] == recovery['closure'] and
            state['handoff']['authorization'].get('integration_recovery') == recovery and
            state['handoff']['controller_ref'] == controller_ref == facts['controller_ref'] and
            state['handoff']['requirement_identity'] == identity and
            state['handoff']['binding'] == request['binding'] == facts['handoff']['binding'] and
            state['handoff']['target'] == request['target'] and
            state['handoff']['requirement'] == request['requirement'] and
            state['scope_authority'] == request.get('inline_authority') ==
                state['execution_authority'] == facts['execution_authority'] and
            state['scope_authority'].get('kind') == 'inline' and
            request['scope']['implementation_paths'] == state['scope_authority']['scope']['implementation_paths'] and
            set(state['handoff']['scope']['protected_paths']) <= set(request['scope']['protected_paths']) and
            current['dispatch_attempt'] == carrier['attempt'] == facts['dispatch_attempt'] and
            carrier['ref'] == facts['dispatcher']['ref'] and
            request['expected_entry']['actor']['role'] == 'implementation-dispatcher' and
            carrier['ref'] in {request['expected_entry']['actor'].get('thread_id'),
                              request['expected_entry']['actor'].get('actor_ref'),
                              'codex-thread:' + request['expected_entry']['actor'].get('thread_id', '')} and
            current['source_digest'] is not None and
            current['retained'] == {'commit': recovery['retained_commit'], 'paths': recovery['retained_paths']},
            'authority_missing', 'inline allocation must retain the original captured C recovery')
    return state


def verify_stage_checkpoint_pin(saved, context, checkpoint_path):
    """Bind a B dispatch record to its original durable Controller checkpoint."""
    import workflow_progress as progress
    require(isinstance(checkpoint_path, str), 'invalid_checkpoint',
            'Controller checkpoint path is required')
    checkpoint = Path(checkpoint_path)
    require(checkpoint.is_absolute() and str(checkpoint.resolve()) == checkpoint_path,
            'invalid_checkpoint', 'canonical original Controller checkpoint required')
    outer = progress.read_projection(checkpoint, projection='scope').record
    state = outer.get(progress.KEY)
    require(isinstance(state, dict), 'handoff_not_ready', 'original Controller checkpoint is unavailable')
    if state.get('scope_kind') == 'stage3-scope-v1':
        progress.validate_stage3_scope(state)
        context_matches = state['control_digest'] == entry.digest(context)
    else:
        progress.validate_state(state)
        context_matches = state['control']['context'] == context
    require(state.get('stage') == saved.get('stage') and state.get('checkpoint_path') == checkpoint_path and
            state.get('handoff') == saved and context_matches and
            (saved.get('readiness') is None or
             saved['readiness']['checkpoint_path'] == checkpoint_path),
            'handoff_not_ready', 'checkpoint pin differs from the original B input')
    return state


def current_stage3_state(saved, context, checkpoint_path=None):
    """Read the active staged or inline authority without changing saved B bytes."""
    import workflow_progress as progress
    readiness = saved.get('readiness')
    require(saved.get('stage') == 3 and saved.get('role') == 'implementation-dispatcher',
            'handoff_not_ready', 'Stage-3 Dispatcher input required')
    if readiness is not None:
        original_path = readiness['checkpoint_path']
        require(checkpoint_path is None or checkpoint_path == original_path,
                'handoff_not_ready', 'Dispatcher checkpoint pin changed')
    else:
        require(checkpoint_path is not None, 'handoff_not_ready',
                'standalone revision needs the original Controller checkpoint pin')
        original_path = checkpoint_path
    checkpoint = Path(original_path)
    outer = progress.read_projection(checkpoint, projection='scope').record
    state = outer.get(progress.KEY)
    require(isinstance(state, dict), 'handoff_not_ready', 'original Controller checkpoint is unavailable')
    if state.get('scope_kind') == 'stage3-scope-v1':
        progress.validate_stage3_scope(state)
    else:
        progress.validate_state(state)
    if state.get('stage') == 2:
        require(readiness is not None, 'handoff_not_ready',
                'standalone Dispatcher cannot read a planning-stage checkpoint')
        progress.read_scope_readiness(readiness)
        return None
    state = verify_stage_checkpoint_pin(saved, context, original_path)
    effective = (progress.consumed_scope_readiness(state, checkpoint)
                 if readiness is not None else None)
    require((readiness is None or state.get('consumed_readiness') == readiness and
             effective is not None and state.get('scope_authority') == effective['scope_authority']) and
            state.get('scope_authority') == (context.get('handoff_progress') or {}).get('execution_authority') and
            (state.get('plan_revision_transaction') or {}).get('state') in (None, 'activated'),
            'handoff_not_ready', 'B result must use the current activated Controller authority')
    # The caller supplied and digest-verified this control; the read projection grants no lease.
    return {**state, 'control': {'context': context}}


def accepted_stage3_progress(predecessor, checkpoint_path=None):
    """Recover the exact accepted Stage-3 authority across Stage-4 replay."""
    import workflow_progress as progress
    prior = predecessor_facts(predecessor)
    saved = prior["handoff"]
    readiness = saved.get("readiness")
    if predecessor.get("kind") in {"implementation-predecessor-ref", "implementation-acceptance-ref"}:
        checkpoint_path = checkpoint_path or predecessor["checkpoint_path"]
    if readiness is None and checkpoint_path is None:
        inline = saved.get("inline_authority") or {}
        original_plan = inline.get("execution_plan") or {}
        initial = {"revision": inline.get("revision"),
            "authority_digest": entry.digest(inline),
            "plan_digest": original_plan.get("plan_digest"),
            "contract_digest": original_plan.get("contract_digest")}
        require(prior.get("payload", {}).get("verification", {}).get("plan_authority") == initial,
                "handoff_not_ready", "revised standalone acceptance needs its original Controller checkpoint pin")
        return None
    original_path = readiness["checkpoint_path"] if readiness is not None else checkpoint_path
    require(checkpoint_path is None or checkpoint_path == original_path,
            "handoff_not_ready", "Stage-4 checkpoint pin changed")
    checkpoint = Path(original_path)
    require(checkpoint.is_absolute() and str(checkpoint.resolve()) == original_path,
            "invalid_checkpoint", "canonical original Controller checkpoint required")
    reference = (predecessor if predecessor.get('kind') in
                 {'implementation-predecessor-ref', 'implementation-acceptance-ref'} else
                 progress.implementation_predecessor(checkpoint))
    accepted, current_progress = progress.implementation_acceptance(reference, original_path)
    require(accepted['digest'] == predecessor_digest(predecessor) and
            accepted['handoff']['digest'] == saved['digest'],
            'handoff_not_ready', 'Stage-4 predecessor differs from original Controller acceptance')
    return current_progress


def attached_source_entry(saved):
    """Read the original discussion source from C for a delegated executor."""
    if saved.get('role') != 'execution-agent' or saved.get('requirement', {}).get('source_kind') != 'discussion':
        return None
    readiness = saved.get('readiness')
    require(readiness is not None, 'source_changed',
            'attached Execution Agent needs its current Controller readiness')
    state = current_readiness(readiness)
    parent = state.get('handoff') or {}
    require(state.get('stage') == 3 and parent.get('role') == 'implementation-dispatcher' and
            parent.get('requirement') == saved['requirement'] and
            parent.get('binding') == saved['binding'],
            'source_changed', 'Execution Agent source differs from original Controller handoff')
    return parent['expected_entry']


def inherited_planning_paths(predecessor):
    if predecessor is None:
        return None
    accepted = predecessor_facts(predecessor)
    original = accepted.get("handoff") or {}
    if original.get("kind") == "implementation-launch-facts":
        return original["planning_paths"]
    if original.get("stage") == 2:
        return accepted["payload"]["planning_paths"]
    if original.get("stage") == 3 and original.get("predecessor") is not None:
        return inherited_planning_paths(original["predecessor"])
    return None


def planning_paths_for(request):
    planning = inherited_planning_paths(request.get("predecessor"))
    if planning is not None:
        return planning
    if request.get("role") == "execution-agent" and request.get("readiness") is not None:
        state = current_readiness(request["readiness"])
        require(state.get("stage") == 3 and state.get("handoff", {}).get("role") == "implementation-dispatcher",
                "scope_changed", "allocation needs the current Dispatcher planning authority")
        return inherited_planning_paths(state["handoff"].get("predecessor"))
    return None


def accepted_design_state(state, accepted):
    if state.get('design_predecessor') is not None:
        import workflow_progress as progress
        prior = progress.read_design_predecessor(state['design_predecessor'], state['checkpoint_path'])
        require(prior['accepted']['digest'] == predecessor_digest(accepted),
                'handoff_not_ready', 'predecessor differs from the original accepted design')
        return prior
    require(state.get('stage') == 2 and
            (state.get('accepted') or {}).get('digest') == predecessor_digest(accepted),
            'handoff_not_ready', 'one original accepted design must own the handoff')
    return state


def inherited_manifest_baseline(request):
    if request.get("role") == "execution-agent" and "manifest_baseline" in request:
        return commit(request["manifest_baseline"])
    readiness = request.get("readiness")
    if readiness is not None:
        return readiness["scope_authority"]["scope"]["baseline"]
    predecessor = request.get("predecessor")
    if predecessor is not None:
        prior = predecessor_facts(predecessor)
        previous = prior.get("handoff") or {}
        if previous.get("kind") == "implementation-launch-facts":
            return previous["manifest_baseline"]
        if previous.get("stage") == 3:
            readiness = previous.get("readiness")
            if readiness is not None:
                return readiness["scope_authority"]["scope"]["baseline"]
    return None


def inherited_planning_candidate(request):
    readiness = request.get("readiness")
    if readiness is not None:
        return readiness["scope_authority"]["candidate_commit"]
    predecessor = request.get("predecessor")
    if predecessor is not None:
        prior = predecessor_facts(predecessor)
        previous = prior.get("handoff") or {}
        if previous.get("stage") == 3 and previous.get("readiness") is not None:
            return previous["readiness"]["scope_authority"]["candidate_commit"]
    return None


def authority_generated_outputs(authority):
    if authority.get('kind') == 'inline':
        outputs = authority.get('generated_outputs', {})
        require(isinstance(outputs, dict), "invalid_scope", "inline generated map must be complete")
        return outputs
    proposal = authority.get("proposal")
    require(isinstance(proposal, dict), "invalid_scope", "sealed scope proposal required")
    outputs = (authority.get("generated_outputs") if authority.get("revision", 1) > 1
               else proposal.get("generated_outputs", {}))
    require(isinstance(outputs, dict), "invalid_scope", "generated output inventory must be an object")
    return outputs


def inherited_generated_outputs(request):
    readiness = request.get("readiness")
    if readiness is not None:
        return authority_generated_outputs(readiness["scope_authority"])
    predecessor = request.get("predecessor")
    if predecessor is not None:
        prior = predecessor_facts(predecessor)
        previous = prior.get("handoff") or {}
        if previous.get("stage") == 3:
            if previous.get("readiness") is not None:
                return authority_generated_outputs(previous["readiness"]["scope_authority"])
            return previous.get("generated_outputs", {})
    return request.get("generated_outputs", {})


def prepare(request, *, checkpoint_path=None, allocation_store=None):
    return _prepare(request, checkpoint_path=checkpoint_path, allocation_store=allocation_store)[0]


def _prepare(request, *, checkpoint_path=None, allocation_store=None):
    """Return the sealed input and this preparation's verified entry to C."""
    request = copy.deepcopy(request)
    require(request.get("protocol") == PROTOCOL, "legacy_run_requires_original_runtime", "retain original stage-transfer runtime: " + str(request.get("protocol")))
    entry.fields(request, FIELDS | {"operation"}, OPTIONAL_FIELDS)
    stage, role = request["stage"], request["role"]
    require(type(stage) is int and stage in ROLES and role in {ROLES[stage], "execution-agent" if stage == 3 else ROLES[stage]},
            "role_mismatch", "stage and role differ")
    if checkpoint_path is not None and role == 'execution-agent':
        require(request.get('readiness') is None and
                'integration_recovery' in request['authorization'],
                'invalid_checkpoint', 'Execution Agent checkpoint pin belongs to inline final recovery')
    current = refresh(request["entry"], request["expected_entry"], _binding_to_verify=request["binding"])
    require(current["entry"]["stage"] == stage, "role_mismatch", "entry must be prepared for the target stage")
    verify_discussion_binding(current, request["binding"])
    scope = request["scope"]
    root = current["repository"]["root"]
    stage3_progress = None
    if stage == 4:
        require(request.get("predecessor") is not None, "predecessor_incomplete",
                "inherited closure requires an accepted implementation result")
        stage3_progress = accepted_stage3_progress(request["predecessor"], checkpoint_path)
    current_authority = (stage3_progress["execution_authority"]
                         if stage3_progress is not None else None)
    require("manifest_baseline" not in request or stage == 3 and role == "execution-agent",
            "invalid_scope", "separate manifest baseline belongs to an Execution Agent allocation")
    require("generated_outputs" not in request or
            stage == 3 and request["predecessor"] is None and request.get("readiness") is None,
            "invalid_scope", "explicit generated output inventory belongs to standalone Stage 3 only")
    if stage >= 2:
        source_identity = (request["requirement"].get("requirement_identity")
                           if isinstance(request["requirement"], dict) else None)
        require(isinstance(source_identity, dict) and isinstance(source_identity.get("path"), str),
                "invalid_source", "complete frozen requirement identity required")
        # An Execution Agent can read a completed implementation file from an
        # earlier group. The manifest remains implementation-owned; this one
        # allocation protects that path only as a read input.
        implementation_reads = []
        if stage == 3 and role == 'execution-agent':
            implementation_reads = sorted(set(scope['protected_paths']) &
                                          set(scope['implementation_paths'] or []))
        validation_scope = ({**scope, 'protected_paths': [path for path in scope['protected_paths']
                             if path not in implementation_reads]} if implementation_reads else scope)
        scope = stage_scope.validate_scope(validation_scope, root, stage=stage,
            requirement_path=source_identity["path"],
            planning_paths=planning_paths_for(request),
            manifest_baseline=inherited_manifest_baseline(request),
            candidate_commit=(current_authority["candidate_commit"] if current_authority is not None
                              else inherited_planning_candidate(request)),
            generated_outputs=(authority_generated_outputs(current_authority) if current_authority is not None
                               else inherited_generated_outputs(request)),
            _repository_facts=current["repository"])
        if implementation_reads:
            scope['protected_paths'] = request['scope']['protected_paths'][:]
        require((stage != 2 or scope["downstream"]["state"] == "unresolved") and
                (stage == 2 or scope["downstream"]["state"] == "sealed"),
                "scope_incomplete", "Stage 2 starts unresolved; downstream stages require a sealed manifest")
        if stage == 3:
            require(scope["downstream"]["implementation_required"] is True and
                    bool(scope["implementation_paths"]), "scope_incomplete",
                    "Stage 3 needs nonempty sealed implementation paths; use scope-propose / scope-revise")
    else:
        entry.fields(scope, {"baseline", "owned_paths", "protected_paths", "implementation_paths", "closure_paths"})
        for field in ("owned_paths", "protected_paths", "implementation_paths", "closure_paths"):
            paths(scope[field], root)
    active_writes = scope["owned_paths"] + ([] if stage == 3 and role == 'execution-agent'
                                           else scope["implementation_paths"] or [])
    if stage != 3:
        active_writes += scope["closure_paths"] or []
    require(not set(scope["protected_paths"]) & set(active_writes),
            "invalid_scope", "write scope overlaps protected paths")
    require(not set(scope["implementation_paths"] or []) & set(scope["closure_paths"] or []), "invalid_scope", "implementation and closure ownership overlap")
    parent = None
    if stage == 3 and role == "execution-agent" and request.get("readiness") is not None:
        parent = current_readiness(request["readiness"])
        current_authority = request["readiness"]["scope_authority"]
        require(parent.get("stage") == 3 and parent.get("handoff", {}).get("role") == "implementation-dispatcher" and
                parent["handoff"]["binding"] == request["binding"] and
                parent["handoff"]["requirement"] == request["requirement"] and
                current_authority["scope"]["downstream"] == scope["downstream"],
                "scope_changed", "allocation must inherit the current sealed Dispatcher scope")
    identity, source_commit = source(current, request["requirement"], stage,
        attached_controller_entry=parent['handoff']['expected_entry'] if parent is not None else None)
    if stage >= 2:
        require(identity["path"] not in (scope["closure_paths"] or []), "invalid_scope", "frozen requirement is never closure-owned")
        if stage == 2:
            require(all(Path(p).suffix.lower() in {".md", ".mdx", ".rst", ".adoc", ".asciidoc", ".org", ".txt"}
                        or Path(p).name.lower() in {"readme", "changelog", "authors", "maintainers"} for p in scope["owned_paths"]),
                    "invalid_scope", "designer owns planning documents only")
        require(identity["path"] in scope["protected_paths"], "invalid_scope", "frozen requirement must be protected")
        commit(scope["baseline"])
        require(stage == 4 or current["repository"]["head"] == scope["baseline"], "baseline_changed", "scope baseline must be current HEAD")
        require(request["binding"] is not None and source_commit is not None, "binding_missing", "Git stages need an actual Flow Worktree and frozen Git source")
        binding_report = control_git.verify_binding(Path(root), request["binding"])
        require(binding_report["current_commit"] == current["repository"]["head"],
                "entry_changed", "Flow Worktree HEAD changed during preparation")
        require(request["binding"]["repository"] == request["target"]["repository"] and
                request["binding"]["target_branch"] == request["target"]["branch"], "target_mismatch", "Flow Worktree and delivery target differ")
        delivered = delivery(request, current, identity, source_commit, _binding_report=binding_report)
        if stage == 2:
            protected_history = supervision.planning_protected_changes(
                request["binding"], delivered["target_head"], scope["protected_paths"])
            require(not protected_history, "source_history_conflict",
                    "Flow Worktree history touches protected planning sources: " + ", ".join(protected_history))
    else:
        require(request["binding"] is None and request["delivery"] is None, "invalid_request", "dedicated discussion preparation has no Flow Worktree delivery")
        delivered = None
    authority = request["authorization"]
    entry.fields(authority, {"reference", "flow_mode", "scope_digest"}, {"phase", "control_plan_id", "integration_recovery"})
    if 'integration_recovery' in authority:
        require(role == 'execution-agent', 'authority_missing',
                'only final recovery allocations inherit closure authority')
        if request.get('readiness') is not None:
            require(current_readiness(request['readiness'])['handoff']['authorization'].get('integration_recovery') ==
                    authority['integration_recovery'], 'authority_missing',
                    'only final recovery allocations inherit closure authority')
        else:
            verify_inline_recovery_pin(request, current['actor']['controller_ref'], identity,
                                       checkpoint_path)
    if "control_plan_id" in authority:
        require(stage < 2, "invalid_request", "control_plan_id selects a dedicated plan only")
        entry.nonempty(authority["control_plan_id"])
    entry.nonempty(authority["reference"])
    require(authority["flow_mode"] in {"stepwise", "continuous"} and authority["scope_digest"] == entry.digest(scope),
            "authorization_changed", "authorization must bind the exact scope and flow mode")
    semantic = request["semantic"]
    entry.fields(semantic, {"objective", "testing_basis", "completion_criteria", "constraints"},
                 {"validation_plan", "implementation_policy", "adopt_paths", "adoption_snapshot_digest",
                  "execution_plan", "inline_plan", "plan_refs", "authority_revision", "authority_digest", "plan_digest", "repair"})
    if stage == 3 and request["role"] == "implementation-dispatcher":
        control.validate_validation_plan(semantic.get("validation_plan"), request["binding"])
    if 'adopt_paths' in semantic or 'adoption_snapshot_digest' in semantic:
        require(role == 'execution-agent', 'invalid_policy', 'adoption belongs only to Execution Agents')
        control.paths(semantic.get('adopt_paths'), empty=True)
        if semantic['adopt_paths']:
            entry.nonempty(semantic.get('adoption_snapshot_digest'))
        else:
            require('adoption_snapshot_digest' not in semantic, 'invalid_policy', 'empty adoption cannot claim a snapshot')
    if 'implementation_policy' in semantic:
        require(isinstance(semantic['implementation_policy'], dict), 'invalid_policy', 'explicit policy must be an object')
        require(stage == 3 and role == 'implementation-dispatcher', 'invalid_policy', 'policy only belongs to the Stage-3 Dispatcher')
        require(semantic['implementation_policy'].get('mode') == 'full', 'invalid_policy',
                'new Stage-3 runs require full Execution Agent implementation')
        control.implementation_policy(semantic['implementation_policy'], controller_ref=current['actor']['controller_ref'],
            requirement_identity=identity, scope_digest=authority['scope_digest'], baseline=scope['baseline'])
    if stage == 3 and request.get('readiness') is None:
        if role == 'implementation-dispatcher' and 'inline_plan' in semantic:
            original_input = inline_input_digest(request, current['actor']['controller_ref'],
                identity, source_commit, delivered,
                control.select_configuration(request['configuration']))
            derived = execution_plan.inline_authority(semantic['inline_plan'], scope, semantic, identity,
                request.get('generated_outputs', {}), repository=request['binding']['worktree'],
                input_digest=original_input)
            require(request.get('inline_authority', derived) == derived,
                    'execution_plan_stale', 'standalone inline plan authority changed')
            request['inline_authority'] = derived
        elif role == 'execution-agent' and 'inline_plan' in semantic:
            inline = request.get('inline_authority')
            require(isinstance(inline, dict), 'execution_plan_stale',
                    'execution allocation needs its original inline plan authority')
            derived = execution_plan.inline_authority(semantic['inline_plan'], inline['scope'], semantic, identity,
                inline.get('generated_outputs', {}), repository=request['binding']['worktree'],
                original_scope_revision=inline['execution_plan']['original_scope_revision'],
                input_digest=inline['execution_plan']['sources'][0]['input_digest'])
            require(inline == derived and scope['downstream'] == inline['scope']['downstream'] and
                    scope['implementation_paths'] == inline['scope']['implementation_paths'],
                    'execution_plan_stale', 'standalone plan or scope changed')
            expected = {'authority_revision': inline['revision'], 'authority_digest': entry.digest(inline),
                        'plan_digest': inline['execution_plan']['plan_digest']}
            require(all(semantic.get(key) == value for key, value in expected.items()),
                    'execution_plan_stale', 'standalone allocation names an older inline plan')
        else:
            require(False, 'execution_plan_incomplete',
                    'standalone Stage 3 requires an inline function plan')
    if stage == 3:
        authority_record = (request['readiness']['scope_authority'] if request.get('readiness') is not None
                            else request['inline_authority'])
        catalog = execution_plan.current_catalog(authority_record)
        plan_reference = {'authority_revision': authority_record['revision'],
                          'authority_digest': entry.digest(authority_record),
                          'plan_digest': catalog['plan_digest'],
                          'contract_digest': catalog['contract_digest']}
        require(semantic.get('execution_plan', plan_reference) == plan_reference,
                'execution_plan_stale', 'Stage-3 input must reference the current sealed plan')
        semantic['execution_plan'] = plan_reference
    if stage == 3 and role == 'execution-agent' and request.get('readiness') is not None:
        authority_record = request['readiness']['scope_authority']
        expected = {'authority_revision': authority_record['revision'],
                    'authority_digest': entry.digest(authority_record),
                    'plan_digest': authority_record['execution_plan']['plan_digest']}
        require(all(semantic.get(key) == value for key, value in expected.items()),
                'execution_plan_stale', 'allocation names an older plan authority')
        execution_plan.validate_plan_refs(execution_plan.current_catalog(authority_record), semantic.get('plan_refs'),
            scope['owned_paths'], scope['protected_paths'],
            parent['plan_results'],
            authority_record)
    entry.nonempty(semantic["objective"]); entry.nonempty(semantic["testing_basis"])
    strings(semantic["completion_criteria"]); strings(semantic["constraints"])
    phase_evidence({**request, "controller_ref": current["actor"]["controller_ref"],
                    "requirement_identity": identity})
    stage3_role = stage == 3 and role in {'implementation-dispatcher', 'execution-agent'}
    configuration = control.select_configuration(request["configuration"])
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
    require(request["configuration"]["role"] == role and not configuration["needs_decision"],
            "configuration_changed", "role configuration requires a controller decision")
    predecessor = request["predecessor"]
    if predecessor is not None:
        prior = predecessor_facts(predecessor)
        require_current_protocol(prior)
        require(prior.get("status") == "accepted", "predecessor_incomplete", "an accepted B result is required")
        old = prior["handoff"]
        if old["stage"] == 2:
            require(prior.get("planning_complete") is True, "predecessor_incomplete", "accepted planning result required")
            readiness = request.get("readiness")
            state = current_readiness(readiness)
            source_state = accepted_design_state(state, predecessor)
            require(source_state.get("accepted", {}).get("digest") == predecessor_digest(predecessor) and
                    source_state.get("handoff", {}).get("digest") == old["digest"] and
                    readiness["accepted_digest"] == predecessor_digest(predecessor) and
                    source_state.get("scope_authority") == readiness["scope_authority"],
                    "handoff_not_ready", "readiness must bind the original accepted design and current scope")
            effective = effective_scope_facts(old, readiness["scope_authority"])
            require(effective["downstream"]["implementation_required"] is True and
                    bool(effective["implementation_paths"]),
                    "scope_incomplete", "planning-only or empty implementation scope has no Stage-3 handoff")
        else:
            require(prior.get("downstream_ready") is True,
                    "predecessor_incomplete", "accepted predecessor is not ready")
        require(old["expected_entry"]["discussion_project"] == current["discussion_project"],
                "discussion_identity_conflict", "successor must retain the original discussion project")
        require((old["stage"], stage) in {(0, 1), (0, 2), (1, 2), (2, 3), (3, 4)}, "invalid_stage", "illegal stage transition")
        require(old["controller_ref"] == current["actor"]["controller_ref"] and prior["requirement_identity"] == identity,
                "source_changed", "predecessor controller or requirement differs")
        if old["stage"] >= 2:
            require(old["binding"] == request["binding"], "binding_changed", "successor must retain the exact Flow Worktree")
            prior_progress = stage3_progress if old["stage"] == 3 else None
            verify_result(old["stage"], prior["payload"], old["binding"], old["scope"], root,
                          prior["role_ref"], old['authorization'].get('integration_recovery'),
                          prior_progress)
            if old["stage"] == 2:
                published_target = scope['baseline']
                for relative in prior["payload"]["planning_paths"]:
                    require(blob(root, prior["payload"]["planning_merge_commit"], relative) ==
                            blob(root, published_target, relative) and
                            mode(root, prior["payload"]["planning_merge_commit"], relative) ==
                            mode(root, published_target, relative),
                            "source_changed", "sealed planning bytes changed at the implementation baseline")
            accepted_scope = (prior_progress["execution_authority"]["scope"]
                              if prior_progress is not None else old["scope"])
            protected = set(old["scope"]["protected_paths"])
            if old["stage"] == 2:
                protected.update(prior["payload"]["planning_paths"])
                require(scope["downstream"] == effective["downstream"] and
                        scope["implementation_paths"] == effective["implementation_paths"] and
                        scope["closure_paths"] == effective["closure_paths"],
                        "scope_changed", "Stage 3 must use the current sealed scope without widening or narrowing it")
            if stage == 4:
                protected -= set(accepted_scope["closure_paths"])
            require(protected <= set(scope["protected_paths"]), "scope_changed", "successor must protect inherited planning and source paths")
            if old["stage"] == 3:
                require(scope["owned_paths"] == accepted_scope["closure_paths"] and
                        scope["downstream"] == accepted_scope["downstream"] and
                        scope["implementation_paths"] == accepted_scope["implementation_paths"] and
                        scope["closure_paths"] == accepted_scope["closure_paths"],
                        "scope_changed", "closure must retain the Stage-3 accepted scope revision")
        if stage == 4:
            require(prior["payload"]["candidate_commit"] == current["repository"]["head"] and scope["baseline"] == old["scope"]["baseline"] and
                    (prior_progress["execution_authority"]["scope"]["closure_paths"]
                     if prior_progress is not None else old["scope"]["closure_paths"]) == scope["closure_paths"],
                    "scope_changed", "closure must retain accepted candidate and documentation scope")
    require(stage != 4 or predecessor is not None, "predecessor_incomplete", "inherited closure requires an accepted implementation result")
    if allocation_store is not None:
        require(stage == 3 and role == 'execution-agent',
                'invalid_request', 'shared allocation requires an Execution Agent')
        store, current_root, allocation_id = allocation_store
        entry.nonempty(allocation_id)
        tickets = {ref['ticket_id'] for ref in semantic['plan_refs']}
        require(len(tickets) == 1, 'invalid_allocation', 'one allocation belongs to one Ticket')
        contract_keys = {'requirement', 'predecessor', 'target', 'delivery', 'readiness',
                         'inline_authority', 'generated_outputs'}
        contract = {key: request[key] for key in contract_keys if key in request}
        contract['scope'] = {key: value for key, value in scope.items()
                             if key not in {'baseline', 'owned_paths', 'protected_paths'}}
        package = {key: request[key] for key in ('entry', 'expected_entry')}
        baseline = {'binding': request['binding'], 'source_commit': source_commit,
                    'delivery_facts': delivered}
        if 'manifest_baseline' in request:
            baseline['manifest_baseline'] = request['manifest_baseline']
        refs = {name: store.write_allocation_part(current_root, name, value)
                for name, value in (('contract', contract), ('package', package), ('baseline', baseline))}
        return seal({'protocol': PROTOCOL, 'kind': 'allocation-input', 'stage': 3,
            'role': role, 'checkpoint': str(store.checkpoint.resolve()), 'run_id': current_root.run_id,
            'refs': refs, 'allocation_id': allocation_id, 'subject_id': 'ticket:' + next(iter(tickets)) + ':0',
            'controller_ref': current['actor']['controller_ref'],
            'requirement_identity': identity, 'selection': configuration,
            'scope': {key: scope[key] for key in ('baseline', 'owned_paths', 'protected_paths')},
            'authorization': request['authorization'], 'configuration': request['configuration'],
            'semantic': {key: value for key, value in semantic.items() if key != 'inline_plan'}}), current
    saved = seal({**{k: copy.deepcopy(request[k]) for k in FIELDS},
                 **({"readiness": copy.deepcopy(request["readiness"])} if "readiness" in request else {}),
                 **({"generated_outputs": copy.deepcopy(request["generated_outputs"])}
                    if "generated_outputs" in request else {}),
                 **({"manifest_baseline": request["manifest_baseline"]}
                    if "manifest_baseline" in request else {}),
                 **({"inline_authority": copy.deepcopy(request["inline_authority"])}
                    if "inline_authority" in request else {}),
                 "kind": "stage-input",
                 "controller_ref": current["actor"]["controller_ref"], "requirement_identity": identity,
                 "source_commit": source_commit, "delivery_facts": delivered, "selection": configuration})
    return saved, current


def require_current_protocol(value):
    original = value.get('record') or value.get('handoff') or value
    saved = original.get('handoff', original)
    pins = {name:pin.get('bundle_digest') for name,pin in saved.get('expected_entry',{}).get('packages',{}).items()}
    require(value.get('protocol') == PROTOCOL, 'legacy_run_requires_original_runtime',
            'retain original stage-transfer runtime ' + str(value.get('protocol')) + '; package digests ' + str(pins))


def verify(saved, *, checkpoint_path=None):
    if saved.get('kind') == 'allocation-input':
        body = resolve_allocation(saved)
        import workflow_state_store
        store = workflow_state_store.open_store(saved['checkpoint'])
        current = store.open_current()
        actual = _prepare({**{key: body[key] for key in FIELDS},
            **{key: body[key] for key in OPTIONAL_FIELDS if key in body}, 'operation': 'verify'},
            checkpoint_path=(saved['checkpoint'] if 'integration_recovery' in body['authorization'] and
                             body.get('readiness') is None else None),
            allocation_store=(store, current, saved['allocation_id']))[0]
        require(actual == saved, 'handoff_changed', 'allocation facts changed; retain its shared contract')
        return saved
    body = unseal(saved)
    require_current_protocol(body)
    entry.fields(body, FIELDS | {"kind", "controller_ref", "requirement_identity", "source_commit", "delivery_facts", "selection"}, OPTIONAL_FIELDS)
    actual = _prepare({**{k: body[k] for k in FIELDS},
                      **({"readiness": body["readiness"]} if "readiness" in body else {}),
                      **({"generated_outputs": body["generated_outputs"]}
                         if "generated_outputs" in body else {}),
                      **({"manifest_baseline": body["manifest_baseline"]}
                         if "manifest_baseline" in body else {}),
                      **({"inline_authority": body["inline_authority"]}
                         if "inline_authority" in body else {}),
                      "operation": "verify"}, checkpoint_path=checkpoint_path)[0]
    require(actual == saved, "handoff_changed", "stage input facts changed; retain the original handoff")
    return actual


def resolve_allocation(saved, *, store=None, current=None):
    """Resolve one named allocation contract; never read a roster or history."""
    if saved.get('kind') != 'allocation-input':
        return saved
    body = unseal(saved)
    require_current_protocol(body)
    entry.fields(body, {'protocol', 'kind', 'stage', 'role', 'checkpoint', 'run_id', 'refs',
        'controller_ref', 'requirement_identity', 'selection', 'scope', 'authorization',
        'configuration', 'semantic', 'allocation_id', 'subject_id'})
    require(body['stage'] == 3 and body['role'] == 'execution-agent' and
            set(body['refs']) == {'contract', 'package', 'baseline'},
            'invalid_evidence', 'shared allocation identity is invalid')
    import workflow_state_store
    store = store or workflow_state_store.open_store(body['checkpoint'])
    require(str(store.checkpoint.resolve()) == body['checkpoint'],
            'identity_mismatch', 'allocation checkpoint changed')
    current = current or store.open_current()
    require(current is not None and current.run_id == body['run_id'],
            'identity_mismatch', 'allocation belongs to another current run')
    parts = {name: store.read_allocation_part(current, name, reference)
             for name, reference in body['refs'].items()}
    contract = parts['contract']
    entry.fields(contract, {'requirement', 'predecessor', 'target', 'delivery', 'scope'},
                 {'readiness', 'inline_authority', 'generated_outputs'})
    entry.fields(parts['package'], {'entry', 'expected_entry'})
    entry.fields(parts['baseline'], {'binding', 'source_commit', 'delivery_facts'}, {'manifest_baseline'})
    entry.fields(body['scope'], {'baseline', 'owned_paths', 'protected_paths'})
    semantic = copy.deepcopy(body['semantic'])
    if 'inline_authority' in contract:
        semantic['inline_plan'] = copy.deepcopy(execution_plan.current_catalog(
            contract['inline_authority'])['tickets'][0]['plan'])
    return seal({**contract, **parts['package'], **parts['baseline'],
        **{key: body[key] for key in ('protocol', 'stage', 'role', 'controller_ref',
            'requirement_identity', 'selection', 'authorization', 'configuration')},
        'kind': 'stage-input', 'semantic': semantic,
        'scope': {**contract['scope'], **body['scope']}})


def _render_verified(saved, readiness, current_progress=None):
    """Project a C-verified acceptance; caller owns the current readback."""
    body = unseal(saved)
    require_current_protocol(body)
    if readiness is not None:
        require(readiness.get("accepted_digest") == saved["digest"], "handoff_not_ready",
                "readiness belongs to another accepted result")
    require(body.get("status") == "accepted", "invalid_evidence", "accepted stage result required")
    original = body["handoff"]
    unseal(original)
    return _render_accepted_facts(saved, readiness, current_progress)


def _render_accepted_facts(saved, readiness, current_progress=None):
    body = saved
    original = body["handoff"]
    stage = original["stage"]
    require(original["role"] != "execution-agent", "result_incomplete", "allocation acceptance is not stage completion")
    if stage == 2:
        require(body.get("planning_complete") is True, "result_incomplete", "accepted planning result required")
        effective, ready = (readiness["scope_authority"]["scope"] if readiness is not None else None,
                            readiness is not None)
    else:
        require(body["downstream_ready"] is True, "result_incomplete", "accepted stage is not complete")
        effective, ready = None, True
    payload = copy.deepcopy(body["payload"])
    accepted_transfer = (seal({'kind': 'design-acceptance-ref', 'protocol': PROTOCOL,
        'stage': 2, 'run_id': readiness['run_id'],
        'checkpoint_path': readiness['checkpoint_path'],
        'accepted_digest': saved['digest']}) if stage == 2 and readiness is not None else saved)
    projection = {"controller_ref": original["controller_ref"], "role_ref": body["role_ref"],
                  "binding": original["binding"], "requirement_identity": body["requirement_identity"],
                  "source_commit": original["source_commit"], "accepted_transfer": accepted_transfer,
                  "control_checkpoint": {"controller_ref": original["controller_ref"], "stage": stage,
                      "role_ref": body["role_ref"], "state": "completed", "role_kind": original["role"]}}
    projection.update({k: v for k, v in payload.items() if k not in {"artifacts", "checks"}})
    if stage == 2:
        projection.update(planning_complete=True, handoff_ready=ready,
                          scope_revision=effective["downstream"]["revision"] if ready else None,
                          allowed_paths=effective["implementation_paths"] if ready else None,
                          closure_paths=effective["closure_paths"] if ready else None,
                          protected_paths=sorted(set(original["scope"]["protected_paths"] +
                                                     payload["planning_paths"])))
    elif stage == 3:
        scope = (current_progress["execution_authority"]["scope"]
                 if current_progress is not None else original["scope"])
        projection.update({k: scope[k] for k in ("implementation_paths", "closure_paths")})
        projection["protected_paths"] = sorted(set(original["scope"]["protected_paths"]) - set(scope["closure_paths"]))
    elif stage == 4:
        projection["ancestor_verified"] = True
    rendered = json.dumps(projection, ensure_ascii=False, sort_keys=True, indent=2)
    result = {"payload": projection, "text": rendered, "downstream_ready": ready}
    if stage >= 2:
        result["stage_result"] = {"result": "completed", "artifacts": payload["artifacts"], "evidence": payload["checks"],
                                  "handoff_json": rendered, "question": "", "message": "", "needs_input_kind": "none"}
    return result


def render_implementation_delivery(checkpoint_path):
    import workflow_progress as progress
    reference = progress.implementation_predecessor(checkpoint_path)
    accepted, checkpoint = progress.implementation_acceptance(reference)
    # Build the footer from finite facts, not from reports and then strip them.
    facts, current, _ = progress.read_implementation_delivery(checkpoint_path)
    footer_acceptance = {**accepted, 'payload': {**facts['payload'],
        'review_ref': facts['review_ref'], 'verification_ref': facts['verification_ref']}}
    rendered = _render_accepted_facts(footer_acceptance, None, checkpoint)
    rendered['payload']['accepted_transfer'] = seal({'kind': 'implementation-acceptance-ref',
        'protocol': PROTOCOL, 'stage': 3, 'run_id': current.run_id,
        'checkpoint_path': str(checkpoint_path), 'accepted_digest': accepted['digest']})
    authority = checkpoint['execution_authority']
    rendered['payload'].update(current_scope_authority=authority, execution_plan=authority['execution_plan']['catalog'],
        generated_outputs=authority_generated_outputs(authority))
    rendered['text'] = json.dumps(rendered['payload'], ensure_ascii=False, sort_keys=True)
    rendered['stage_result']['handoff_json'] = rendered['text']
    return rendered


def model_input(payload, *, expanded=False, read_totals=None):
    """Measure the exact model text separately from authority verification reads."""
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    size = len(text.encode('utf-8'))
    if expanded:
        segments = payload.get('segments')
        require(isinstance(segments, list) and segments and all(isinstance(segment, str) and
                len(segment.encode('utf-8')) <= 64 * 1024 for segment in segments),
                'model_input_capacity', 'expanded original sources require bounded UTF-8 segments')
    require(size <= (256 if expanded else 64) * 1024,
            'model_input_capacity', 'model input exceeds its fixed total UTF-8 budget')
    reading = {'model_bytes': size, 'model_limit': (256 if expanded else 64) * 1024,
        'structured_bytes': read_totals['bytes'] if read_totals is not None else 'unknown',
        'structured_files': read_totals['files'] if read_totals is not None else 'unknown',
        'coverage': 'state-store-only' if read_totals is not None else 'unknown',
        'external_read_bytes': 'unknown', 'external_read_files': 'unknown'}
    return {'payload': payload, 'text': text, 'reading': reading, 'downstream_ready': False}


def render(saved, *, readiness=None, checkpoint_path=None):
    if saved.get('kind') == 'allocation-input':
        import workflow_state_store
        with workflow_state_store.operation_store(saved['checkpoint']):
            payload = render(resolve_allocation(saved), readiness=readiness, checkpoint_path=checkpoint_path)
            payload['payload']['input_digest'] = saved['digest']
            return model_input(payload['payload'],
                read_totals=workflow_state_store.open_store(saved['checkpoint']).read_totals)
    body = unseal(saved)
    require_current_protocol(body)
    if body.get("status") == "accepted":
        require('allocation_digest' not in body, 'result_incomplete',
                'allocation acceptance is not stage completion')
        original = body["handoff"]
        if original["stage"] == 2 and readiness is not None:
            state = current_readiness(readiness)
            source_state = accepted_design_state(state, saved)
            require(source_state.get("accepted", {}).get("digest") == saved["digest"], "handoff_not_ready",
                    "readiness belongs to another planning result")
            effective_scope(original, readiness["scope_authority"])
        current_progress = (accepted_stage3_progress(saved, checkpoint_path)
                            if original["stage"] == 3 and checkpoint_path is not None else None)
        if original['stage'] == 3 and checkpoint_path is not None:
            return render_implementation_delivery(checkpoint_path)
        return _render_verified(saved, readiness, current_progress)

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
    if "readiness" in body:
        payload["readiness"] = copy.deepcopy(body["readiness"])
        if body['stage'] == 3:
            authority = body['readiness']['scope_authority']
            payload['current_scope_authority'] = copy.deepcopy(authority)
            payload['execution_plan'] = copy.deepcopy(authority['execution_plan'])
            payload['generated_outputs'] = copy.deepcopy(authority_generated_outputs(authority))
    elif 'inline_authority' in body:
        payload['current_scope_authority'] = copy.deepcopy(body['inline_authority'])
        payload['execution_plan'] = copy.deepcopy(body['inline_authority']['execution_plan'])
        payload['generated_outputs'] = copy.deepcopy(authority_generated_outputs(body['inline_authority']))
    if "generated_outputs" in body:
        payload["generated_outputs"] = copy.deepcopy(body["generated_outputs"])
    if "manifest_baseline" in body:
        payload["manifest_baseline"] = body["manifest_baseline"]
    if body['stage'] == 3:
        authority = (body['readiness']['scope_authority'] if body.get('readiness') is not None
                     else body['inline_authority'])
        payload['current_scope_authority'] = body['semantic']['execution_plan']
        payload['execution_plan'] = execution_plan.current_catalog(authority)
        payload['semantic'] = {key: value for key, value in payload['semantic'].items() if key != 'inline_plan'}
        if 'readiness' in payload:
            payload['readiness'] = {key: value for key, value in payload['readiness'].items()
                                    if key not in {'scope_authority', 'execution_plan'}}
    if body['role'] == 'execution-agent':
        payload['write_authority'] = 'await-bound-release'
        authority = (body['readiness']['scope_authority'] if body.get('readiness') is not None
                     else body['inline_authority'])
        catalog = execution_plan.current_catalog(authority)
        selected = {ref['step_id'] for ref in body['semantic']['plan_refs']}
        payload['execution_plan'] = {'identity': body['semantic']['execution_plan'], 'tickets': []}
        for ticket in catalog['tickets']:
            plan = ticket['plan']
            steps = [step for step in plan['steps'] + plan['artifact_steps'] if step['id'] in selected]
            if not steps:
                continue
            verifications = {identity for step in steps for identity in step['verification_refs']}
            payload['execution_plan']['tickets'].append({'ticket_id': plan['ticket_id'], 'source': ticket['source'],
                'contract_refs': plan['contract_refs'], 'steps': steps,
                'behavior_slices': [group for group in plan['behavior_slices'] if set(group['step_ids']) & selected],
                'verifications': [item for item in plan['verifications'] if item['id'] in verifications]})
        payload['current_scope_authority'] = body['semantic']['execution_plan']
        payload['dispatch_preflight'] = execution_plan.dispatch_preflight(catalog, selected)
        if body['semantic'].get('repair') is not None:
            payload['execution_invocation'] = {'operation': 'continue',
                'ref': body['semantic']['repair']['agent_ref'],
                'invocation_id': body['semantic']['repair']['invocation_id']}
    return model_input(payload)


def handle(request):
    require(request.get("protocol") == PROTOCOL, "legacy_run_requires_original_runtime", "retain original stage-transfer runtime: " + str(request.get("protocol")))
    if request.get("operation") == "prepare":
        return prepare(request)
    entry.fields(request, {"protocol", "operation", "handoff"}, {"readiness"})
    require(request["operation"] in {"verify", "render"}, "invalid_operation", "unknown handoff operation")
    return verify(request["handoff"]) if request["operation"] == "verify" else render(request["handoff"], readiness=request.get("readiness"))


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
