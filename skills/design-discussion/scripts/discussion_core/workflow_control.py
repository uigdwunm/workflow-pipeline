"""Persist controller preferences/checkpoints in the existing discussion ledger."""
from pathlib import Path
import importlib.util

from .state import (ProtocolError, _canonical_json, _flock_with_timeout,
                    _idempotent_result, _json_field, _load_records, _record_by_id,
                    _sha256, _require_regular_nosymlink, _validate_revisions,
                    _verify_topic_owner, _write_ledger_transaction)
from .handoffs import _handoff_mutation_context
from .topic_dependencies import apply_gate_policy

_CONTROL_PATH = Path(__file__).resolve().parents[3] / 'guided-implementation/scripts/workflow_control.py'
_spec = importlib.util.spec_from_file_location('workflow_control_contract', _CONTROL_PATH)
_control = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_control)


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
        if request['action'] in {'prepare', 'decide', 'standalone-entry'}:
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
            if context['stage'] != topic['current_phase']:
                context['stage'] = topic['current_phase']
                context['preference']['stage_current'] = False
                context['carrier'] = None
                context['handoff_progress'] = None
            context['requirement_identity'] = identity
        else:
            record = {'result_id': 'WC-' + request['actor_topic_id'], 'result_kind': 'workflow-control',
                      'topic_id': request['actor_topic_id'], 'record_revision': 0, 'state': 'active'}
            records['Phase Results'].append(record)
            context = {'schema_version': 1, 'controller_ref': owner_ref,
                       'topic_ref': request['actor_topic_id'], 'stage': topic['current_phase'],
                       'carrier': None, 'preference': {'topic_current': False, 'stage_current': False},
                       'flow_authority': None, 'requirement_identity': identity, 'handoff_progress': None}
        try:
            control = _control.transition({'schema_version': 1, 'actor_ref': owner_ref,
                'context': context, 'action': request['action'], 'evidence': request['evidence']})
        except _control.ControlError as error:
            raise ProtocolError('invalid_request', str(error)) from error
        if request['action'] == 'standalone-entry':
            return {'ok': True, 'state': 'standalone-eligible', 'ledger_revision': revision,
                    'record_revision': topic_revision, 'control': control}
        record['data_json'] = _canonical_json(control['context'])
        record['record_revision'] += 1
        result = {'ok': True, 'state': 'recorded', 'ledger_revision': revision + 1,
                  'record_revision': topic_revision, 'control': control}
        _write_ledger_transaction(ledger_path, frontmatter, records, request,
            ledger_revision=revision + 1, event_type='workflow-control', result=result)
        return result
