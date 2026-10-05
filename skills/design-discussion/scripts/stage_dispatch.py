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
import execution_plan
import workflow_control as control
import workflow_control_git as control_git

PROTOCOL = handoff.PROTOCOL
require = handoff.require


def verify_discussion_port(envelope, saved):
    entry.fields(envelope, {"protocol_version", "project_path", "project_id", "tree_id", "actor_topic_id",
                           "actor_conversation_ref", "expected_ledger_revision", "expected_topic_revision", "idempotency_key"})
    controller_entry = handoff.attached_source_entry(saved) or saved['expected_entry']
    require(envelope["project_path"] == entry.discussion_root(controller_entry) and
            all(envelope[key] == controller_entry['requirement']['attachment'][key]
                for key in ("project_id", "tree_id", "actor_topic_id")),
            "discussion_identity_conflict", "control envelope differs from pinned discussion project")


def protected_index_matches_head(repository, relative):
    tree = control_git.git(repository, 'ls-tree', '-z', 'HEAD', '--', relative)
    index = control_git.git(repository, 'ls-files', '--stage', '-v', '-z', '--', relative)
    if not tree:
        return not index
    header, separator, name = tree.partition(b'\t')
    fields = header.split(b' ')
    return (separator and name == relative.encode() + b'\0' and len(fields) == 3 and
            index == b'H ' + fields[0] + b' ' + fields[2] + b' 0\t' + name)


def checkpoint(port, request, action, evidence, *, checkpoint_path=None):
    """Apply the existing authority, never a transport-record shadow ledger.

Discussion mutation envelopes are supplied from the controller's saved intent;
their exact idempotency key and revisions must be retained on an unknown result.
"""
    entry.fields(port, {"context", "discussion"})
    context = port["context"]
    control.validate_current_context(context)
    require(context["controller_ref"] == request["controller_ref"], "identity_mismatch", "wrong checkpoint controller")
    if port["discussion"] is not None:
        envelope = port["discussion"]
        verify_discussion_port(envelope, request)
        require(envelope["actor_conversation_ref"] == context["controller_ref"] and
                envelope["actor_topic_id"] == context["topic_ref"], "identity_mismatch", "discussion checkpoint owner differs")
        actual = discussion_protocol.handle({**envelope, "operation": "workflow-control", "action": action,
            "evidence": evidence,
            **({'scope_checkpoint_path': checkpoint_path} if checkpoint_path is not None else {})})
        return actual["control"], actual
    # Downstream Phase Runs own lifecycle, while execution control stays in the
    # caller checkpoint. Attached dedicated-stage mutations must use the ledger.
    require(context["topic_ref"] is None or context["stage"] >= 2, "authority_missing", "attached dedicated control requires its ledger adapter")
    payload = {"schema_version": context["schema_version"], "actor_ref": context["controller_ref"], "context": context,
               "action": action, "evidence": evidence}
    if action in {'archive', 'archive-result'}:
        # Retired task cleanup has no Git authority. Historical stage baselines
        # must not be compared against the current native writer's baseline.
        require(context['topic_ref'] is None, 'authority_missing', 'attached archive recovery requires the original discussion ledger')
        return control.transition(payload), None
    if context["stage"] >= 2 or action == "receive":
        repository = request["binding"]["repository"] if action == "closure-result" else request["expected_entry"]["repository"]["root"]
        baseline = request['scope']['baseline']
        if request['role'] == 'execution-agent':
            progress = context.get('handoff_progress') or {}
            require('git_baseline_commit' in progress and request['binding'] == progress.get('binding'),
                    'authority_changed', 'allocation requires its original dispatcher Git authority')
            scope = request['scope']
            require(scope['implementation_paths'] == progress['allowed_paths'] and
                    set(progress['protected_paths']) <= set(scope['protected_paths']),
                    'scope_changed', 'allocation must retain original implementation scope and protections')
            authority_base = progress['git_baseline_commit']
            handoff.ancestor(repository, authority_base, baseline)
            authority = request.get('readiness', {}).get('scope_authority')
            protected_commit = authority['candidate_commit'] if authority is not None else authority_base
            repository_path = Path(repository)
            integration = progress.get('integration_source')
            approved = set()
            if integration is not None:
                require(progress.get('integration_retained') is not None,
                        'source_changed', 'captured integration lacks its retained closure authority')
                if request.get('readiness') is not None:
                    import workflow_progress
                    checkpoint_state = workflow_progress.read_record(
                        Path(request['readiness']['checkpoint_path'])).get(workflow_progress.KEY)
                    require(checkpoint_state is not None and
                            checkpoint_state['control']['context'] == context,
                            'authority_changed', 'captured integration differs from the current Controller checkpoint')
                control_git.git(repository_path, 'merge-base', '--is-ancestor',
                                integration['snapshot']['head'], baseline)
                verified = (control_git.verified_planning_paths(repository_path, progress) |
                            control_git.retained_closure_paths(repository_path, progress))
                # Only the original C readiness pin can authorize a changed
                # protected document. Inline runs keep their original byte lock.
                if request.get('readiness') is not None:
                    approved = verified
            for relative in progress['protected_paths']:
                original = control_git.committed_fingerprint(repository_path, protected_commit, relative)
                expected = (integration['snapshot']['files'][relative]
                            if integration is not None and relative in approved else original)
                require((integration is None or
                         integration['snapshot']['files'][relative] == expected and
                         control_git.committed_fingerprint(repository_path,
                             integration['snapshot']['head'], relative) == expected) and
                        control_git.committed_fingerprint(repository_path, baseline, relative) == expected and
                        control_git.file_fingerprint(repository_path, relative) == expected and
                        (integration is None or protected_index_matches_head(repository_path, relative)),
                        'source_changed', 'allocation cannot move or change a protected source')
            # scope.baseline is this execution's current-HEAD snapshot. The
            # controller's frozen scope base remains the Git authority boundary.
            baseline = authority_base
        result = control_git.verified_transition({"repository": repository,
                   "baseline": baseline, "request": payload})
    else:
        result = control.transition(payload)
    return result, None


