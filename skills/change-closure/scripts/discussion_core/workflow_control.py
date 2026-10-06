"""Persist controller preferences/checkpoints in the existing discussion ledger."""

from .state import (ProtocolError, _active_pending_write, _canonical_json, _flock_with_timeout,
                    _idempotent_result, _json_field, _load_records, _record_by_id,
                    _sha256, _require_regular_nosymlink, _validate_revisions,
                    _verify_topic_owner, _write_ledger_transaction, _mutation_fingerprint)
from .handoffs import _handoff_mutation_context, _handoff_data, _store_handoff
from .topic_dependencies import apply_gate_policy

from pathlib import Path
import workflow_control as _control
import workflow_control_git as _git


def read_control_projection(ledger_path, record, *, selector=None):
    import workflow_state_store as _store
    pointer = _json_field(record, 'data_json', 'workflow control')
    if pointer.get('format') != 'ledger-control-v1':
        raise ProtocolError('legacy_run_requires_original_runtime', 'retain original embedded ledger control runtime')
    if pointer.get('topic_id') != record['topic_id'] or pointer.get('control_revision') != record['record_revision']:
        raise ProtocolError('state_corrupt', 'ledger control pointer differs from its record')
    try:
        context, _ = _store.open_store(ledger_path).read_ledger_control(pointer, selector=selector)
        _control.validate_current_context(context)
    except (_store.entry.PreparationError, _control.ControlError) as error:
        raise ProtocolError('state_corrupt', str(error)) from error
    return context, pointer


def public_control_result(result, ledger_path):
    """Expand only the original bounded current projection, after ledger persistence."""
    import workflow_state_store as _store
    value = result['control']
    if 'store_ref' not in value:
        return result
    try:
        context, _ = _store.open_store(ledger_path).read_ledger_control(value['store_ref'])
    except _store.entry.PreparationError as error:
        raise ProtocolError('state_corrupt', str(error)) from error
    return {**result, 'control': {**value, 'context': context}}


GIT_BACKED_ACTIONS = {
    'start-design', 'start-dispatch', 'start-closure', 'design-result',
    'plan-execution', 'assign', 'execution-result', 'accept-execution',
    'plan-revision-activate', 'prepare-dispatch-recovery', 'recover-dispatch',
    'escalate-implementation', 'capture-integration', 'candidate-ready',
    'review-start', 'review-converged', 'validation-start', 'validation-result',
    'validation-retry', 'invalidate-candidate', 'delivery-ready',
    'accept-delivery', 'closure-result',
}


def _start_git_authority(request, context, evidence, action):
    """Resolve a start baseline from the C-owned pending B request, not HEAD."""
    import stage_handoff as _handoff
    import workflow_progress as _progress

    checkpoint_path = request.get('scope_checkpoint_path')
    if not isinstance(checkpoint_path, str):
        raise ProtocolError('invalid_request', 'attached start requires its original Controller checkpoint')
    checkpoint = Path(checkpoint_path)
    if not checkpoint.is_absolute() or str(checkpoint.resolve()) != checkpoint_path:
        raise ProtocolError('invalid_request', 'attached start checkpoint must be canonical')
    identity = _progress.read_projection(checkpoint, projection='identity')
    subject_id = identity.record.get(_progress.KEY, {}).get('stage_subject')
    transaction = _progress.read_projection(checkpoint, projection='transaction',
                                           subject_id=subject_id, intent='prepare')
    state = transaction.record.get(_progress.KEY)
    pending = state.get('transaction') if isinstance(state, dict) else None
    saved = pending.get('handoff') if isinstance(pending, dict) else None
    if not isinstance(saved, dict):
        raise ProtocolError('invalid_request', 'attached start has no persisted B input')
    _handoff.unseal(saved)
    expected_role = {'start-design': 'solution-designer',
                     'start-dispatch': 'implementation-dispatcher',
                     'start-closure': 'closure-agent'}[action]
    if not (state.get('checkpoint_path') == checkpoint_path and
            transaction.authority_revision == identity.authority_revision and
            state.get('handoff') == saved and state.get('stage') == saved.get('stage') and
            state.get('control_digest') == _progress.entry.digest(context) and
            state.get('control_ref') == request.get('expected_control_ref') and
            pending.get('operation') == 'prepare' and
            pending.get('scope_checkpoint_path') == checkpoint_path and
            pending.get('base_control_digest') == state.get('control_port_digest') and
            type(pending.get('base_revision')) is int and
            0 <= pending['base_revision'] < state.get('revision') and
            saved.get('controller_ref') == context.get('controller_ref') and
            saved.get('role') == expected_role and
            saved.get('binding') == evidence.get('binding')):
        raise ProtocolError('invalid_request', 'attached start differs from the original C/B authority')
    repository = saved['binding']['worktree']
    baseline = saved['scope']['baseline']
    if _git.snapshot(repository)['head'] != baseline or (action == 'start-dispatch' and
            _git.git(Path(repository), 'status', '--porcelain')):
        raise ProtocolError('source_changed', 'attached start changed after its frozen B baseline')
    return repository, baseline


