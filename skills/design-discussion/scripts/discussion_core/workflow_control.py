"""Persist controller preferences/checkpoints in the existing discussion ledger."""
import copy
import re
from pathlib import Path

from .state import (ProtocolError, _active_pending_write, _canonical_json, _flock_with_timeout,
                    _idempotent_result, _json_field, _load_records, _record_by_id,
                    _sha256, _require_regular_nosymlink, _validate_revisions,
                    _verify_topic_owner, _write_ledger_transaction)
from .handoffs import _handoff_mutation_context, _handoff_data, _store_handoff
from .phase_runs import _phase_attempt, _phase_data, _phase_record
from .topic_dependencies import apply_gate_policy

import workflow_control as _control
import workflow_control_git as _git


_LEGACY_RECOVERY_FIELDS = {'checkpoint_path', 'original_checkpoint_sha256',
                           'original_accepted_digest', 'recovery_id', 'controller_ref',
                           'old_control_sha256', 'new_control_context',
                           'takeover_transaction_id'}
_LEGACY_RECOVERY_ID_FIELDS = _LEGACY_RECOVERY_FIELDS - {'new_control_context', 'controller_ref'}
_SHA256 = re.compile(r'[0-9a-f]{64}\Z')


def _legacy_takeover_metadata(evidence, owner_ref):
    if not isinstance(evidence, dict) or set(evidence) != _LEGACY_RECOVERY_FIELDS:
        raise ProtocolError('invalid_request', 'legacy recovery requires its exact bounded evidence')
    path = evidence['checkpoint_path']
    if (not isinstance(path, str) or not Path(path).is_absolute() or
            str(Path(path).resolve()) != path):
        raise ProtocolError('invalid_request', 'legacy checkpoint path must be canonical and absolute')
    if evidence['controller_ref'] != owner_ref:
        raise ProtocolError('document_ownership_conflict', 'legacy recovery requires the original Controller')
    for field in ('original_checkpoint_sha256', 'original_accepted_digest', 'old_control_sha256'):
        if not isinstance(evidence[field], str) or _SHA256.fullmatch(evidence[field]) is None:
            raise ProtocolError('invalid_request', 'legacy recovery requires exact SHA-256 identities')
    for field in ('recovery_id', 'takeover_transaction_id'):
        if not isinstance(evidence[field], str) or not evidence[field] or len(evidence[field].encode()) > 256:
            raise ProtocolError('invalid_request', 'legacy recovery transaction identity is invalid')
    return {**{field: evidence[field] for field in _LEGACY_RECOVERY_ID_FIELDS}, 'state': 'pending'}


def _legacy_core_binding(value):
    fields = {'repository', 'worktree', 'git_common_dir', 'branch', 'target_branch', 'base_commit'}
    if not isinstance(value, dict) or not fields <= set(value):
        raise ProtocolError('invalid_request', 'legacy designer binding is incomplete')
    return {field: value[field] for field in fields}