def port_result(result, applied):
    return {"context": result["context"], "discussion_receipt": applied}


def design_scope(request, saved, payload):
    """Re-read C's persisted seal before accepting published planning work."""
    checkpoint_path = request.get("scope_checkpoint_path")
    require(isinstance(checkpoint_path, str), "scope_incomplete",
            "sealed progression checkpoint required before design publication")
    checkpoint = Path(checkpoint_path)
    require(checkpoint.is_absolute() and str(checkpoint.resolve()) == str(checkpoint),
            "invalid_checkpoint", "canonical absolute progression checkpoint required")
    import workflow_progress as progress
    state = progress.read_projection(checkpoint, projection='scope', stage=2).record[progress.KEY]
    authority = request.get("scope_authority")
    require(isinstance(state, dict) and state.get("protocol") == progress.PROTOCOL and
            state.get("checkpoint_path") == str(checkpoint) and
            state.get("handoff") == saved and state.get("scope_authority") == authority,
            "scope_changed", "design result must use the current persisted scope authority")
    effective = handoff.effective_scope(saved, authority)
    require(authority["candidate_commit"] == payload["planning_commit"],
            "scope_changed", "published planning candidate differs from the sealed manifest basis")
    return effective


def inline_recovery_allocation(request, saved):
    """Require the exact C-owned allocation transaction for inline recovery."""
    if ('integration_recovery' not in saved['authorization'] or
            saved.get('readiness') is not None or saved['role'] != 'execution-agent'):
        return
    recorded_path = (request.get('record') or {}).get('scope_checkpoint_path')
    checkpoint_path = request.get('scope_checkpoint_path', recorded_path)
    require(recorded_path is None or checkpoint_path == recorded_path,
            'authority_changed', 'inline recovery checkpoint pin changed')
    state = handoff.verify_inline_recovery_pin(
        saved, saved['controller_ref'], saved['requirement_identity'],
        checkpoint_path)
    require(state['control'] == request['control'], 'authority_changed',
            'allocation must use the original current Controller control port')
    slots = [slot for slot in state.get('allocations', {}).values()
             if slot.get('handoff') == saved and
             slot.get('owner_ref') == state['control']['context']['carrier']['ref']]
    require(len(slots) == 1, 'authority_missing',
            'inline recovery requires its exact C-owned allocation')
    slot = slots[0]
    if request.get('operation') == 'prepare':
        authorized = slot.get('record') is None and slot.get('transaction') == request
    elif slot.get('transaction') is not None:
        authorized = slot['transaction'] == request and slot.get('record') == request.get('record')
    else:
        # C clears a completed transaction after saving B's returned record.
        # That immutable record supports readback; C serves completed replays.
        authorized = (request.get('operation') is None and
                      slot.get('record') == request.get('record') and
                      (slot.get('result') or {}).get('record') == request.get('record') and
                      slot['record']['status'] in {'received', 'accepted', 'not-created'})
    require(authorized,
            'authority_missing', 'inline recovery requires its exact C-owned allocation transaction')


