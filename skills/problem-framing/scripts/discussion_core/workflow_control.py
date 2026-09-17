"""Persist controller preferences/checkpoints in the existing discussion ledger."""
from pathlib import Path

from .state import (ProtocolError, _active_pending_write, _canonical_json, _flock_with_timeout,
                    _idempotent_result, _json_field, _load_records, _record_by_id,
                    _sha256, _require_regular_nosymlink, _validate_revisions,
                    _verify_topic_owner, _write_ledger_transaction)
from .handoffs import _handoff_mutation_context, _handoff_data, _store_handoff
from .topic_dependencies import apply_gate_policy

import workflow_control as _control
import workflow_control_git as _git


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
        if action in {'prepare', 'decide', 'standalone-entry', 'choose-dedicated'}:
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
            context = _json_field(record, 'data_json', 'workflow control')
            if context['controller_ref'] != owner_ref:
                raise ProtocolError('document_ownership_conflict', 'controller identity changed')
        else:
            record = {'result_id': 'WC-' + request['actor_topic_id'], 'result_kind': 'workflow-control',
                      'topic_id': request['actor_topic_id'], 'record_revision': 0, 'state': 'active'}
            records['Phase Results'].append(record)
            context = {'schema_version': 1, 'controller_ref': owner_ref,
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
                apply_gate_policy(records, 'authorize-handoff-discussion', topic['topic_id'])
                if evidence.get('requirement_identity') != identity:
                    raise ProtocolError('phase_source_drift', 'delivery version differs from current topic document')
                _git.verify_delivery(project, evidence['commit'], evidence)
            control = _control.transition({'schema_version': 1, 'actor_ref': owner_ref,
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