def _legacy_context_projection(old, new, metadata):
    if not isinstance(old, dict) or old.get('schema_version') != 4:
        raise ProtocolError('legacy_run_requires_original_runtime', 'only control schema 4 may be taken over')
    if not isinstance(new, dict):
        raise ProtocolError('invalid_request', 'new current control context is required')
    progress = old.get('handoff_progress')
    carrier = old.get('carrier')
    if (old.get('stage') != 2 or not isinstance(progress, dict) or
            progress.get('state') != 'design-accepted' or
            not isinstance(progress.get('result_digest'), str) or
            not isinstance(carrier, dict) or carrier.get('kind') != 'solution-designer' or
            not isinstance(carrier.get('ref'), str) or not carrier['ref'] or
            old.get('successor_control') is not None):
        raise ProtocolError('phase_authorization_required', 'only accepted Stage-2 designer control may be taken over')
    if 'scope_seal' in progress or 'legacy_takeover' in old or progress.get('recovery') is not None:
        raise ProtocolError('phase_authorization_required', 'legacy designer control has another active authority')
    new_progress = new.get('handoff_progress')
    if not isinstance(new_progress, dict) or 'scope_seal' not in new_progress:
        raise ProtocolError('invalid_request', 'new scope seal is required for takeover')
    old_binding = _legacy_core_binding(progress.get('binding'))
    new_binding = new_progress.get('binding')
    if (not isinstance(new_binding, dict) or
            set(new_binding) != set(old_binding) | {'lifecycle'} or
            _legacy_core_binding(new_binding) != old_binding):
        raise ProtocolError('binding_changed', 'supplemental binding must retain the original Git identity')
    lifecycle = new_binding['lifecycle']
    if (not isinstance(lifecycle, dict) or
            lifecycle.get('disposition') != 'retain' or
            lifecycle.get('origin') != 'reused' or
            lifecycle.get('creation_receipt') is not None):
        raise ProtocolError('binding_changed', 'recovered workspace must use verified retained ownership')
    expected = copy.deepcopy(old)
    expected['schema_version'] = 5
    expected['handoff_progress']['binding'] = copy.deepcopy(new_binding)
    expected['handoff_progress']['scope_seal'] = copy.deepcopy(new_progress['scope_seal'])
    expected['legacy_takeover'] = metadata
    if new != expected:
        raise ProtocolError('control_context_changed', 'takeover changed fields outside the supplemental scope authority')
    try:
        _control.validate_context(new)
    except _control.ControlError as error:
        raise ProtocolError('invalid_request', 'new control context is invalid') from error


def _legacy_phase_guard(records, topic):
    if topic.get('current_phase') != 2 or topic.get('phase_state') != 'active':
        raise ProtocolError('phase_authorization_required', 'Topic must remain in completed Stage-2 Phase 2')
    if _active_pending_write(records) is not None:
        raise ProtocolError('document_write_reconciliation_required', 'finish the original document transaction before takeover')
    for record in records['Phase Runs']:
        if record.get('run_kind') != 'phase-run':
            continue
        phase = _json_field(record, 'data_json', 'phase run')
        if (phase.get('source_topic_id') == topic['topic_id'] and
                phase.get('from_phase') == 2 and phase.get('to_phase') == 3):
            raise ProtocolError('phase_authorization_required', 'a Stage-3 Phase Run intent already exists')


def _scope_recover_legacy(request, owner_ref, topic, identity, records, record,
                          revision, topic_revision, frontmatter, ledger_path):
    evidence = request['evidence']
    metadata = _legacy_takeover_metadata(evidence, owner_ref)
    _legacy_phase_guard(records, topic)
    raw = record.get('legacy_control_json', record.get('data_json'))
    if not isinstance(raw, str) or _sha256(raw.encode('utf-8')) != evidence['old_control_sha256']:
        raise ProtocolError('control_context_changed', 'old control bytes differ from the inspected record')
    old = _json_field({'data_json': raw}, 'data_json', 'legacy workflow control')
    current = _json_field(record, 'data_json', 'workflow control')
    if (old.get('controller_ref') != owner_ref or old.get('topic_ref') != topic['topic_id'] or
            old.get('requirement_identity') != identity):
        raise ProtocolError('handoff_identity_conflict', 'legacy control differs from the current Topic and requirement')
    if current.get('schema_version') == 4:
        if 'legacy_control_json' in record:
            raise ProtocolError('state_corrupt', 'unclaimed legacy control history already exists')
        proposed = evidence['new_control_context']
        _legacy_context_projection(old, proposed, metadata)
        record['legacy_control_json'] = raw
        record['data_json'] = _canonical_json(proposed)
        record['record_revision'] += 1
        result = {'ok': True, 'state': 'takeover-pending', 'ledger_revision': revision + 1,
                  'record_revision': topic_revision, 'control': {'ok': True, 'context': proposed,
                  'effects': []}, 'takeover_transaction_id': evidence['takeover_transaction_id']}
        _write_ledger_transaction(ledger_path, frontmatter, records, request,
            ledger_revision=revision + 1, event_type='scope-recover-legacy', result=result)
        return result
    if current.get('schema_version') != 5 or 'legacy_control_json' not in record:
        raise ProtocolError('legacy_run_requires_original_runtime', 'only the original legacy control can be taken over')
    saved = current.get('legacy_takeover')
    if not isinstance(saved, dict) or {k: v for k, v in saved.items() if k != 'state'} != {
            k: v for k, v in metadata.items() if k != 'state'}:
        raise ProtocolError('idempotency_conflict', 'another recovery owns this control checkpoint')
    proposed = evidence['new_control_context']
    if proposed == current:
        return {'ok': True, 'state': 'takeover-' + saved['state'], 'ledger_revision': revision,
                'record_revision': topic_revision, 'control': {'ok': True, 'context': current,
                'effects': [], 'acknowledged': True},
                'takeover_transaction_id': evidence['takeover_transaction_id']}
    pending_replay = copy.deepcopy(current)
    pending_replay['legacy_takeover']['state'] = 'pending'
    if saved.get('state') == 'active' and proposed == pending_replay:
        return {'ok': True, 'state': 'takeover-active', 'ledger_revision': revision,
                'record_revision': topic_revision, 'control': {'ok': True, 'context': current,
                'effects': [], 'acknowledged': True},
                'takeover_transaction_id': evidence['takeover_transaction_id']}
    activated = copy.deepcopy(current)
    activated['legacy_takeover']['state'] = 'active'
    if saved.get('state') != 'pending' or proposed != activated:
        raise ProtocolError('control_context_changed', 'recovery may only activate the exact pending control')
    try:
        _control.validate_context(activated)
    except _control.ControlError as error:
        raise ProtocolError('invalid_request', 'active control context is invalid') from error
    record['data_json'] = _canonical_json(activated)
    record['record_revision'] += 1
    result = {'ok': True, 'state': 'takeover-active', 'ledger_revision': revision + 1,
              'record_revision': topic_revision, 'control': {'ok': True, 'context': activated,
              'effects': []}, 'takeover_transaction_id': evidence['takeover_transaction_id']}
    _write_ledger_transaction(ledger_path, frontmatter, records, request,
        ledger_revision=revision + 1, event_type='scope-recover-legacy-activated', result=result)
    return result