def prepared(request):
    entry.fields(request, {"protocol", "operation", "handoff", "control"}, {"scope_checkpoint_path", "allocation_intent"})
    saved = handoff.verify(request["handoff"],
                           checkpoint_path=request.get("scope_checkpoint_path"))
    compact = saved if saved.get("kind") == "allocation-input" else None
    saved = handoff.resolve_allocation(saved)
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
    checkpoint_path = request.get('scope_checkpoint_path')
    if checkpoint_path is not None:
        if saved['role'] == 'execution-agent':
            require(saved.get('readiness') is None and
                    'integration_recovery' in saved['authorization'],
                    'invalid_checkpoint', 'Execution Agent checkpoint pin belongs to inline final recovery')
            inline_recovery_allocation(request, saved)
        else:
            require(saved['stage'] >= 2, 'invalid_request',
                    'Controller checkpoint pin belongs to its stage carrier')
            handoff.verify_stage_checkpoint_pin(saved, context, checkpoint_path)
    elif saved['role'] == 'execution-agent' and 'integration_recovery' in saved['authorization']:
        inline_recovery_allocation(request, saved)
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
                            testing_basis=saved["semantic"]["testing_basis"], validation_plan=saved["semantic"]["validation_plan"],
                            implementation_policy=control.implementation_policy(saved["semantic"].get("implementation_policy")))
            if saved.get('readiness') is not None or saved.get('inline_authority') is not None:
                authority = (saved['readiness']['scope_authority'] if saved.get('readiness') is not None
                             else saved['inline_authority'])
                evidence['execution_authority'] = authority
                evidence['plan_authority'] = {'revision': authority['revision'],
                    'authority_digest': entry.digest(authority),
                    'plan_digest': authority['execution_plan']['plan_digest'],
                    'contract_digest': authority['execution_plan']['contract_digest']}
        elif role == "execution-agent":
            action = "plan-execution"
            progress = selected["handoff_progress"] or {}
            original_manifest_base = (saved["readiness"]["scope_authority"]["scope"]["baseline"]
                if saved.get("readiness") is not None else progress.get("git_baseline_commit"))
            require(original_manifest_base is not None and
                    saved.get("manifest_baseline", scope["baseline"]) == original_manifest_base,
                    "scope_changed", "allocation manifest must retain the original sealed baseline")
            evidence.update(task_id=str(uuid.uuid4()), paths=scope["owned_paths"], read_only=scope["protected_paths"],
                            behavior=saved["semantic"]["objective"], tests=saved["semantic"]["completion_criteria"], git_operations=[])
            if saved.get('readiness') is not None or saved.get('inline_authority') is not None:
                authority = (saved['readiness']['scope_authority'] if saved.get('readiness') is not None
                             else saved['inline_authority'])
                catalog = execution_plan.current_catalog(authority)
                group = execution_plan.validate_plan_refs(catalog, saved['semantic'].get('plan_refs'),
                    scope['owned_paths'], scope['protected_paths'],
                    progress.get('carried_results', []) + progress.get('executions', []), authority)
                require(progress.get('plan_authority') == group['plan_authority'],
                        'execution_plan_stale', 'current control authority differs from allocation')
                evidence.update(plan_refs=group['plan_refs'], plan_authority=group['plan_authority'],
                    group_digest=group['group_digest'], tests=group['verification_commands'])
            if saved['semantic'].get('adopt_paths'):
                evidence.update(adopt_paths=saved['semantic']['adopt_paths'],
                    adoption_snapshot_digest=saved['semantic']['adoption_snapshot_digest'])
        else:
            action = "start-closure"
            prior = saved["predecessor"]
            evidence.update(candidate=prior["payload"]["candidate_commit"], dispatcher_ref=prior["role_ref"],
                            review=prior["payload"]["review"], verification=prior["payload"]["verification"], binding=saved["binding"],
                            implementation_paths=scope["implementation_paths"], closure_paths=scope["closure_paths"], protected_paths=scope["protected_paths"])
        result, applied = checkpoint(request["control"], saved, action, evidence,
                                     checkpoint_path=checkpoint_path)
        attempt = result.get("allocation_digest", result["context"]["carrier"]["attempt"])
    if compact is not None:
        # Payload materialization belongs to C's persisted issue boundary.
        launch = handoff.seal({"kind": "native", "role": role,
            "controller_ref": saved["controller_ref"], "attempt": attempt,
            "input_digest": compact["digest"], "configuration": saved["selection"]})
        record = handoff.seal({"protocol": PROTOCOL, "kind": "dispatch", "allocation": allocation_reference(compact),
            "request": launch, "receipts": [], "delivery": None, "acceptance": None, "status": "prepared"})
        if saved['semantic'].get('adopt_paths'):
            import workflow_state_store
            store = workflow_state_store.open_store(compact['checkpoint'])
            current = store.open_current()
            progress = result['context']['handoff_progress']
            source = control.adoption_source(progress)
            inheritance = {'baseline': progress['git_baseline_commit'], 'snapshot': source['snapshot'],
                'snapshot_digest': source['snapshot_digest'], 'adopt_paths': saved['semantic']['adopt_paths'],
                'pending_paths': source['pending_paths'], 'accepted': False}
            record = handoff.seal({**handoff.unseal(record), 'inheritance_ref': store.write_object(
                current.run_id, 'evidence', {'allocation_inheritance': inheritance})})
        return {"status": "prepared", "record": record, "checkpoint": port_result(result, applied),
                "downstream_ready": False}
    payload = handoff.render(saved)["payload"]
    if role == 'execution-agent' and saved['semantic'].get('adopt_paths'):
        progress = result['context']['handoff_progress']
        source = control.adoption_source(progress)
        payload['inherited_implementation'] = {'baseline': progress['git_baseline_commit'],
            'snapshot': copy.deepcopy(source['snapshot']), 'snapshot_digest': source['snapshot_digest'],
            'adopt_paths': saved['semantic']['adopt_paths'], 'pending_paths': source['pending_paths'], 'accepted': False}
    native = saved["stage"] >= 2
    launch = handoff.seal({"kind": "native" if native else "visible-task", "role": role,
                          "controller_ref": saved["controller_ref"], "attempt": attempt,
                          "input_digest": saved["digest"], "configuration": saved["selection"], "payload": payload})
    if not native:
        launch = handoff.seal({**handoff.unseal(launch), "dedicated_plan": plan})
    record = handoff.seal({"protocol": PROTOCOL, "kind": "dispatch", "handoff": saved, "request": launch,
                          "receipts": [], "delivery": None, "acceptance": None, "status": "prepared"})
    if checkpoint_path is not None:
        record = handoff.seal({**handoff.unseal(record), 'scope_checkpoint_path': checkpoint_path})
    return {"status": "prepared", "record": record, "checkpoint": port_result(result, applied),
            "host_call": launch, "downstream_ready": False,
            "required_order": ["persist-record-and-checkpoint", "verify-input-and-target-configuration", "invoke-host-once", "save-receipt", "bind"]}


