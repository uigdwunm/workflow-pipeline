"""Persist controller preferences/checkpoints in the existing discussion ledger."""
from pathlib import Path
import importlib.util

from .state import (ProtocolError, _canonical_json, _flock_with_timeout,
                    _idempotent_result, _json_field, _load_records, _record_by_id,
                    _sha256, _require_regular_nosymlink, _validate_revisions,
                    _verify_topic_owner, _write_ledger_transaction)
from .handoffs import _handoff_mutation_context, _handoff_data, _store_handoff
from .topic_dependencies import apply_gate_policy

_CONTROL_PATH = Path(__file__).resolve().parents[3] / 'guided-implementation/scripts/workflow_control.py'
_spec = importlib.util.spec_from_file_location('workflow_control_contract', _CONTROL_PATH)
_control = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_control)

_git_spec = importlib.util.spec_from_file_location('workflow_control_git', _CONTROL_PATH.with_name('workflow_control_git.py'))
_git = importlib.util.module_from_spec(_git_spec)
# The adapter imports its pure sibling; this is one shared contract module.
import sys
sys.modules.setdefault('workflow_control', _control)
_git_spec.loader.exec_module(_git)


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
        if request['action'] in {'prepare', 'decide', 'standalone-entry', 'choose-dedicated'}:
            apply_gate_policy(records, 'prepare-handoff', request['actor_topic_id'])
        matches = [r for r in records['Phase Results'] if r.get('result_kind') == 'workflow-control'
                   and r.get('topic_id') == request['actor_topic_id']]
        if len(matches) > 1:
            raise ProtocolError('state_corrupt', 'multiple workflow control checkpoints')
        topic_path = Path(topic['topic_document_path'])
        if not topic_path.is_absolute():
            topic_path = project / topic_path
        identity = {'path': topic_path.relative_to(project).as_posix(),
                    'sha256': _sha256(_require_regular_nosymlink(topic_path, 'topic document')),
                    'version': topic_revision}
        if matches:
            record = matches[0]
            context = _json_field(record, 'data_json', 'workflow control')
            if context['controller_ref'] != owner_ref:
                raise ProtocolError('document_ownership_conflict', 'controller identity changed')
            accepted_progress = context['handoff_progress'] or {}
            if request['action'] == 'receive' and accepted_progress.get('delivery') == request['evidence']:
                # Receipt is already authenticated and durable. Later phase/document
                # revisions do not turn an identical receipt into a new delivery.
                return {'ok': True, 'state': 'acknowledged', 'ledger_revision': revision,
                        'record_revision': topic_revision, 'control': {
                            'ok': True, 'context': context, 'effects': [], 'acknowledged': True,
                            'delivery_digest': accepted_progress['delivery_digest']}}
            pending_handoff = context['handoff_progress'] and context['handoff_progress'].get('state') in {'result-received', 'result-accepted', 'successor-ready', 'archive-pending'}
            if context['stage'] != topic['current_phase'] and not pending_handoff:
                context['stage'] = topic['current_phase']
                context['preference']['stage_current'] = False
                context['carrier'] = None
                context['handoff_progress'] = None
            if context['handoff_progress'] is None:
                context['requirement_identity'] = identity
        else:
            record = {'result_id': 'WC-' + request['actor_topic_id'], 'result_kind': 'workflow-control',
                      'topic_id': request['actor_topic_id'], 'record_revision': 0, 'state': 'active'}
            records['Phase Results'].append(record)
            context = {'schema_version': 1, 'controller_ref': owner_ref,
                       'topic_ref': request['actor_topic_id'], 'stage': topic['current_phase'],
                       'carrier': None, 'preference': {'topic_current': False, 'stage_current': False},
                       'flow_authority': None, 'requirement_identity': identity, 'handoff_progress': None}
        evidence = dict(request['evidence'])
        if request['action'] == 'receive' or request['action'] == 'creation-result' and evidence.get('status') == 'ready':
            source_ref = evidence.get('source_ref', evidence.get('ref'))
            carriers = []
            for item in records['Phase Runs']:
                if item.get('run_kind') != 'discussion-handoff':
                    continue
                handoff = _handoff_data(item)
                if handoff.get('kind') == 'dedicated-stage' and handoff.get('source_topic_id') == request['actor_topic_id'] and handoff.get('stage') == context['stage'] and handoff.get('controller_ref') == owner_ref:
                    carriers.extend(a for a in handoff['attempts'] if a.get('conversation_ref') == source_ref and a.get('binding_eligible') and a.get('state') in {'bound-pending-acceptance', 'accepted-awaiting-next-turn', 'active'})
            if len(carriers) != 1:
                raise ProtocolError('handoff_identity_conflict', 'control result has no unique authenticated dedicated binding')
        if request['action'] == 'successor-ready':
            matching = []
            for item in records['Phase Runs']:
                if item.get('run_kind') != 'phase-run':
                    continue
                phase = _json_field(item, 'data_json', 'phase run')
                if phase.get('source_topic_id') == request['actor_topic_id'] and phase.get('from_phase') == context['stage'] and phase.get('to_phase') == evidence.get('stage') and phase.get('state') in {'active', 'completed'}:
                    matching.extend(a for a in phase['attempts'] if a.get('carrier_ref') == evidence.get('ref') and a.get('state') in {'active', 'completed'} and (not phase.get('wrapper_integration') or a.get('claimed') is True))
            if len(matching) != 1:
                raise ProtocolError('phase_identity_conflict', 'successor has not authentically taken over the source')
            evidence['activated'] = True
        try:
            if request['action'] == 'receive':
                if evidence.get('requirement_identity') != identity:
                    raise ProtocolError('phase_source_drift', 'delivery version differs from current topic document')
                _git.verify_delivery(project, evidence['commit'], evidence)
            control = _control.transition({'schema_version': 1, 'actor_ref': owner_ref,
                'context': context, 'action': request['action'], 'evidence': evidence})
        except _control.ControlError as error:
            raise ProtocolError('invalid_request', str(error)) from error
        if request['action'] == 'standalone-entry':
            return {'ok': True, 'state': 'standalone-eligible', 'ledger_revision': revision,
                    'record_revision': topic_revision, 'control': control}
        if request['action'] == 'cancel':
            for item in records['Phase Runs']:
                if item.get('run_kind') != 'discussion-handoff':
                    continue
                handoff = _handoff_data(item)
                if handoff.get('kind') == 'dedicated-stage' and handoff.get('source_topic_id') == request['actor_topic_id'] and handoff.get('stage') == context['stage']:
                    for attempt in handoff['attempts']:
                        if attempt.get('binding_eligible'):
                            attempt.update(state='cancelled', binding_eligible=False, reason='Controller cancelled dedicated attempt')
                    handoff['state'] = 'cancelled'
                    handoff['record_revision'] += 1
                    _store_handoff(item, handoff)
                    for binding in records['Conversation Bindings']:
                        if binding.get('handoff_id') == handoff['handoff_id']:
                            binding['binding_state'] = 'superseded'
        record['data_json'] = _canonical_json(control['context'])
        record['record_revision'] += 1
        result = {'ok': True, 'state': 'recorded', 'ledger_revision': revision + 1,
                  'record_revision': topic_revision, 'control': control}
        _write_ledger_transaction(ledger_path, frontmatter, records, request,
            ledger_revision=revision + 1, event_type='workflow-control', result=result)
        return result