def _scope_recover_legacy_supersede(request, owner_ref, topic, identity, records,
                                    record, revision, topic_revision, frontmatter, ledger_path):
    evidence = request['evidence']
    fields = _LEGACY_RECOVERY_FIELDS | {'previous_takeover_transaction_id',
                                         'previous_scope_seal_ref'}
    if not isinstance(evidence, dict) or set(evidence) != fields:
        raise ProtocolError('invalid_request', 'legacy supersede requires exact prior authority and new scope')
    metadata = _legacy_takeover_metadata({key: evidence[key] for key in _LEGACY_RECOVERY_FIELDS}, owner_ref)
    prior_transaction = evidence['previous_takeover_transaction_id']
    prior_seal = evidence['previous_scope_seal_ref']
    if (not isinstance(prior_transaction, str) or not prior_transaction or
            not isinstance(prior_seal, str) or not prior_seal):
        raise ProtocolError('invalid_request', 'previous takeover transaction and scope seal are required')
    fingerprint = _sha256(_canonical_json(evidence).encode('utf-8'))
    history_keys = sorted((key for key in record if re.fullmatch(r'legacy_supersede_[1-9][0-9]*_meta_json', key)),
                          key=lambda key: int(key.split('_')[2]))
    for index, key in enumerate(history_keys, 1):
        if key != f'legacy_supersede_{index}_meta_json':
            raise ProtocolError('state_corrupt', 'legacy supersede history is not contiguous')
        item = _json_field(record, key, 'legacy supersede history')
        raw = record.get(f'legacy_supersede_{index}_control_json')
        if not isinstance(raw, str) or _sha256(raw.encode('utf-8')) != item.get('previous_control_sha256'):
            raise ProtocolError('state_corrupt', 'prior current control bytes changed')
        if item.get('idempotency_key') == request['idempotency_key'] and item.get('request_fingerprint') != fingerprint:
            raise ProtocolError('idempotency_conflict', 'legacy supersede key was reused for another request')
        if item.get('request_fingerprint') == fingerprint:
            current = _json_field(record, 'data_json', 'workflow control')
            return {'ok': True, 'state': 'takeover-superseded', 'ledger_revision': revision,
                    'record_revision': topic_revision, 'acknowledged': True,
                    'control': {'ok': True, 'context': current, 'effects': [], 'acknowledged': True},
                    'takeover_transaction_id': evidence['takeover_transaction_id'],
                    'previous_takeover_transaction_id': prior_transaction}
    if len(history_keys) >= 32:
        raise ProtocolError('history_limit', 'legacy supersede history is full')
    _legacy_phase_guard(records, topic)
    raw_old = record.get('legacy_control_json')
    raw_current = record.get('data_json')
    if (not isinstance(raw_old, str) or not isinstance(raw_current, str) or
            _sha256(raw_old.encode('utf-8')) != evidence['old_control_sha256']):
        raise ProtocolError('control_context_changed', 'original control bytes differ from the frozen takeover')
    old = _json_field({'data_json': raw_old}, 'data_json', 'legacy workflow control')
    current = _json_field(record, 'data_json', 'workflow control')
    takeover = current.get('legacy_takeover')
    progress = current.get('handoff_progress')
    seal = progress.get('scope_seal') if isinstance(progress, dict) else None
    if (current.get('schema_version') != 5 or
            current.get('controller_ref') != owner_ref or current.get('topic_ref') != topic['topic_id'] or
            current.get('requirement_identity') != identity or
            old.get('controller_ref') != owner_ref or old.get('topic_ref') != topic['topic_id'] or
            old.get('requirement_identity') != identity or
            not isinstance(takeover, dict) or takeover.get('state') not in {'pending', 'active'} or
            takeover.get('reservation') is not None or not isinstance(seal, dict) or
            takeover.get('takeover_transaction_id') != prior_transaction or
            seal.get('seal_ref') != prior_seal):
        raise ProtocolError('scope_conflict', 'previous unconsumed takeover is no longer current')
    for field in ('recovery_id', 'checkpoint_path', 'original_checkpoint_sha256',
                  'original_accepted_digest', 'old_control_sha256'):
        if takeover.get(field) != metadata[field]:
            raise ProtocolError('handoff_identity_conflict', 'supersede changed original recovery identity')
    if evidence['takeover_transaction_id'] == prior_transaction:
        raise ProtocolError('scope_conflict', 'supersede requires a new takeover transaction')
    proposed = evidence['new_control_context']
    _legacy_context_projection(old, proposed, metadata)
    if (proposed['handoff_progress']['binding'] != progress['binding'] or
            proposed['handoff_progress']['scope_seal']['seal_ref'] == prior_seal):
        raise ProtocolError('scope_conflict', 'supersede must retain binding and replace the scope seal')
    number = len(history_keys) + 1
    record[f'legacy_supersede_{number}_control_json'] = raw_current
    record[f'legacy_supersede_{number}_meta_json'] = _canonical_json({
        'idempotency_key': request['idempotency_key'], 'request_fingerprint': fingerprint,
        'previous_control_sha256': _sha256(raw_current.encode('utf-8')),
        'previous_takeover_transaction_id': prior_transaction,
        'previous_scope_seal_ref': prior_seal,
        'takeover_transaction_id': evidence['takeover_transaction_id']})
    record['data_json'] = _canonical_json(proposed)
    record['record_revision'] += 1
    result = {'ok': True, 'state': 'takeover-pending', 'ledger_revision': revision + 1,
              'record_revision': topic_revision, 'control': {'ok': True, 'context': proposed,
              'effects': []}, 'takeover_transaction_id': evidence['takeover_transaction_id'],
              'previous_takeover_transaction_id': prior_transaction}
    _write_ledger_transaction(ledger_path, frontmatter, records, request,
        ledger_revision=revision + 1, event_type='scope-recover-legacy-superseded', result=result)
    return result