def allocation_reference(saved):
    import workflow_state_store
    store = workflow_state_store.open_store(saved['checkpoint'])
    current = store.open_current()
    require(current.run_id == saved['run_id'], 'identity_mismatch', 'allocation run changed')
    return {'checkpoint': saved['checkpoint'], 'run_id': current.run_id,
            'reference': store.write_object(current.run_id, 'evidence', {'allocation_input': saved})}


def allocation_descriptor(record):
    import workflow_state_store
    pin = record['allocation']
    entry.fields(pin, {'checkpoint', 'run_id', 'reference'})
    store = workflow_state_store.open_store(pin['checkpoint'])
    current = store.open_current()
    require(current is not None and current.run_id == pin['run_id'],
            'identity_mismatch', 'allocation belongs to another run')
    value = store.read_evidence(pin['reference'], run_id=current.run_id)['value']
    require(isinstance(value, dict) and set(value) == {'allocation_input'},
            'invalid_evidence', 'allocation descriptor required')
    saved = value['allocation_input']
    require(saved.get('kind') == 'allocation-input' and saved.get('checkpoint') == pin['checkpoint'] and
            saved.get('run_id') == current.run_id, 'identity_mismatch', 'allocation descriptor changed')
    handoff.unseal(saved)
    return saved


def allocation_receipt(record, receipt):
    """Keep the authenticated external response once; records hold its facts/ref."""
    if 'allocation' not in record:
        return copy.deepcopy(receipt)
    import workflow_state_store
    pin = record['allocation']
    store = workflow_state_store.open_store(pin['checkpoint'])
    current = store.open_current()
    require(current.run_id == pin['run_id'], 'identity_mismatch', 'allocation receipt run changed')
    reference = store.write_object(current.run_id, 'evidence', {'allocation_receipt': receipt})
    return {**{key: copy.deepcopy(value) for key, value in receipt.items() if key != 'raw'}, 'raw_ref': reference}


def verify_allocation_receipts(record):
    if 'allocation' not in record:
        return
    import workflow_state_store
    pin = record['allocation']
    store = workflow_state_store.open_store(pin['checkpoint'])
    current = store.open_current()
    for summary in record['receipts']:
        require('raw' not in summary and 'raw_ref' in summary, 'invalid_evidence', 'referenced native receipt required')
        value = store.read_evidence(summary['raw_ref'], run_id=current.run_id)['value']
        require(isinstance(value, dict) and set(value) == {'allocation_receipt'},
                'invalid_evidence', 'native receipt object changed')
        original = value['allocation_receipt']
        require({key: value for key, value in original.items() if key != 'raw'} ==
                {key: value for key, value in summary.items() if key != 'raw_ref'},
                'identity_mismatch', 'native receipt facts changed')
        receipt_for(record, original, {'create', 'lookup', 'result'})


def dispatch_input(record):
    return handoff.resolve_allocation(allocation_descriptor(record)) if 'allocation' in record else record['handoff']


def allocation_launch(record):
    """Materialize only this native invocation from its immutable contract refs."""
    require('allocation' in record, 'invalid_request', 'shared allocation required')
    compact = allocation_descriptor(record)
    payload = handoff.render(compact)['payload']
    if compact['semantic'].get('adopt_paths'):
        import workflow_state_store
        store = workflow_state_store.open_store(compact['checkpoint'])
        value = store.read_evidence(record.get('inheritance_ref'), run_id=compact['run_id'])['value']
        require(isinstance(value, dict) and set(value) == {'allocation_inheritance'},
                'invalid_evidence', 'original allocation inheritance required')
        inherited = value['allocation_inheritance']
        require(inherited.get('adopt_paths') == compact['semantic']['adopt_paths'] and
                inherited.get('snapshot_digest') == compact['semantic']['adoption_snapshot_digest'],
                'identity_mismatch', 'allocation inheritance changed')
        payload['inherited_implementation'] = inherited
    else:
        require('inheritance_ref' not in record, 'invalid_evidence', 'ordinary allocation has no inheritance')
    launch = {key: record['request'][key] for key in
              ('kind', 'role', 'controller_ref', 'attempt', 'input_digest', 'configuration')}
    return handoff.seal({**launch, 'payload': payload})


def verified_launch(record):
    if 'allocation' not in record or 'payload_digest' not in record['request']:
        return handoff.unseal(record['request'])
    rendered = allocation_launch(record)
    expected = {key: value for key, value in rendered.items() if key != 'payload'}
    expected['payload_digest'] = entry.digest(rendered['payload'])
    require(record['request'] == expected, 'identity_mismatch', 'allocation launch changed')
    return record['request']