def _entry_candidates(records, topic, request, owner_ref, identity):
    candidates = []
    for record in records['Phase Runs']:
        if record.get('run_kind') == 'discussion-handoff':
            data = _handoff_data(record)
            if (data.get('kind') != 'dedicated-stage' or data.get('target_topic_id') != topic['topic_id']
                    or data.get('controller_ref') != owner_ref or data.get('stage') != topic['current_phase']):
                continue
            if data.get('requirement_baseline') != {'revision': identity['version'], 'sha256': identity['sha256']} and any(a.get('binding_eligible') and not a.get('delivery_frozen') for a in data['attempts']):
                raise ProtocolError('phase_source_drift', 'dedicated handoff baseline changed before planning')
            kind, stage = 'dedicated-stage', data['stage']
            attempts = [a for a in data['attempts'] if a.get('binding_eligible') and not a.get('delivery_frozen')]
        elif record.get('run_kind') == 'phase-run':
            data = _json_field(record, 'data_json', 'phase run')
            if (data.get('wrapper_integration') is not True or data.get('carrier_kind') != 'dedicated-grilling'
                    or data.get('source_topic_id') != topic['topic_id'] or data.get('from_phase') != topic['current_phase']
                    or (data.get('from_phase'), data.get('to_phase')) != (0, 1)):
                continue
            kind, stage = 'wrapper-phase-run', 1
            attempts = [a for a in data['attempts'] if a.get('state') in {'setup-pending', 'ready', 'active'}]
        else:
            continue
        for attempt in attempts:
            candidates.append({'kind': kind, 'project_id': request['project_id'], 'tree_id': request['tree_id'],
                'topic_id': topic['topic_id'], 'controller_ref': owner_ref, 'source_phase': topic['current_phase'],
                'stage': stage, 'run_id': record['run_id'], 'attempt_id': attempt['attempt_id']})
    if len(candidates) != 1:
        raise ProtocolError('handoff_identity_conflict', 'control plan requires one exact eligible entry authority')
    return candidates[0]


def _entry_attempt(records, authority, request, owner_ref):
    if any(authority.get(k) != v for k, v in {
        'project_id': request['project_id'], 'tree_id': request['tree_id'],
        'topic_id': request['actor_topic_id'], 'controller_ref': owner_ref}.items()):
        raise ProtocolError('handoff_identity_conflict', 'entry authority identity changed')
    record = _record_by_id(records['Phase Runs'], 'run_id', authority['run_id'], 'entry authority')
    data = _json_field(record, 'data_json', 'entry authority')
    if authority['kind'] == 'dedicated-stage':
        valid = (record.get('run_kind') == 'discussion-handoff' and data.get('kind') == 'dedicated-stage'
            and data.get('stage') == authority['stage'] and data.get('controller_ref') == owner_ref
            and data.get('target_topic_id') == authority['topic_id'])
    else:
        valid = (record.get('run_kind') == 'phase-run' and data.get('wrapper_integration') is True
            and data.get('carrier_kind') == 'dedicated-grilling' and data.get('source_topic_id') == authority['topic_id']
            and data.get('from_phase') == authority['source_phase'] and data.get('to_phase') == authority['stage'])
    attempts = [a for a in data['attempts'] if a['attempt_id'] == authority['attempt_id']]
    if not valid or len(attempts) != 1:
        raise ProtocolError('handoff_identity_conflict', 'entry authority no longer matches its route')
    return record, data, attempts[0]


def _store_entry(record, data):
    data['record_revision'] += 1
    record.update(state=data['state'], record_revision=data['record_revision'], data_json=_canonical_json(data))