def _scope_recover_legacy_reserve(request, owner_ref, topic, identity, records, record,
                                  context, revision, topic_revision, frontmatter, ledger_path):
    evidence = request['evidence']
    fields = {'recovery_id', 'checkpoint_path', 'original_checkpoint_sha256',
              'bridge_digest', 'phase_run_id', 'phase_attempt_id', 'controller_ref',
              'reservation_id'}
    if not isinstance(evidence, dict) or set(evidence) != fields:
        raise ProtocolError('invalid_request', 'legacy reservation requires exact bounded evidence')
    if evidence['controller_ref'] != owner_ref or context.get('controller_ref') != owner_ref:
        raise ProtocolError('document_ownership_conflict', 'original Controller must reserve the recovered launch')
    takeover = context.get('legacy_takeover')
    if (context.get('topic_ref') != topic['topic_id'] or
            not isinstance(takeover, dict) or takeover.get('state') != 'active' or
            not isinstance(record.get('legacy_control_json'), str) or
            _sha256(record['legacy_control_json'].encode('utf-8')) != takeover.get('old_control_sha256')):
        raise ProtocolError('phase_authorization_required', 'active original legacy takeover is required')
    for field in ('recovery_id', 'checkpoint_path', 'original_checkpoint_sha256'):
        if evidence[field] != takeover[field]:
            raise ProtocolError('handoff_identity_conflict', 'reservation differs from the active recovery')
    if (not isinstance(evidence['bridge_digest'], str) or
            _SHA256.fullmatch(evidence['bridge_digest']) is None):
        raise ProtocolError('invalid_request', 'supplemental bridge digest is invalid')
    for field in ('reservation_id', 'phase_run_id', 'phase_attempt_id'):
        value = evidence[field]
        if not isinstance(value, str) or not value or len(value.encode()) > 256:
            raise ProtocolError('invalid_request', 'legacy reservation identity is invalid')
    reservation = {field: evidence[field] for field in fields - {'controller_ref'}}
    previous = takeover.get('reservation')
    if previous is not None:
        if previous != reservation:
            raise ProtocolError('idempotency_conflict', 'another Stage-3 launch owns this recovery')
        return {'ok': True, 'state': 'launch-reserved', 'ledger_revision': revision,
                'record_revision': topic_revision, 'reservation': copy.deepcopy(previous),
                'control': {'ok': True, 'context': context, 'effects': [], 'acknowledged': True}}
    if context.get('stage') != 2 or context.get('requirement_identity') != identity:
        raise ProtocolError('phase_authorization_required', 'current Stage-2 requirement is required for new reservation')
    if topic.get('current_phase') != 2 or topic.get('phase_state') != 'active':
        raise ProtocolError('phase_authorization_required', 'Topic no longer owns Phase 2')
    if _active_pending_write(records) is not None:
        raise ProtocolError('document_write_reconciliation_required', 'finish the Topic document transaction before launch')
    phase_record = _phase_record(records, evidence['phase_run_id'])
    phase = _phase_data(phase_record)
    if (phase.get('source_topic_id') != topic['topic_id'] or phase.get('from_phase') != 2 or
            phase.get('to_phase') != 3 or phase.get('state') not in
                {'prepared', 'setup-pending', 'ready', 'active'}):
        raise ProtocolError('phase_identity_conflict', 'reservation requires a current 2-to-3 Phase Run')
    attempt = _phase_attempt(phase, evidence['phase_attempt_id'])
    if attempt.get('state') not in {'setup-pending', 'ready', 'active'}:
        raise ProtocolError('phase_identity_conflict', 'reservation attempt is no longer launchable')
    for other in records['Phase Runs']:
        if other.get('run_kind') != 'phase-run' or other.get('run_id') == phase['run_id']:
            continue
        value = _json_field(other, 'data_json', 'phase run')
        if (value.get('source_topic_id') == topic['topic_id'] and value.get('from_phase') == 2 and
                value.get('to_phase') == 3 and value.get('state') in
                {'prepared', 'setup-pending', 'ready', 'active', 'completion-claimed',
                 'completion-pending', 'outcome-unknown'}):
            raise ProtocolError('phase_identity_conflict', 'another Stage-3 Phase Run is active')
    new_context = copy.deepcopy(context)
    new_context['legacy_takeover']['reservation'] = copy.deepcopy(reservation)
    try:
        _control.validate_context(new_context)
    except _control.ControlError as error:
        raise ProtocolError('invalid_request', 'legacy launch reservation is invalid') from error
    record['data_json'] = _canonical_json(new_context)
    record['record_revision'] += 1
    result = {'ok': True, 'state': 'launch-reserved', 'ledger_revision': revision + 1,
              'record_revision': topic_revision, 'reservation': reservation,
              'control': {'ok': True, 'context': new_context, 'effects': []}}
    _write_ledger_transaction(ledger_path, frontmatter, records, request,
        ledger_revision=revision + 1, event_type='scope-recover-legacy-reserved', result=result)
    return result


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