def record_for(request):
    record = handoff.unseal(request["record"])
    entry.fields(record, {"protocol", "kind", "request", "receipts", "delivery", "acceptance", "status"},
                 {"scope_checkpoint_path", "handoff", "allocation", "inheritance_ref"})
    require(("handoff" in record) != ("allocation" in record), "invalid_evidence", "one dispatch input required")
    handoff.require_current_protocol(record)
    require(record["kind"] == "dispatch", "invalid_evidence", "dispatch record required")
    launch = verified_launch(record)
    verify_allocation_receipts(record)
    handoff.require_current_protocol(handoff.unseal(dispatch_input(record)))
    if dispatch_input(record)["stage"] == 3 and "readiness" in dispatch_input(record) and \
            dispatch_input(record)["role"] == "execution-agent":
        handoff.current_readiness(dispatch_input(record)["readiness"])
    require(launch["input_digest"] == (allocation_descriptor(record) if "allocation" in record else record["handoff"])["digest"], "identity_mismatch", "request belongs to another input")
    control.validate_context(request["control"]["context"])
    require(request["control"]["context"]["controller_ref"] == launch["controller_ref"], "identity_mismatch", "checkpoint owner changed")
    inline_recovery_allocation(request, dispatch_input(record))
    if request["control"]["discussion"] is not None:
        envelope = request["control"]["discussion"]
        verify_discussion_port(envelope, dispatch_input(record))
        base = {k: envelope[k] for k in ("protocol_version", "project_path", "project_id", "tree_id", "actor_topic_id", "actor_conversation_ref")}
        current = discussion_protocol.handle({**base, "operation": "read-topic"})
        require(current["workflow_control"] is not None, "authority_missing", "discussion has no workflow control checkpoint")
        control.validate_context(current["workflow_control"])
        require(current["workflow_control"]["controller_ref"] == launch["controller_ref"], "identity_mismatch", "ledger controller changed")
        request["control"] = {**request["control"], "context": current["workflow_control"]}
    if dispatch_input(record)["stage"] == 3 and dispatch_input(record)["role"] == "implementation-dispatcher" and \
            ("readiness" in dispatch_input(record) or "scope_checkpoint_path" in record):
        state = handoff.current_stage3_state(dispatch_input(record), request["control"]["context"],
                                             record.get('scope_checkpoint_path'))
        if state is not None:
            request["_current_stage3_progress"] = state["control"]["context"]["handoff_progress"]
    elif dispatch_input(record)["stage"] == 3 and dispatch_input(record)["role"] == "implementation-dispatcher":
        require(not (request['control']['context'].get('handoff_progress') or {}).get('plan_revisions'),
                'handoff_not_ready', 'revised standalone delivery needs its original Controller checkpoint pin')
    if dispatch_input(record)["stage"] == 4 and "scope_checkpoint_path" in record:
        handoff.verify_stage_checkpoint_pin(dispatch_input(record), request["control"]["context"],
                                            record["scope_checkpoint_path"])
    return record


def matching(record, context, *, reconcile_unbound=False):
    launch = record["request"]
    selected = control.selected_control(context, {"attempt": launch["attempt"]})
    if launch["role"] == "execution-agent":
        progress = selected["handoff_progress"] or {}
        matches = [item for item in progress.get("executions", []) if item["allocation_digest"] == launch["attempt"]]
        require(len(matches) == 1, "attempt_mismatch", "allocation is no longer eligible")
        item = matches[0]
        semantic = dispatch_input(record)['semantic']
        if semantic.get('plan_refs') is not None:
            authority = (dispatch_input(record)['readiness']['scope_authority']
                         if dispatch_input(record).get('readiness') is not None else
                         dispatch_input(record)['inline_authority'])
            expected = {'revision': semantic['authority_revision'],
                'authority_digest': semantic['authority_digest'],
                'plan_digest': semantic['plan_digest'],
                'contract_digest': authority['execution_plan']['contract_digest']}
            require(progress.get('plan_authority') == expected and item.get('plan_authority') == expected and
                    item.get('plan_refs') == semantic['plan_refs'],
                    'execution_plan_stale', 'allocation no longer belongs to current plan authority')
        cancelled_lookup = (reconcile_unbound and progress["state"] == "cancelled" and item["agent_ref"] is None
                            and (item["state"] == "dispatch-pending" or item["state"] == "cancelled" and item["stopped"] is True))
        require(progress["state"] == "implementing" or cancelled_lookup, "attempt_mismatch", "allocation is no longer eligible")
        return item["agent_ref"], item["state"], item
    carrier = selected["carrier"]
    require(carrier is not None and carrier["attempt"] == launch["attempt"], "attempt_mismatch", "checkpoint belongs to another attempt")
    return carrier["ref"], selected["handoff_progress"]["state"], selected["handoff_progress"]