def _bounded_workflow_control(request):
    import workflow_state_store as _store
    schema_request = {**request, 'scope_checkpoint_path': request.get('scope_checkpoint_path'),
                      'expected_control_ref': request.get('expected_control_ref')}
    ledger_path, lock_path, project, owner_ref = _handoff_mutation_context(
        schema_request, {'action', 'evidence', 'scope_checkpoint_path', 'expected_control_ref'})
    with lock_path.open('a+b') as stream:
        _flock_with_timeout(stream)
        frontmatter, records = _load_records(ledger_path)
        matches = [r for r in records['Phase Results'] if r.get('result_kind') == 'workflow-control'
                   and r.get('topic_id') == request['actor_topic_id']]
        if len(matches) > 1:
            raise ProtocolError('state_corrupt', 'multiple workflow control checkpoints')
        selector = None
        evidence = request.get('evidence') or {}
        if request['action'] in {'archive', 'archive-result'} and evidence.get('handoff_id'):
            selector = {'handoff_id': evidence['handoff_id']}
        elif request['action'] == 'successor-ready' and evidence.get('input_digest'):
            selector = {'delivery_digest': evidence['input_digest']}
        context, pointer = read_control_projection(ledger_path, matches[0], selector=selector) if matches else (None, None)
        store = _store.open_store(ledger_path)
        measurement = store._review_measurement
        if store._measured_writes is not None:
            if not (measurement is not None and measurement['request'] == request and
                    measurement['pointer'] == pointer and context['stage'] == 3 and
                    context['handoff_progress']['binding'] == measurement['binding']):
                raise ProtocolError('invalid_request', 'measurement is limited to its original bound review-start')
        if pointer is not None:
            # The lock and actual ledger record establish this commit, not object existence.
            store.certify_ledger_control(pointer)
        replay = _idempotent_result(records, request)
        if replay is None:
            replay = store.read_ledger_receipt(request['actor_topic_id'], request['idempotency_key'],
                                               _mutation_fingerprint(request))
        if replay is not None:
            return public_control_result(replay, ledger_path)
        topic = _record_by_id(records['Current Topics'], 'topic_id', request['actor_topic_id'], 'topic_id')
        revision, topic_revision = _validate_revisions(request, frontmatter, topic)
        _verify_topic_owner(records, request['actor_topic_id'], owner_ref)
        if request.get('expected_control_ref') is not None and request['expected_control_ref'] != pointer:
            raise ProtocolError('control_revision_conflict', 'runner projection is not the current ledger authority')
        action = request['action']
        if not isinstance(request['evidence'], dict):
            raise ProtocolError('invalid_request', 'control evidence must be an object')
        evidence = dict(request['evidence'])
        if action in {'prepare', 'decide', 'reserve-launch', 'standalone-entry', 'choose-dedicated'}:
            apply_gate_policy(records, 'prepare-handoff', request['actor_topic_id'])
        topic_path = project / topic['topic_document_path']
        identity = {'path': topic_path.relative_to(project).as_posix(),
                    'sha256': _sha256(_require_regular_nosymlink(topic_path, 'topic document')),
                    'version': topic_revision}
        if matches:
            record = matches[0]
            if context['controller_ref'] != owner_ref:
                raise ProtocolError('document_ownership_conflict', 'controller identity changed')
        else:
            record = {'result_id': 'WC-' + request['actor_topic_id'], 'result_kind': 'workflow-control',
                      'topic_id': request['actor_topic_id'], 'record_revision': 0, 'state': 'active'}
            records['Phase Results'].append(record)
            context = {'schema_version': 6, 'controller_ref': owner_ref,
                       'topic_ref': request['actor_topic_id'], 'stage': topic['current_phase'],
                       'carrier': None, 'preference': {'topic_current': False, 'stage_current': False},
                       'flow_authority': None, 'requirement_identity': identity, 'handoff_progress': None}
        try:
            if action == 'prepare':
                authority = _entry_candidates(records, topic, request, owner_ref, identity)
                if 'entry_authority' in evidence and evidence['entry_authority'] != authority:
                    raise ProtocolError('handoff_identity_conflict', 'supplied entry authority differs from ledger')
                evidence['entry_authority'] = authority
                if context['carrier'] is None:
                    if context['stage'] != authority['stage']:
                        context['stage'] = authority['stage']
                        context['preference']['stage_current'] = False
                        context['handoff_progress'] = None
                    context['requirement_identity'] = identity
                selected = context
            elif action in {'archive', 'archive-result'}:
                selected = context
            elif action == 'successor-ready' and any(r['delivery_digest'] == evidence.get('input_digest') for r in context.get('retired_handoffs', {}).values()):
                retired = next(r for r in context['retired_handoffs'].values() if r['delivery_digest'] == evidence['input_digest'])
                selected = {**retired, 'handoff_progress': {'plan': retired['plan']}}
            else:
                selected = _control.selected_control(context, evidence)
            progress = selected['handoff_progress'] or {}
            if action == 'decide' and progress.get('state') == 'prepared':
                selected['requirement_identity'] = identity
            authority = progress.get('plan', {}).get('entry_authority')
            entry = _entry_attempt(records, authority, request, owner_ref) if authority else None
            receipt = {k: v for k, v in evidence.items() if k != 'control_plan_id'}
            if action == 'receive' and progress.get('delivery'):
                receipt['verified_commit_hash'] = progress['delivery']['verified_commit_hash']
            if action == 'receive' and progress.get('delivery') == receipt:
                return public_control_result({'ok': True, 'state': 'acknowledged', 'ledger_revision': revision,
                    'record_revision': topic_revision, 'control': {'ok': True, 'store_ref': pointer,
                    'effects': [], 'acknowledged': True, 'delivery_digest': progress['delivery_digest']}}, ledger_path)
            if action == 'decide' and entry:
                _, data, attempt = entry
                eligible = attempt.get('binding_eligible') if authority['kind'] == 'dedicated-stage' else attempt['state'] in {'setup-pending', 'ready', 'active'}
                if not eligible or data.get('source_topic_id') != topic['topic_id'] or topic['current_phase'] != authority['source_phase']:
                    raise ProtocolError('handoff_identity_conflict', 'entry authority is no longer eligible')
            if action == 'receive' or action == 'creation-result' and evidence.get('status') == 'ready':
                if entry is None:
                    raise ProtocolError('handoff_identity_conflict', 'control result has no frozen authority')
                _, data, attempt = entry
                source = evidence.get('source_ref', evidence.get('ref'))
                if authority['kind'] == 'dedicated-stage':
                    valid = attempt.get('binding_eligible') and attempt.get('conversation_ref') == source
                    valid = valid and attempt['state'] in ({'active'} if action == 'receive' else {'bound-pending-acceptance', 'active'})
                else:
                    valid = attempt.get('authorization') is True and attempt.get('carrier_ref') == source
                    valid = valid and attempt['state'] in ({'completion-claimed', 'completion-pending', 'completed'} if action == 'receive' else {'setup-pending', 'ready', 'active'})
                    if action == 'receive':
                        valid = valid and attempt.get('output_evidence', {}).get('source') == identity['sha256']
                if not valid:
                    raise ProtocolError('handoff_identity_conflict', 'result does not match the exact authorized carrier')
            if action == 'successor-ready':
                if entry is None:
                    raise ProtocolError('handoff_identity_conflict', 'takeover has no original entry authority')
                _, old_data, old_attempt = entry
                if authority['kind'] == 'dedicated-stage' and not (
                        old_attempt.get('delivery_frozen') and old_attempt.get('delivery_accepted') and
                        (evidence.get('stage') != 1 or old_attempt.get('binding_eligible') is False)):
                    raise ProtocolError('phase_authorization_required', 'old dedicated delivery must be frozen, accepted and revoked before a writable successor')
                if authority['kind'] == 'wrapper-phase-run' and not (
                        old_attempt.get('state') == 'completed' and old_data.get('state') == 'completed'):
                    raise ProtocolError('phase_authorization_required', 'old wrapper must be completed without live writing authority')
                matching = []
                for item in records['Phase Runs']:
                    if item.get('run_kind') != 'phase-run':
                        continue
                    phase = _json_field(item, 'data_json', 'phase run')
                    if phase.get('source_topic_id') == topic['topic_id'] and phase.get('from_phase') == selected['stage'] and phase.get('to_phase') == evidence.get('stage') and phase.get('state') in {'active', 'completed'}:
                        matching.extend(a for a in phase['attempts'] if a.get('carrier_ref') == evidence.get('ref') and a.get('authorization') is True and a.get('state') in {'active', 'completed'} and (not phase.get('wrapper_integration') or a.get('claimed') is True))
                if len(matching) != 1:
                    raise ProtocolError('phase_identity_conflict', 'successor has not authentically taken over the source')
                successor = context.get('successor_control')
                if successor is not None and (successor['carrier'] or {}).get('ref') != evidence.get('ref'):
                    raise ProtocolError('phase_identity_conflict', 'successor differs from the frozen control slot')
                evidence['activated'] = True
            if action == 'receive':
                if _active_pending_write(records) is not None:
                    raise ProtocolError('document_write_reconciliation_required', 'complete the pending document write before delivery')
                apply_gate_policy(records, 'dedicated-handoff', topic['topic_id'])
                if evidence.get('requirement_identity') != identity:
                    raise ProtocolError('phase_source_drift', 'delivery version differs from current topic document')
                if evidence['commit'] is None:
                    probe = _git.subprocess.run(['git', '-C', str(project), 'rev-parse', '--show-toplevel'], capture_output=True)
                    if selected['stage'] != 0 or probe.returncode == 0:
                        raise ProtocolError('invalid_request', 'snapshot-only delivery requires non-Git Stage 0')
                    evidence['verified_commit_hash'] = identity['sha256']
                else:
                    _git.verify_delivery(project, evidence['commit'], evidence)
            transition_request = {'schema_version': context['schema_version'], 'actor_ref': owner_ref,
                                  'context': context, 'action': action, 'evidence': evidence}
            if action in GIT_BACKED_ACTIONS:
                # The ledger lock is the original control transaction boundary.
                # Resolve the Flow from the persisted control slot once launched;
                # start actions use the just-verified B binding in their evidence.
                bound = selected.get('handoff_progress') or {}
                binding = bound.get('binding') or evidence.get('binding')
                if not isinstance(binding, dict) or not isinstance(binding.get('worktree'), str):
                    raise ProtocolError('invalid_request', 'attached Git action has no bound Flow')
                if action in {'start-design', 'start-dispatch', 'start-closure'}:
                    repository, baseline = _start_git_authority(request, context, evidence, action)
                else:
                    repository = binding['repository'] if action == 'closure-result' else binding['worktree']
                    if not isinstance(bound.get('git_baseline_commit'), str):
                        raise ProtocolError('invalid_request', 'attached Git action has no frozen control baseline')
                    baseline = bound['git_baseline_commit']
                control = _git.verified_transition({
                    'repository': repository, 'baseline': baseline,
                    'request': transition_request})
            else:
                control = _control.transition(transition_request)
        except _control.ControlError as error:
            raise ProtocolError('invalid_request', str(error)) from error
        if action == 'standalone-entry':
            return {'ok': True, 'state': 'standalone-eligible', 'ledger_revision': revision,
                    'record_revision': topic_revision, 'control': control}
        if action in {'cancel', 'receive', 'accept'} and entry is not None:
            item, data, attempt = entry
            if action == 'cancel':
                if authority['kind'] == 'dedicated-stage':
                    attempt.update(state='cancelled', binding_eligible=False)
                    for binding in records['Conversation Bindings']:
                        if binding.get('handoff_id') == authority['run_id'] and binding.get('attempt_id') == authority['attempt_id']:
                            binding['binding_state'] = 'superseded'
                else:
                    attempt.update(state='cancelled', authorization=False)
                if attempt is data['attempts'][-1]:
                    data['state'] = 'cancelled'
            elif authority['kind'] == 'dedicated-stage':
                attempt['delivery_frozen'] = True
                if action == 'accept':
                    attempt['delivery_accepted'] = True
            _store_entry(item, data)
        record['record_revision'] += 1
        compact = {key: value for key, value in control.items() if key != 'context'}
        result = {'ok': True, 'state': 'recorded', 'ledger_revision': revision + 1,
                  'record_revision': topic_revision, 'control': compact}
        pointer = store.prepare_ledger_control(control['context'], topic_id=topic['topic_id'],
            ledger_revision=revision + 1, control_revision=record['record_revision'], previous=pointer,
            receipt={'key': request['idempotency_key'], 'fingerprint': _mutation_fingerprint(request), 'result': result})
        record['data_json'] = _canonical_json(pointer)
        result = {**result, 'control': {**compact, 'store_ref': pointer}}
        if measurement is None:
            _write_ledger_transaction(ledger_path, frontmatter, records, request,
                ledger_revision=revision + 1, event_type='workflow-control', result=result)
        # Only the internal, exact bound review-start overlay returns virtual
        # objects. No public request can select it or obtain a committed ACK.
        store.certify_ledger_control(pointer)
        return public_control_result(result, ledger_path)


def workflow_control(request):
    import workflow_state_store as _store
    try:
        return _bounded_workflow_control(request)
    except _store.entry.PreparationError as error:
        raise ProtocolError(error.code, str(error)) from error