def workflow_control(request):
    ledger_path, lock_path, project, owner_ref = _handoff_mutation_context(request, {'action', 'evidence'})
    with lock_path.open('a+b') as stream:
        _flock_with_timeout(stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic = _record_by_id(records['Current Topics'], 'topic_id', request['actor_topic_id'], 'topic_id')
        revision, topic_revision = _validate_revisions(request, frontmatter, topic)
        _verify_topic_owner(records, request['actor_topic_id'], owner_ref)
        action = request['action']
        if not isinstance(request['evidence'], dict):
            raise ProtocolError('invalid_request', 'control evidence must be an object')
        evidence = dict(request['evidence'])
        if action in {'prepare', 'decide', 'reserve-launch', 'standalone-entry', 'choose-dedicated'}:
            apply_gate_policy(records, 'prepare-handoff', request['actor_topic_id'])
        matches = [r for r in records['Phase Results'] if r.get('result_kind') == 'workflow-control'
                   and r.get('topic_id') == request['actor_topic_id']]
        if len(matches) > 1:
            raise ProtocolError('state_corrupt', 'multiple workflow control checkpoints')
        topic_path = project / topic['topic_document_path']
        identity = {'path': topic_path.relative_to(project).as_posix(),
                    'sha256': _sha256(_require_regular_nosymlink(topic_path, 'topic document')),
                    'version': topic_revision}
        if matches:
            record = matches[0]
            if action == 'scope-recover-legacy':
                return _scope_recover_legacy(request, owner_ref, topic, identity, records, record,
                    revision, topic_revision, frontmatter, ledger_path)
            if action == 'scope-recover-legacy-supersede':
                return _scope_recover_legacy_supersede(request, owner_ref, topic, identity,
                    records, record, revision, topic_revision, frontmatter, ledger_path)
            context = _json_field(record, 'data_json', 'workflow control')
            if context.get('schema_version') != 5:
                raise ProtocolError('legacy_run_requires_original_runtime', 'retain original package for embedded control schema ' + str(context.get('schema_version')))
            if context['controller_ref'] != owner_ref:
                raise ProtocolError('document_ownership_conflict', 'controller identity changed')
            if action == 'scope-recover-legacy-reserve':
                return _scope_recover_legacy_reserve(request, owner_ref, topic, identity,
                    records, record, context, revision, topic_revision, frontmatter, ledger_path)
        else:
            if action in {'scope-recover-legacy', 'scope-recover-legacy-reserve',
                          'scope-recover-legacy-supersede'}:
                raise ProtocolError('legacy_run_requires_original_runtime',
                    'legacy recovery requires the original workflow control checkpoint')
            record = {'result_id': 'WC-' + request['actor_topic_id'], 'result_kind': 'workflow-control',
                      'topic_id': request['actor_topic_id'], 'record_revision': 0, 'state': 'active'}
            records['Phase Results'].append(record)
            context = {'schema_version': 5, 'controller_ref': owner_ref,
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
                return {'ok': True, 'state': 'acknowledged', 'ledger_revision': revision,
                    'record_revision': topic_revision, 'control': {'ok': True, 'context': context,
                    'effects': [], 'acknowledged': True, 'delivery_digest': progress['delivery_digest']}}
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
            control = _control.transition({'schema_version': 5, 'actor_ref': owner_ref,
                'context': context, 'action': action, 'evidence': evidence})
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
        record['data_json'] = _canonical_json(control['context'])
        record['record_revision'] += 1
        result = {'ok': True, 'state': 'recorded', 'ledger_revision': revision + 1,
                  'record_revision': topic_revision, 'control': control}
        _write_ledger_transaction(ledger_path, frontmatter, records, request,
            ledger_revision=revision + 1, event_type='workflow-control', result=result)
        return result