def execution_release_token(record):
    launch = record['request']
    require(launch['role'] == 'execution-agent', 'role_mismatch', 'write release belongs to an Execution Agent')
    ready = next((receipt for receipt in record['receipts'] if receipt['status'] == 'ready'), None)
    require(ready is not None, 'authorization_missing', 'bound Execution Agent receipt required')
    semantic = dispatch_input(record)['semantic']
    return entry.digest([launch['attempt'], ready['receipt_ref'], ready['ref'],
                         semantic.get('authority_revision'), semantic.get('authority_digest'),
                         semantic.get('plan_digest'), semantic.get('plan_refs')])


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
    saved = dispatch_input(record)
    if saved["stage"] == 2 and saved["entry"]["source"]["kind"] == "discussion":
        phase = handoff.phase_evidence(saved)
        known = phase["attempt"].get("carrier_ref")
        if known is not None:
            # A recovered designer inherits this exact original Phase Run via
            # the persisted control lineage; never rebind or create a new run.
            handoff.phase_evidence(saved, role_ref=receipt["ref"], control_context=port["context"])
            return port, None
        binding = saved["authorization"]["phase"]
        applied = discussion_protocol.handle({"protocol_version": 1, "operation": "authorize-phase-carrier",
                     "project_path": entry.discussion_root(saved["expected_entry"]), **saved["entry"]["source"]["attachment"],
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
    entry.fields(request, {"protocol", "operation", "record", "control", "receipt"},
                 {"scope_checkpoint_path", "allocation_intent"})
    record = record_for(request)
    require(not any(isinstance(receipt, dict) and receipt.get("event") == "existing-dispatcher"
                    for retained_receipt in record["receipts"]),
            "invalid_operation", "an inherited Dispatcher has no creation receipt to bind")
    launch = record['request']
    receipt = request["receipt"]
    receipt_for(record, receipt, {"create"} if request["operation"] == "bind" else {"lookup"})
    require(receipt["status"] in {"ready", "pending", "unknown", "not-created"}, "invalid_request", "unknown launch outcome")
    require((receipt["status"] == "ready") == (receipt["ref"] is not None), "identity_mismatch", "only ready results carry usable identities")
    require(receipt["status"] != "ready" or receipt["pending_id"] is None, "identity_mismatch", "pending identity cannot be bound")
    reconcile_unbound = (request["operation"] == "reconcile" and record["request"]["role"] == "execution-agent"
                         and receipt["status"] in {"unknown", "pending", "not-created"})
    retained_receipt = allocation_receipt(record, receipt)
    known, state, item = matching(record, request["control"]["context"], reconcile_unbound=reconcile_unbound)
    if reconcile_unbound and receipt["status"] == "not-created" and known is None and state == "cancelled" and item["stopped"] is True:
        record["status"] = "not-created"
        if retained_receipt not in record["receipts"]:
            record["receipts"].append(retained_receipt)
        return {"status": "not-created", "record": handoff.seal(record), "acknowledged": True,
                "checkpoint": {"context": request["control"]["context"], "discussion_receipt": None}, "downstream_ready": False}
    if retained_receipt in record["receipts"]:
        require(known == receipt["ref"] or known is None, "identity_mismatch", "replayed receipt conflicts with checkpoint")
        result = {"status": record["status"], "record": request["record"], "acknowledged": True, "downstream_ready": False}
        if launch['role'] == 'execution-agent' and receipt['status'] == 'ready':
            result['write_release'] = execution_release_token(record)
        return result
    require(state not in {"cancelled", "creation-failed", "accepted"}, "attempt_mismatch", "late receipt cannot revive an ended attempt")
    if known is not None:
        require(receipt["status"] == "ready" and known == receipt["ref"], "identity_mismatch", "bound identity cannot be replaced")
        record["receipts"].append(retained_receipt)
        record["status"] = "bound"
        result = {"status": "bound", "record": handoff.seal(record), "acknowledged": True,
                  "checkpoint": {"context": request["control"]["context"], "discussion_receipt": None}, "downstream_ready": False}
        if launch['role'] == 'execution-agent':
            result['write_release'] = execution_release_token(record)
        return result
    if record["receipts"]:
        require(request["operation"] == "reconcile", "outcome_unknown", "read back the original launch before retrying")
        pending = {r["pending_id"] for r in record["receipts"] if r["pending_id"] is not None}
        if receipt["pending_id"] is not None:
            require(not pending or receipt["pending_id"] in pending, "identity_mismatch", "pending identity changed")
    ready = receipt["status"] == "ready"
    if launch["kind"] == "visible-task":
        action = "creation-result"
        evidence = {"attempt": launch["attempt"], "ref": receipt["ref"],
                    "status": "failed" if receipt["status"] == "not-created" else receipt["status"]}
    elif launch["role"] == "execution-agent":
        action = "assign" if ready else "execution-dispatch-result"
        evidence = {"task_id": item["task_id"], "allocation_digest": launch["attempt"]}
        evidence.update({"agent_ref": receipt["ref"]} if ready else {"status": "not-created" if receipt["status"] == "not-created" else "unknown"})
        if ready:
            evidence['write_release'] = execution_release_token({**record, 'receipts': [*record['receipts'], receipt]})
    elif ready:
        action = {"solution-designer": "designer-bound", "implementation-dispatcher": "dispatcher-bound", "closure-agent": "closure-bound"}[launch["role"]]
        evidence = {"ref": receipt["ref"], "attempt": launch["attempt"]}
    else:
        action = "native-dispatch-result"
        evidence = {"attempt": launch["attempt"], "status": "not-created" if receipt["status"] == "not-created" else "unknown"}
    # A created Execution Agent must retain its exact receipt even if the Git
    # adapter rejects binding because assigned bytes changed before handoff.
    port, binding_receipt = request["control"], None
    try:
        if ready:
            port, binding_receipt = bind_discussion(port, record, receipt)
        result, applied = checkpoint(port, dispatch_input(record), action, evidence)
    except entry.ERROR_TYPES as error:
        raise entry.PreparationError("binding_incomplete", "Host creation evidence retained; reconcile this exact attempt, never create again.",
              completed_evidence=[{"kind": "host-creation", "receipt": receipt, "binding_receipt": binding_receipt, "downstream_ready": False}]) from error
    record["receipts"].append(retained_receipt)
    record["status"] = "bound" if ready else receipt["status"]
    bound_result = {"status": record["status"], "record": handoff.seal(record), "checkpoint": port_result(result, applied), "binding_receipt": binding_receipt,
                    "downstream_ready": False, "next_action": "receive" if ready else "stop" if receipt["status"] == "not-created" else "lookup-exact-request",
                    "lookup": None if ready else {"request_digest": launch["digest"], "attempt": launch["attempt"], "pending_id": receipt["pending_id"]}}
    if ready and launch['role'] == 'execution-agent':
        bound_result['write_release'] = execution_release_token(record)
    return bound_result


def received(request):
    entry.fields(request, {"protocol", "operation", "record", "control", "receipt", "result"},
                 {"scope_checkpoint_path", "scope_authority", "allocation_intent"})
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
    saved = dispatch_input(record)
    stage, payload = saved["stage"], message["payload"]
    if stage >= 2:
        handoff.phase_evidence(saved, role_ref=ref, receiving=True, control_context=request["control"]["context"])
    root = saved["expected_entry"]["repository"]["root"]
    if stage != 4:
        current = handoff.refresh(saved["entry"], saved["expected_entry"], after_work=True)
        if stage >= 2:
            handoff.source(current, saved["requirement"], stage,
                           attached_controller_entry=handoff.attached_source_entry(saved))
    if saved["role"] == "execution-agent":
        entry.fields(payload, {"changed_paths", "file_hashes", "tests", "write_release"},
                     {"adopted_paths", "plan_refs", "plan_authority"})
        require(payload['write_release'] == execution_release_token(record),
                'authorization_changed', 'Execution Agent did not receive its bound write release')
        evidence = {key: payload[key] for key in ("changed_paths", "file_hashes", "tests")}
        if 'adopted_paths' in payload:
            evidence['adopted_paths'] = payload['adopted_paths']
        evidence.update(agent_ref=ref, stopped=True, git_unchanged=True)
        if saved['semantic'].get('plan_refs') is not None:
            require(payload.get('plan_refs') == saved['semantic']['plan_refs'] and
                    payload.get('plan_authority') == item['plan_authority'],
                    'execution_plan_stale', 'Execution Agent result names an older plan')
            evidence.update(plan_refs=payload['plan_refs'], plan_authority=payload['plan_authority'],
                            write_release=payload['write_release'])
        action = "execution-result"
    else:
        if stage == 2:
            design_scope(request, saved, payload)
        handoff.verify_result(stage, payload, saved["binding"], saved["scope"], root, ref,
                              saved['authorization'].get('integration_recovery'),
                              request.get('_current_stage3_progress'))
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
            action = "delivery-ready"
            evidence = {"dispatcher_ref":ref, "candidate":payload['candidate_commit'],
                        "review":payload['review'], "verification":payload['verification']}
        else:
            require(payload["candidate_commit"] == saved["predecessor"]["payload"]["candidate_commit"], "source_changed", "closure candidate differs from accepted input")
            action = "closure-result"
            evidence = {"ref": ref, "candidate": payload["candidate_commit"], "merge": payload["merge_commit"],
                        "ancestor_verified": True, "changed_paths": payload["changed_paths"], "binding": saved["binding"],
                        "checks": payload["checks"], **payload["cleanup"], "implementation_problem": None}
    result, applied = checkpoint(request["control"], saved, action, evidence)
    record.update(status="received", delivery=claim)
    record["receipts"].append(allocation_receipt(record, request["receipt"]))
    return {"status": "received", "record": handoff.seal(record), "checkpoint": port_result(result, applied), "downstream_ready": False}


def accepted(request):
    entry.fields(request, {"protocol", "operation", "record", "control", "decision"},
                 {"scope_checkpoint_path", "scope_authority", "allocation_intent"})
    record = record_for(request)
    entry.fields(request["decision"], {"reference", "delivery_digest"})
    entry.nonempty(request["decision"]["reference"])
    require(record["delivery"] is not None and request["decision"]["delivery_digest"] == record["delivery"]["digest"],
            "authorization_changed", "acceptance must name the received delivery")
    saved, claim = dispatch_input(record), record["delivery"]
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
            handoff.phase_evidence(saved, role_ref=ref, receiving=True, control_context=request["control"]["context"])
            if stage != 4:
                current = handoff.refresh(saved["entry"], saved["expected_entry"], after_work=True)
                handoff.source(current, saved["requirement"], stage,
                               attached_controller_entry=handoff.attached_source_entry(saved))
        if saved["role"] == "execution-agent":
            evidence = {"agent_ref": ref, "file_hashes": payload["file_hashes"]}
            if saved['semantic'].get('plan_refs') is not None:
                evidence.update(plan_refs=payload['plan_refs'], plan_authority=payload['plan_authority'],
                                write_release=payload['write_release'])
            result, applied = checkpoint(request["control"], saved, "accept-execution", evidence)
        else:
            if stage == 2:
                design_scope(request, saved, payload)
            handoff.verify_result(stage, payload, saved["binding"], saved["scope"], root, ref,
                                  saved['authorization'].get('integration_recovery'),
                                  request.get('_current_stage3_progress'))
            if stage < 2:
                current = handoff.refresh(saved["entry"], saved["expected_entry"], after_work=True)
                identity, revision = handoff.source(current, payload["requirement"], stage)
                if stage == 1:
                    handoff.delivery({**saved, "requirement": payload["requirement"], "delivery": payload["delivery"]}, current, identity, revision)
                result, applied = checkpoint(request["control"], saved, "accept", {"delivery_digest": item["delivery_digest"]})
            elif stage == 2:
                result, applied = checkpoint(request["control"], saved, "accept-design", {"ref": ref, "result_digest": claim["digest"]})
            elif stage == 3:
                result, applied = checkpoint(request['control'], saved, 'accept-delivery',
                    {'dispatcher_ref':ref,'candidate':payload['candidate_commit'],'review':payload['review'],'verification':payload['verification'], 'delivery_digest':claim['digest']})
        record["status"] = "accepted"
    identity = saved["requirement_identity"]
    if saved["stage"] < 2:
        source = payload["requirement"]
        identity = source.get("requirement_identity", {k: source.get(k) for k in ("path", "sha256", "version")})
    if "allocation" in record:
        outcome = handoff.seal({"protocol": PROTOCOL, "status": "accepted",
            "allocation_digest": allocation_descriptor(record)['digest'], "request_digest": record['request']['digest'],
            "role_ref": ref, "attempt": record['request']['attempt'],
            "requirement_identity": identity, "delivery_digest": claim['digest'],
            "decision": request['decision'], "downstream_ready": False})
        record['acceptance'] = outcome
        return {"status": "accepted", "record": handoff.seal(record),
                "checkpoint": port_result(result, applied), "accepted": outcome, "downstream_ready": False}
    outcome = handoff.seal({"protocol": PROTOCOL, "status": "accepted", "handoff": saved,
               "request_digest": record["request"]["digest"], "role_ref": ref, "attempt": record["request"]["attempt"],
               "requirement_identity": identity, "payload": payload, "delivery_digest": claim["digest"],
               "decision": request["decision"],
               **({"planning_complete": True} if saved["stage"] == 2 else {}),
               "downstream_ready": saved["role"] not in {"execution-agent", "solution-designer"}})
    record["acceptance"] = outcome
    return {"status": "accepted", "record": handoff.seal(record), "checkpoint": port_result(result, applied),
            "accepted": outcome, "downstream_ready": outcome["downstream_ready"]}


def verify_allocation_intent(request):
    """Consume the original C intent through the exact read-only transaction."""
    if request.get('operation') == 'prepare':
        saved = request.get('handoff', {})
    elif 'allocation' in (request.get('record') or {}):
        saved = allocation_descriptor(handoff.unseal(request['record']))
    else:
        return
    if saved.get('kind') != 'allocation-input':
        return
    intent = request.get('allocation_intent')
    require(isinstance(intent, str) and intent, 'authority_missing',
            'referenced allocation requires its original Controller intent')
    import workflow_progress
    state = workflow_progress.read_projection(saved['checkpoint'], projection='transaction',
        subject_id=saved['subject_id'], allocation_id=saved['allocation_id'], intent=intent).record[workflow_progress.KEY]
    supplied = {key: value for key, value in request.items() if key not in {'control', 'allocation_intent'}}
    require(state['handoff_digest'] == saved['digest'] and state['operation'] == request['operation'] and
            state['request_digest'] == entry.digest(supplied) and
            state['base_control_digest'] == entry.digest(request['control']),
            'authority_changed', 'allocation request differs from the original Controller intent')


def handle(request):
    request = copy.deepcopy(request)
    require(request.get("protocol") == PROTOCOL, "legacy_run_requires_original_runtime", "retain original stage-transfer runtime: " + str(request.get("protocol")))
    operation = request.get("operation")
    require(operation in {"prepare", "bind", "reconcile", "receive", "accept"}, "invalid_operation", "unknown dispatch operation")
    verify_allocation_intent(request)
    return {"prepare": prepared, "bind": bound, "reconcile": bound, "receive": received, "accept": accepted}[operation](request)


if __name__ == "__main__":
    sys.exit(handoff.cli(handle))
