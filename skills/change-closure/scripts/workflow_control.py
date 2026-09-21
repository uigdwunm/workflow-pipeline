"""Pure, bounded workflow control plans. Callers own authenticated tool evidence.

No storage, task tools or Git operations occur here. The returned checkpoint is
persisted by its controller in the existing conversation or foreground runner.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import PurePosixPath
import re
import sys
from typing import Any

MAX_BYTES = 262144
CONTEXT_FIELDS = {'schema_version', 'controller_ref', 'topic_ref', 'stage', 'carrier',
                  'preference', 'flow_authority', 'requirement_identity', 'handoff_progress'}

class ControlError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ControlError(message)


def keys(value, expected):
    require(isinstance(value, dict) and set(value) == set(expected),
            'unexpected or missing fields: ' + ', '.join(sorted(expected)))


def decode_json(raw):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate JSON field: ' + key)
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique_pairs)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def path(value):
    require(isinstance(value, str) and value and '\\' not in value and
            not PurePosixPath(value).is_absolute() and
            all(part not in {'', '.', '..', '.git'} for part in value.split('/')),
            'path must be an exact relative file within the scope')
    return value


def text(value):
    require(isinstance(value, str) and bool(value.strip()) and len(value.encode()) <= 4096,
            'expected bounded nonempty identity or text')
    return value


def paths(value, empty=False):
    require(isinstance(value, list) and (empty or value) and len(value) <= 4096, 'bounded file list required')
    result = [path(p) for p in value]
    require(len(set(result)) == len(result), 'duplicate paths')
    return result


def hashes(value):
    require(isinstance(value, dict), 'file hashes must be an object')
    for p, h in value.items():
        path(p)
        require(h is None or isinstance(h, str) and re.fullmatch('[0-9a-f]{64}', h), 'invalid file hash')


def execution_record(progress, ref):
    text(ref)
    require(progress is not None and 'executions' in progress, 'no execution checkpoint')
    matches = [e for e in progress['executions'] if e['agent_ref'] == ref]
    require(len(matches) == 1, 'unknown execution identity')
    return matches[0]


def validate_validation_plan(plan, binding=None):
    keys(plan, {'review_required', 'final_required', 'environment_not_applicable'})
    seen = set()
    for group, categories in (('review_required', {'focused', 'affected'}), ('final_required', {'full', 'environment'})):
        require(isinstance(plan[group], list) and plan[group], 'nonempty required validation list needed')
        for item in plan[group]:
            keys(item, {'id', 'category', 'command', 'cwd', 'pass_condition', 'allowed_skips', 'environment'})
            identity = text(item['id'])
            require(identity not in seen, 'duplicate validation check id')
            seen.add(identity)
            require(text(item['category']) in categories, 'invalid required check category')
            for field in ('command', 'cwd', 'pass_condition'):
                text(item[field])
            require(item['pass_condition'] != 'accepted' or item['category'] == 'environment', 'procedure acceptance requires an environment check')
            require(PurePosixPath(item['cwd']).is_absolute(), 'validation cwd must be absolute')
            if binding is not None:
                require(item['cwd'] == binding['worktree'], 'validation cwd differs from bound worktree')
            require(isinstance(item['allowed_skips'], list) and all(isinstance(v, str) and v for v in item['allowed_skips']), 'invalid allowed suite skips')
            if item['category'] == 'environment':
                text(item['environment'])
            else:
                require(item['environment'] is None, 'environment identity only belongs to environment checks')
    require({v['category'] for v in plan['review_required']} == {'focused','affected'}, 'review plan must explicitly cover focused and affected purposes')
    require(any(v['category'] == 'full' for v in plan['final_required']), 'final validation requires a full check')
    if not any(v['category'] == 'environment' for v in plan['final_required']):
        text(plan['environment_not_applicable'])


def check_passed(result):
    """The frozen condition selects command success or explicit procedure acceptance."""
    return result['status'] == 'passed' and (result['exit_code'] == 0 or
        result['exit_code'] is None and result['category'] == 'environment' and result['pass_condition'] == 'accepted')


def validate_checks(required, checks, candidate, *, passed=True, complete=True):
    require(isinstance(checks, list), 'structured check results required')
    require((not complete or len(checks) == len(required)) and all(isinstance(v, dict) for v in checks), 'required checks must be covered exactly once')
    for result in checks:
        keys(result, {'id','category','command','cwd','pass_condition','allowed_skips','environment',
                      'status','exit_code','start_commit','end_commit','environment_fingerprint','output_ref','output_digest'})
        text(result['id'])
    require(len({v['id'] for v in checks}) == len(checks), 'duplicate check result')
    by_id = {v['id']: v for v in required}
    for result in checks:
        require(result.get('id') in by_id, 'unexpected check result')
        item = by_id[result['id']]
        keys(result, set(item) | {'status', 'exit_code', 'start_commit', 'end_commit', 'environment_fingerprint', 'output_ref', 'output_digest'})
        require(all(result[k] == v for k, v in item.items()), 'check differs from frozen validation plan')
        require(result['start_commit'] == result['end_commit'] == candidate, 'validation source changed')
        require(text(result['status']) in {'passed', 'failed', 'skipped', 'unknown'}, 'invalid check outcome')
        require(result['exit_code'] is None or type(result['exit_code']) is int, 'invalid check exit code')
        if passed:
            require(check_passed(result), 'required validation did not pass')
        if item['environment'] is not None:
            require(result['environment_fingerprint'] == item['environment'], 'wrong validation environment')
        text(result['output_ref'])
        require(isinstance(result['output_digest'], str) and re.fullmatch('[0-9a-f]{64}', result['output_digest']), 'raw output digest required')


def validate_reviewable(progress, candidate, verification):
    require(progress and progress['state'] in {'reviewable', 'reviewing'}, 'candidate is not reviewable')
    require(progress['candidate'] == candidate, 'review candidate changed')
    keys(verification, {'candidate', 'plan_digest', 'checks'})
    require(verification == {'candidate': candidate, 'plan_digest': progress['plan_digest'], 'checks': progress['tests']}, 'review verification differs from frozen candidate')
    validate_checks(progress['validation_plan']['review_required'], verification['checks'], candidate)


def validate_review_axes(candidate, target, plan_digest, review, dispatcher_ref):
    require(isinstance(candidate, str) and re.fullmatch('[0-9a-f]{40}', candidate), 'invalid reviewed candidate')
    keys(review, {'standards', 'spec'})
    reviewers = []
    for axis in ('standards', 'spec'):
        keys(review[axis], {'candidate', 'expected_target_head', 'plan_digest', 'reviewer_ref', 'status', 'result_ref'})
        require(review[axis]['candidate'] == candidate and review[axis]['status'] == 'accepted' and
                review[axis]['expected_target_head'] == target and review[axis]['plan_digest'] == plan_digest,
                'review does not accept exact candidate, target and plan')
        reviewers.append(text(review[axis]['reviewer_ref']))
        text(review[axis]['result_ref'])
    require(len(set(reviewers)) == 2 and dispatcher_ref not in reviewers, 'independent two-axis review required')


def validate_attempt_identities(attempts):
    require(isinstance(attempts,list) and all(isinstance(v,dict) for v in attempts), 'structured validation attempts required')
    identities = [text(value.get('attempt_id')) for value in attempts]
    require(len(set(identities)) == len(identities), 'duplicate validation attempt')


def validate_review(candidate, review, verification, dispatcher_ref):
    """Strict delivery projection shared by B, runner and closure."""
    keys(verification, {'candidate', 'expected_target_head', 'validation_plan', 'plan_digest', 'review_digest',
                        'review_decision', 'attempts', 'dispatcher_ref'})
    require(verification['candidate'] == candidate and verification['dispatcher_ref'] == dispatcher_ref, 'verification identity mismatch')
    plan = verification['validation_plan']
    validate_validation_plan(plan)
    require(verification['plan_digest'] == digest(plan), 'validation plan digest changed')
    validate_review_axes(candidate, verification['expected_target_head'], verification['plan_digest'], review, dispatcher_ref)
    decision = verification['review_decision']
    keys(decision, {'controller_ref', 'reference', 'review', 'native_evidence'})
    require(decision['review'] == review and verification['review_digest'] == digest(decision), 'review convergence mismatch')
    text(decision['controller_ref']); text(decision['reference'])
    keys(decision['native_evidence'], {'standards', 'spec'})
    for axis, item in decision['native_evidence'].items():
        keys(item, {'adapter','call_ref','response_ref','status','ref','raw'})
        for field in ('adapter','call_ref','response_ref'):
            text(item[field])
        require(isinstance(item, dict) and item.get('status') == 'stopped' and item.get('ref') == review[axis]['reviewer_ref'] and
                item.get('response_ref') == review[axis]['result_ref'] and isinstance(item.get('raw'), dict) and item['raw'],
                'stopped native semantic review evidence required')
    attempts = verification['attempts']
    require(isinstance(attempts, list) and attempts, 'final validation attempts missing')
    validate_attempt_identities(attempts)
    latest = attempts[-1]
    keys(latest, {'attempt_id','carrier_attempt','candidate','expected_target_head','plan_digest','review_digest','dispatcher_ref','source_snapshot','required','state','checks','source','result'})
    keys(latest['result'], {'attempt_id','checks','source','source_snapshot'})
    require(latest['result'] == {k:latest[k] for k in ('attempt_id','checks','source','source_snapshot')}, 'final result differs from original attempt observations')
    require(isinstance(latest['source_snapshot'],dict), 'original source snapshot required')
    require(latest['state'] == 'passed', 'latest final attempt did not pass')
    require(all(latest[k] == verification[k] for k in ('candidate', 'expected_target_head', 'plan_digest', 'review_digest', 'dispatcher_ref')), 'final attempt fixed point differs')
    require(latest['required'] == plan['final_required'], 'final required checks changed')
    validate_checks(plan['final_required'], latest['checks'], candidate)
    validate_result_source(latest['source'], latest['checks'], dispatcher_ref, latest['attempt_id'])


def validate_result_source(source, checks, dispatcher_ref, attempt_id=None):
    keys(source, {'adapter', 'call_ref', 'response_ref', 'raw'})
    for field in ('adapter', 'call_ref', 'response_ref'):
        text(source[field])
    raw = source['raw']
    if attempt_id is not None:
        require(isinstance(raw,dict) and raw.get('attempt_id') == attempt_id, 'command result belongs to another validation attempt')
    require(isinstance(raw, dict) and raw.get('checks') == checks and raw.get('source_unchanged') is True and
            raw.get('dispatcher_ref') == dispatcher_ref and raw.get('stopped') is True, 'trusted original stopped command observations required')


def delivery_verification(progress, dispatcher_ref):
    return {k: copy.deepcopy(progress[k]) for k in ('candidate', 'expected_target_head', 'validation_plan', 'plan_digest',
            'review_digest', 'review_decision', 'attempts')} | {'dispatcher_ref': dispatcher_ref}


def validate_deliverable(progress, candidate, review, verification, dispatcher_ref):
    require(progress and progress['state'] == 'deliverable', 'candidate is not deliverable')
    require(verification == delivery_verification(progress, dispatcher_ref) and review == progress['review_decision']['review'], 'delivery differs from control evidence')
    validate_review(candidate, review, verification, dispatcher_ref)


def validate_binding(binding):
    keys(binding, {'repository', 'worktree', 'git_common_dir', 'branch', 'target_branch', 'base_commit'})
    for field in ('repository', 'worktree', 'git_common_dir'):
        value = text(binding[field])
        require(PurePosixPath(value).is_absolute() and '..' not in PurePosixPath(value).parts, 'binding paths must be absolute and normalized')
    text(binding['branch']); text(binding['target_branch'])
    require(re.fullmatch('[0-9a-f]{40}', binding['base_commit']), 'invalid worktree base commit')


def validate_selection(value):
    keys(value, {'model', 'effort', 'capability', 'cost', 'permission', 'visible_identity', 'source', 'reason', 'receipt', 'needs_decision', 'disclose', 'upgrade_attempted'})
    for field in ('model', 'effort', 'permission', 'visible_identity', 'reason', 'receipt'):
        text(value[field])
    require(value['source'] in {'user', 'confirmed', 'inherited', 'role'}, 'invalid selection source')
    for field in ('needs_decision', 'disclose', 'upgrade_attempted'):
        require(type(value[field]) is bool, 'invalid selection flag')
    for field in ('capability', 'cost'):
        require(value[field] is None or type(value[field]) in {int, float} and math.isfinite(value[field]) and value[field] >= 0, 'invalid selection evidence')


def validate_entry_authority(authority):
    require(isinstance(authority, dict), 'entry authority must be an object')
    kind = authority.get('kind')
    if kind == 'standalone':
        keys(authority, {'kind'})
        return
    require(kind in {'dedicated-stage', 'wrapper-phase-run'}, 'invalid entry authority kind')
    keys(authority, {'kind', 'project_id', 'tree_id', 'topic_id', 'controller_ref',
                     'source_phase', 'stage', 'run_id', 'attempt_id'})
    for field in ('project_id', 'tree_id', 'topic_id', 'controller_ref', 'run_id', 'attempt_id'):
        text(authority[field])
    require(type(authority['source_phase']) is int and type(authority['stage']) is int,
            'entry phases must be integers')
    require((authority['source_phase'], authority['stage']) == (0, 1) if kind == 'wrapper-phase-run'
            else authority['source_phase'] == authority['stage'] and authority['stage'] in {0, 1},
            'entry authority route mismatch')


def validate_progress(progress):
    require(isinstance(progress, dict), 'invalid control checkpoint')
    state = progress.get('state')
    if 'configuration' in progress:
        validate_selection(progress['configuration'])
    if 'design_input' in progress:
        allowed = {'state', 'design_input', 'binding', 'allowed_paths', 'protected_paths', 'configuration', 'result_digest', 'git_baseline_commit'}
        require(state in {'designer-pending', 'designing', 'design-received', 'design-accepted', 'cancelled'}, 'invalid designer state')
        text(progress['design_input'])
        validate_binding(progress['binding'])
        paths(progress['allowed_paths']); paths(progress['protected_paths'], empty=True)
    elif 'executions' in progress:
        allowed = {'state', 'binding', 'allowed_paths', 'protected_paths', 'authority_digest', 'executions', 'candidate', 'tests', 'configuration', 'git_baseline_commit', 'validation_plan', 'plan_digest', 'expected_target_head', 'candidate_evidence', 'review_decision', 'review_digest', 'attempts', 'validation_history', 'accepted_delivery'}
        require(state in {'dispatcher-pending', 'implementing', 'reviewable', 'reviewing', 'final-validation-pending', 'validating', 'final-validation-failed', 'deliverable', 'cancelled'}, 'invalid dispatcher state')
        validate_binding(progress['binding'])
        paths(progress['allowed_paths']); paths(progress['protected_paths'], empty=True)
        validate_validation_plan(progress['validation_plan'], progress['binding'])
        require(progress['plan_digest'] == digest(progress['validation_plan']), 'frozen validation plan changed')
        if 'attempts' in progress:
            validate_attempt_identities(progress['attempts'])
        require(isinstance(progress['executions'], list), 'executions must be a list')
        for execution in progress['executions']:
            required = {'task_id', 'paths', 'read_only', 'behavior', 'tests', 'git_operations', 'configuration', 'agent_ref', 'state', 'stopped', 'file_hashes', 'allocation_digest'}
            require(isinstance(execution, dict) and required <= set(execution) <= required | {'git_snapshot', 'git_result_snapshot'}, 'invalid execution envelope fields')
            require(execution['state'] in {'dispatch-pending', 'assigned', 'received', 'accepted', 'cancelled'} and type(execution['stopped']) is bool, 'invalid execution state')
            validate_selection(execution['configuration'])
            text(execution['task_id']); text(execution['behavior']); text(execution['allocation_digest'])
            require(execution['agent_ref'] is None or isinstance(execution['agent_ref'], str) and execution['agent_ref'].strip(), 'invalid execution identity')
            require(execution['git_operations'] == [] and isinstance(execution['tests'], list) and execution['tests'], 'invalid execution constraints')
            if 'git_snapshot' in execution:
                keys(execution['git_snapshot'], {'head', 'branch', 'index_hash', 'files'})
                hashes(execution['git_snapshot']['files'])
                require(set(execution['git_snapshot']['files']) == set(progress['allowed_paths']), 'allocation snapshot must cover full implementation scope')
            if 'git_result_snapshot' in execution:
                hashes(execution['git_result_snapshot'])
                require(set(execution['git_result_snapshot']) == set(execution['file_hashes']), 'received snapshot paths mismatch')
            paths(execution['paths']); paths(execution['read_only'], empty=True); hashes(execution['file_hashes'])
    elif 'closure_paths' in progress:
        allowed = {'state', 'candidate', 'dispatcher_ref', 'review', 'verification', 'binding', 'implementation_paths', 'closure_paths', 'protected_paths', 'configuration', 'merge', 'problem', 'git_baseline_commit'}
        require(state in {'closure-pending', 'closing', 'cleanup-pending', 'completed', 'implementation-required', 'cancelled'}, 'invalid closure state')
        validate_binding(progress['binding'])
        validate_review(progress['candidate'], progress['review'], progress['verification'], progress['dispatcher_ref'])
    else:
        allowed = {'state', 'plan', 'decision', 'creation_status', 'delivery', 'delivery_digest', 'successor', 'archive_ref', 'archive_status'}
        require(state in {'prepared', 'creation-pending', 'current-task', 'cancelled', 'creation-failed', 'carrier-bound', 'result-received', 'result-accepted', 'successor-ready', 'archive-pending', 'archived'}, 'invalid dedicated state')
        plan = progress.get('plan')
        keys(plan, {'target', 'project', 'title', 'missing_context', 'configuration', 'next_step', 'archive_ref', 'gate_open', 'stage', 'controller_ref', 'topic_ref', 'requirement_identity', 'task_count', 'plan_id', 'entry_authority'})
        validate_entry_authority(plan['entry_authority'])
        validate_selection(plan['configuration'])
        require(type(plan['task_count']) is int and plan['task_count'] == 1 and plan['plan_id'] == digest({k: v for k, v in plan.items() if k != 'plan_id'}), 'prepared plan digest changed')
    if 'launch_input' in progress:
        text(progress['launch_input'])
    require(set(progress) <= allowed | {'launch_input'}, 'unknown checkpoint fields')


def validate_context(context):
    require(isinstance(context, dict) and CONTEXT_FIELDS <= set(context) <= CONTEXT_FIELDS | {'successor_control'}, 'invalid context fields')
    if context.get('successor_control') is not None:
        successor = context['successor_control']
        require(isinstance(successor, dict) and 'successor_control' not in successor, 'only one successor slot allowed')
        validate_context(successor)
        require(successor['controller_ref'] == context['controller_ref'] and successor['topic_ref'] == context['topic_ref'], 'successor controller/topic mismatch')
    require(type(context['schema_version']) is int and context['schema_version'] == 2, 'legacy_run_requires_original_runtime: control schema ' + str(context.get('schema_version')))
    text(context['controller_ref'])
    if context['topic_ref'] is not None:
        text(context['topic_ref'])
    require(type(context['stage']) is int and context['stage'] in range(5), 'invalid stage')
    require(context['topic_ref'] is None or isinstance(context['topic_ref'], str), 'invalid topic')
    keys(context['preference'], {'topic_current', 'stage_current'})
    require(all(type(v) is bool for v in context['preference'].values()), 'invalid preference')
    if context['carrier'] is not None:
        keys(context['carrier'], {'kind', 'ref', 'attempt'})
        require(context['carrier']['kind'] in {'dedicated-stage', 'solution-designer', 'implementation-dispatcher', 'closure-agent'}, 'invalid carrier kind')
        text(context['carrier']['attempt'])
        if context['carrier']['ref'] is not None:
            text(context['carrier']['ref'])
    if context['flow_authority'] is not None:
        keys(context['flow_authority'], {'source_phase', 'stages', 'scope_digest', 'controller_ref', 'topic_ref', 'checkpoint_id', 'checkpoint_hash', 'intent'})
        authority = context['flow_authority']
        require(type(authority['source_phase']) is int and authority['source_phase'] in {0, 1} and authority['stages'] == [2, 3, 4], 'invalid continuous route')
        for field in ('scope_digest', 'checkpoint_id', 'checkpoint_hash', 'intent'):
            text(authority[field])
        require(context['flow_authority']['controller_ref'] == context['controller_ref'] and context['flow_authority']['topic_ref'] == context['topic_ref'], 'continuous authority identity mismatch')
    if context['handoff_progress'] is not None:
        validate_progress(context['handoff_progress'])
    identity = context['requirement_identity']
    keys(identity, {'path', 'sha256', 'version'})
    path(identity['path'])
    require(isinstance(identity['sha256'], str) and re.fullmatch('[0-9a-f]{64}', identity['sha256']), 'invalid requirement hash')
    require(type(identity['version']) is int and identity['version'] > 0, 'invalid requirement version')


def select_configuration(evidence):
    keys(evidence, {'role', 'required_capability', 'supported', 'user', 'frozen', 'previous',
                    'receipt', 'can_override', 'inherited', 'upgrade_attempted'})
    require(evidence['role'] in {'dedicated-discussion', 'dedicated-problem-framing', 'solution-designer',
        'implementation-dispatcher', 'execution-agent', 'closure-agent', 'scripted-carrier'}, 'invalid role')
    text(evidence['receipt'])
    require(type(evidence['can_override']) is bool and type(evidence['upgrade_attempted']) is bool, 'invalid adapter capability flags')
    require(evidence['required_capability'] is None or type(evidence['required_capability']) in {int, float} and math.isfinite(evidence['required_capability']) and evidence['required_capability'] >= 0, 'invalid required capability')
    supported = evidence['supported']
    require(isinstance(supported, list) and 0 < len(supported) <= 128, 'no supported configurations')
    for item in supported:
        keys(item, {'model', 'effort', 'capability', 'cost', 'permission', 'visible_identity'})
        for field in ('model', 'effort', 'permission', 'visible_identity'):
            text(item[field])
        require(item['capability'] is None or type(item['capability']) in {int, float} and math.isfinite(item['capability']) and item['capability'] >= 0, 'unknown capability')
        require(item['cost'] is None or type(item['cost']) in {int, float} and math.isfinite(item['cost']) and item['cost'] >= 0, 'invalid cost evidence')
    require(len({(s['model'], s['effort']) for s in supported}) == len(supported), 'duplicate supported pair')
    def resolve(pair):
        if pair is None:
            return None
        keys(pair, {'model', 'effort'})
        return next((s for s in supported if all(s[k] == pair[k] for k in pair)), None)
    user, frozen = resolve(evidence['user']), resolve(evidence['frozen'])
    require(evidence['user'] is None or user is not None, 'explicit user configuration is unsupported')
    if evidence['can_override'] is False:
        selected = resolve(evidence['inherited'])
        require(selected is not None, 'actual inherited configuration unavailable')
        require(user is None or user == selected, 'adapter cannot express user override')
        source = 'inherited'
    elif user is not None:
        selected, source = user, 'user'
    elif frozen is not None:
        selected, source = frozen, 'confirmed'
    else:
        require(evidence['upgrade_attempted'] is False, 'capability upgrade already attempted')
        eligible = [s for s in supported if evidence['required_capability'] is not None and
                    s['capability'] is not None and s['capability'] >= evidence['required_capability']]
        known = [s for s in eligible if s['cost'] is not None]
        if known:
            selected = min(known, key=lambda s: (s['cost'], s['capability']))
            source = 'role'
        else:
            selected = resolve(evidence['inherited'])
            require(selected is not None, 'no comparable capability/cost evidence; preserve a supported choice or obtain a controller decision')
            source = 'inherited'
    previous = evidence['previous']
    if previous is not None:
        keys(previous, {'model', 'effort', 'capability', 'cost', 'permission', 'visible_identity'})
        for field in ('model', 'effort', 'permission', 'visible_identity'):
            text(previous[field])
        require(previous['cost'] is None or type(previous['cost']) in {int, float} and math.isfinite(previous['cost']) and previous['cost'] >= 0, 'invalid previous cost')
    changed = previous is not None and any(previous.get(k) != selected[k] for k in ('model', 'effort', 'permission', 'visible_identity'))
    needs_decision = changed and (previous.get('cost') is None or selected['cost'] is None or
        selected['cost'] > previous['cost'] or any(previous.get(k) != selected[k] for k in ('permission', 'visible_identity')))
    return {**selected, 'source': source, 'reason': 'current adapter evidence and role requirement',
            'receipt': evidence['receipt'], 'needs_decision': bool(needs_decision),
            'disclose': evidence['role'] != 'execution-agent', 'upgrade_attempted': source == 'role' and evidence['frozen'] is not None}


def _transition_single(request: dict[str, Any]) -> dict[str, Any]:
    keys(request, {'schema_version', 'action', 'actor_ref', 'context', 'evidence'})
    require(type(request['schema_version']) is int and request['schema_version'] == 2, 'legacy_run_requires_original_runtime: control request schema ' + str(request.get('schema_version')))
    context = copy.deepcopy(request['context'])
    validate_context(context)
    require(request['actor_ref'] == context['controller_ref'], 'only authenticated controller writes control checkpoints')
    action, evidence = request['action'], copy.deepcopy(request['evidence'])
    require(isinstance(action, str), 'action must be a string')
    require(isinstance(evidence, dict), 'evidence must be an object')
    if action in {'prepare', 'start-design', 'start-dispatch', 'start-closure', 'plan-execution'} and 'configuration' in evidence:
        expected_role = {'prepare': 'dedicated-discussion' if context['stage'] == 0 else 'dedicated-problem-framing',
                         'start-design': 'solution-designer', 'start-dispatch': 'implementation-dispatcher', 'start-closure': 'closure-agent', 'plan-execution': 'execution-agent'}[action]
        require(isinstance(evidence['configuration'], dict), 'configuration selection input must be an object')
        require(evidence['configuration'].get('role') == expected_role, 'configuration role mismatch')
        evidence['configuration'] = select_configuration(evidence['configuration'])
        require(not evidence['configuration']['needs_decision'], 'configuration requires controller decision')
    progress = context['handoff_progress']
    effects = []
    if action == 'select-configuration':
        return {'ok': True, 'context': context, 'effects': [], 'selection': select_configuration(evidence)}
    if action == 'prepare':
        keys(evidence, {'target', 'project', 'title', 'missing_context', 'configuration',
                        'next_step', 'archive_ref', 'gate_open'} | ({'entry_authority'} if 'entry_authority' in evidence else set()))
        authority = evidence.get('entry_authority', {'kind': 'standalone'})
        validate_entry_authority(authority)
        require(authority['kind'] != 'standalone' or context['topic_ref'] is None, 'attached plan requires exact entry authority')
        if authority['kind'] != 'standalone':
            require(authority['controller_ref'] == context['controller_ref'] and authority['topic_id'] == context['topic_ref'] and authority['stage'] == context['stage'], 'entry authority context mismatch')
        require(context['stage'] in {0, 1}, 'dedicated preparation requires stage 0 or 1')
        require(evidence['gate_open'] is True, 'topic gate is closed')
        for field in ('target', 'project', 'title', 'next_step'):
            text(evidence[field])
        require(isinstance(evidence['missing_context'], list) and all(isinstance(item, str) for item in evidence['missing_context']), 'missing context must be a text list')
        require(evidence['archive_ref'] is None or isinstance(evidence['archive_ref'], str), 'invalid archive identity')
        if context['carrier'] is not None or any(context['preference'].values()):
            return {'ok': True, 'context': context, 'plan': None, 'effects': []}
        plan = {**evidence, 'stage': context['stage'], 'controller_ref': context['controller_ref'],
                'topic_ref': context['topic_ref'], 'requirement_identity': context['requirement_identity'],
                'task_count': 1, 'entry_authority': authority}
        plan['plan_id'] = digest(plan)
        context['handoff_progress'] = {'state': 'prepared', 'plan': plan}
        return {'ok': True, 'context': context, 'plan': plan, 'effects': []}
    if action == 'decide':
        keys(evidence, {'plan_id', 'intent'})
        require(progress is not None and progress['plan']['plan_id'] == evidence['plan_id'], 'stale plan')
        require(progress['plan']['requirement_identity'] == context['requirement_identity'], 'requirement changed')
        intent = evidence['intent']
        require(intent in {'confirm', 'stage-current', 'topic-current', 'cancel'}, 'invalid decision')
        if progress['state'] != 'prepared':
            require(progress.get('decision') == intent, 'plan already decided')
        else:
            progress['decision'] = intent
            if intent == 'confirm':
                progress['state'] = 'creation-pending'
                context['carrier'] = {'kind': 'dedicated-stage', 'ref': None, 'attempt': evidence['plan_id']}
                effects = [{'operation': 'create_thread', 'plan': progress['plan'], 'attempt': evidence['plan_id']}]
            elif intent == 'cancel':
                progress['state'] = 'cancelled'
            else:
                context['preference']['topic_current' if intent == 'topic-current' else 'stage_current'] = True
                progress['state'] = 'current-task'
    elif action == 'reserve-launch':
        keys(evidence, {'attempt', 'input_digest'})
        require(progress and progress['state'] == 'creation-pending' and context['carrier']['ref'] is None and
                context['carrier']['attempt'] == evidence['attempt'], 'dedicated launch is not pending')
        text(evidence['input_digest'])
        if 'launch_input' in progress:
            require(progress['launch_input'] == evidence['input_digest'], 'reserved launch input changed')
            return {'ok': True, 'context': context, 'effects': [], 'acknowledged': True}
        progress['launch_input'] = evidence['input_digest']
    elif action == 'start-design':
        keys(evidence, {'design_input', 'binding', 'allowed_paths', 'protected_paths', 'configuration'})
        require(context['stage'] == 2 and progress is None and context['carrier'] is None, 'only one solution designer')
        validate_binding(evidence['binding'])
        text(evidence['design_input'])
        require(not set(paths(evidence['allowed_paths'])) & set(paths(evidence['protected_paths'], empty=True)), 'protected design scope overlap')
        attempt = digest(evidence)
        context['carrier'] = {'kind': 'solution-designer', 'ref': None, 'attempt': attempt}
        context['handoff_progress'] = {**evidence, 'state': 'designer-pending'}
        return {'ok': True, 'context': context, 'attempt': attempt,
                'effects': [{'operation': 'spawn_native', 'role': 'solution-designer', 'configuration': evidence['configuration']}]}
    elif action == 'designer-bound':
        keys(evidence, {'ref', 'attempt'})
        require(progress and progress['state'] == 'designer-pending' and evidence['attempt'] == context['carrier']['attempt'], 'wrong designer attempt')
        context['carrier']['ref'] = text(evidence['ref'])
        progress['state'] = 'designing'
    elif action in {'design-result', 'accept-design'}:
        keys(evidence, {'ref', 'result_digest'})
        require(progress and 'design_input' in progress and evidence['ref'] == context['carrier']['ref'], 'wrong designer identity')
        text(evidence['result_digest'])
        require(progress['state'] in ({'designing', 'design-received'} if action == 'design-result' else {'design-received', 'design-accepted'}), 'wrong designer result state')
        require('result_digest' not in progress or progress['result_digest'] == evidence['result_digest'], 'designer delivery cannot be replaced')
        progress.update(state='design-received' if action == 'design-result' else 'design-accepted', result_digest=evidence['result_digest'])
    elif action == 'native-dispatch-result':
        keys(evidence, {'attempt', 'status'})
        require(context['carrier'] is not None and context['carrier']['kind'] in {'solution-designer', 'implementation-dispatcher', 'closure-agent'} and
                context['carrier']['ref'] is None and context['carrier']['attempt'] == evidence['attempt'] and
                progress['state'] in {'designer-pending', 'dispatcher-pending', 'closure-pending'}, 'native attempt is no longer pending')
        require(evidence['status'] in {'unknown', 'not-created'}, 'invalid native creation outcome')
        if evidence['status'] == 'not-created':
            progress['state'] = 'cancelled'
        else:
            effects = [{'operation': 'read-native-state', 'attempt': evidence['attempt']}]
    elif action == 'start-closure':
        keys(evidence, {'candidate', 'dispatcher_ref', 'review', 'verification', 'binding',
                        'implementation_paths', 'closure_paths', 'protected_paths', 'configuration'})
        require(context['stage'] == 4 and progress is None and context['carrier'] is None, 'only one closure agent')
        validate_review(evidence['candidate'], evidence['review'], evidence['verification'], evidence['dispatcher_ref'])
        validate_binding(evidence['binding'])
        implementation = paths(evidence['implementation_paths'])
        closure = paths(evidence['closure_paths'], empty=True)
        require(all(PurePosixPath(p).suffix.lower() in {'.md', '.mdx', '.rst', '.adoc', '.asciidoc', '.org', '.txt'} or PurePosixPath(p).name.lower() in {'readme', 'changelog', 'authors', 'maintainers'} for p in closure), 'closure scope must contain documentation files only')
        protected = paths(evidence['protected_paths'], empty=True)
        require(not set(implementation) & set(closure), 'closure overlaps implementation contracts')
        validate_validation_plan(evidence['verification']['validation_plan'], evidence['binding'])
        require(not (set(implementation) | set(closure)) & set(protected), 'closure scope includes protected source')
        attempt = digest(evidence)
        context['carrier'] = {'kind': 'closure-agent', 'ref': None, 'attempt': attempt}
        context['handoff_progress'] = {**evidence, 'state': 'closure-pending', 'merge': None}
        return {'ok': True, 'context': context, 'attempt': attempt,
                'effects': [{'operation': 'spawn_native', 'role': 'closure-agent', 'configuration': evidence['configuration']}]}
    elif action == 'closure-bound':
        keys(evidence, {'ref', 'attempt'})
        require(progress and progress['state'] == 'closure-pending' and evidence['attempt'] == context['carrier']['attempt'], 'wrong closure attempt')
        context['carrier']['ref'] = text(evidence['ref'])
        progress['state'] = 'closing'
    elif action == 'closure-result':
        keys(evidence, {'ref', 'candidate', 'merge', 'ancestor_verified', 'changed_paths', 'binding',
                        'checks', 'worktree_removed', 'branch_removed', 'implementation_problem'})
        require(progress and progress['state'] in {'closing', 'cleanup-pending'} and evidence['ref'] == context['carrier']['ref'], 'wrong closure identity/state')
        require(evidence['candidate'] == progress['candidate'] and evidence['binding'] == progress['binding'], 'closure candidate or binding mismatch')
        if evidence['implementation_problem'] is not None:
            require(progress['merge'] is None, 'published candidate cannot return to implementation')
            text(evidence['implementation_problem'])
            progress.update(state='implementation-required', problem=evidence['implementation_problem'])
            return {'ok': True, 'context': context, 'effects': [], 'return_stage': 3}
        require(evidence['ancestor_verified'] is True and evidence['checks'], 'publication ancestry/checks are unverified')
        require(isinstance(evidence['merge'], str) and re.fullmatch('[0-9a-f]{40}', evidence['merge']), 'verified merge required')
        require(set(paths(evidence['changed_paths'])) <= set(progress['implementation_paths'] + progress['closure_paths']), 'closure changed paths escape scope')
        require(progress['merge'] is None or progress['merge'] == evidence['merge'], 'cleanup recovery must not republish')
        progress['merge'] = evidence['merge']
        if evidence['worktree_removed'] is True and evidence['branch_removed'] is True:
            progress['state'] = 'completed'
        else:
            progress['state'] = 'cleanup-pending'
            effects = [{'operation': 'cleanup-only', 'binding': progress['binding'], 'merge': evidence['merge']}]
    elif action == 'standalone-entry':
        keys(evidence, {'goal', 'complexity', 'implementation_basis', 'allowed_paths', 'failure_semantics',
                        'acceptance', 'testing_seam', 'skip_stages_1_2', 'explicit_standalone', 'claimed_attached', 'gate_open'})
        require(context['stage'] in {0, 1} and evidence['complexity'] == 'low' and
                evidence['explicit_standalone'] is True and evidence['skip_stages_1_2'] is True and
                evidence['claimed_attached'] is False and evidence['gate_open'] is True,
                'standalone route must be explicitly qualified and unattached')
        for field in ('goal', 'implementation_basis', 'failure_semantics', 'testing_seam'):
            text(evidence[field])
        paths(evidence['allowed_paths'])
        require(isinstance(evidence['acceptance'], list) and evidence['acceptance'], 'acceptance criteria missing')
        context.update(stage=3, topic_ref=None, carrier=None, flow_authority=None, handoff_progress=None,
                       preference={'topic_current': False, 'stage_current': False})
    elif action == 'start-dispatch':
        keys(evidence, {'binding', 'binding_verified', 'allowed_paths', 'protected_paths', 'authority_digest', 'testing_basis', 'validation_plan', 'configuration'})
        require(context['stage'] == 3 and progress is None and context['carrier'] is None, 'only one implementation dispatcher')
        require(evidence['binding_verified'] is True and isinstance(evidence['binding'], dict) and evidence['binding'], 'verified worktree required')
        validate_binding(evidence['binding'])
        allowed, protected = paths(evidence['allowed_paths']), paths(evidence['protected_paths'], empty=True)
        require(not set(allowed) & set(protected), 'protected scope overlap')
        text(evidence['testing_basis'])
        validate_validation_plan(evidence['validation_plan'], evidence['binding'])
        attempt = digest(evidence)
        context['carrier'] = {'kind': 'implementation-dispatcher', 'ref': None, 'attempt': attempt}
        context['handoff_progress'] = {'state': 'dispatcher-pending', 'binding': evidence['binding'],
            'allowed_paths': allowed, 'protected_paths': protected, 'authority_digest': evidence['authority_digest'],
            'executions': [], 'candidate': None, 'tests': [], 'configuration': evidence['configuration'],
            'validation_plan': evidence['validation_plan'], 'plan_digest': digest(evidence['validation_plan'])}
        return {'ok': True, 'context': context, 'attempt': attempt,
                'effects': [{'operation': 'spawn_native', 'role': 'implementation-dispatcher', 'configuration': evidence['configuration']}]}
    elif action == 'dispatcher-bound':
        keys(evidence, {'ref', 'attempt'})
        require(progress and progress['state'] == 'dispatcher-pending' and evidence['attempt'] == context['carrier']['attempt'], 'wrong dispatcher attempt')
        context['carrier']['ref'] = text(evidence['ref'])
        progress['state'] = 'implementing'
    elif action == 'plan-execution':
        keys(evidence, {'task_id', 'paths', 'read_only', 'behavior', 'tests', 'git_operations', 'configuration'})
        require(progress and progress['state'] == 'implementing', 'dispatcher is not accepting assignments')
        text(evidence['task_id']); text(evidence['behavior'])
        assigned = paths(evidence['paths'])
        paths(evidence['read_only'], empty=True)
        require(isinstance(evidence['tests'], list) and evidence['tests'], 'testing requirement is missing')
        require(evidence['git_operations'] == [], 'execution agents cannot mutate Git')
        require(set(assigned) <= set(progress['allowed_paths']) and not set(assigned) & set(progress['protected_paths']), 'assignment escapes implementation scope')
        require(all(e['task_id'] != evidence['task_id'] for e in progress['executions']), 'execution identity reused')
        require(all(e.get('stopped') is True or not set(e['paths']) & set(assigned) for e in progress['executions']), 'concurrent file assignment overlap')
        allocation_digest = digest({'binding': progress['binding'], 'envelope': evidence})
        progress['executions'].append({**evidence, 'agent_ref': None, 'state': 'dispatch-pending', 'stopped': False,
                                       'file_hashes': {}, 'allocation_digest': allocation_digest})
        return {'ok': True, 'context': context, 'allocation_digest': allocation_digest,
                'effects': [{'operation': 'spawn_native', 'role': 'execution-agent', 'envelope': evidence}]}
    elif action == 'execution-dispatch-result':
        keys(evidence, {'task_id', 'allocation_digest', 'status'})
        require(progress and progress['state'] in {'implementing', 'cancelled'}, 'no native allocation to reconcile')
        matches = [e for e in progress['executions'] if e['task_id'] == evidence['task_id'] and e['allocation_digest'] == evidence['allocation_digest']]
        require(len(matches) == 1 and matches[0]['state'] == 'dispatch-pending', 'allocation is not pending')
        require(evidence['status'] in {'unknown', 'not-created'}, 'invalid native dispatch outcome')
        if evidence['status'] == 'not-created':
            matches[0].update(state='cancelled', stopped=True)
        else:
            effects = [{'operation': 'read-native-state', 'task_id': evidence['task_id'], 'allocation_digest': evidence['allocation_digest']}]
    elif action == 'assign':
        keys(evidence, {'agent_ref', 'task_id', 'allocation_digest'})
        require(progress and progress['state'] == 'implementing', 'dispatcher is not accepting assignments')
        matches = [e for e in progress['executions'] if e['task_id'] == evidence['task_id'] and e['allocation_digest'] == evidence['allocation_digest']]
        require(len(matches) == 1 and matches[0]['state'] == 'dispatch-pending', 'allocation not prepared or already bound')
        ref = text(evidence['agent_ref'])
        require(ref != context['carrier']['ref'] and all(e['agent_ref'] != ref for e in progress['executions']), 'native identity reused')
        matches[0].update(agent_ref=ref, state='assigned')
    elif action == 'execution-result':
        keys(evidence, {'agent_ref', 'stopped', 'changed_paths', 'file_hashes', 'tests', 'git_unchanged'})
        execution = execution_record(progress, evidence['agent_ref'])
        require(execution['state'] in {'assigned', 'received'}, 'execution result requires an actual bound writer')
        require(evidence['stopped'] is True and evidence['git_unchanged'] is True, 'execution must stop without Git mutations')
        require(set(paths(evidence['changed_paths'], empty=True)) <= set(execution['paths']), 'actual diff escapes allocation')
        hashes(evidence['file_hashes'])
        require(set(evidence['file_hashes']) == set(evidence['changed_paths']), 'diff hashes incomplete')
        require(isinstance(evidence['tests'], list) and evidence['tests'], 'execution test evidence missing')
        execution.update(state='received', stopped=True, file_hashes=evidence['file_hashes'], tests=evidence['tests'])
    elif action == 'accept-execution':
        keys(evidence, {'agent_ref', 'file_hashes'})
        execution = execution_record(progress, evidence['agent_ref'])
        require(execution['state'] == 'received' and execution['stopped'] is True and
                evidence['file_hashes'] == execution['file_hashes'], 'execution bytes do not match received result')
        execution['state'] = 'accepted'
    elif action == 'recover-dispatch':
        keys(evidence, {'stopped_refs', 'file_hashes', 'replacement_ref'})
        require(progress and 'executions' in progress, 'no dispatcher checkpoint')
        require(all(e['agent_ref'] is not None or e['state'] == 'cancelled' and e['stopped'] for e in progress['executions']), 'unbound native dispatch must be reconciled before replacement')
        required = {context['carrier']['ref']} | {e['agent_ref'] for e in progress['executions'] if e['agent_ref'] is not None}
        require(required <= set(evidence['stopped_refs']), 'all old writers must be proven stopped before replacement')
        accepted = {}
        revalidate = []
        for execution in progress['executions']:
            execution['stopped'] = True
            if execution['state'] == 'accepted':
                accepted.update(execution['file_hashes'])
            elif execution['state'] != 'cancelled':
                revalidate.append(execution['agent_ref'])
        require(all(evidence['file_hashes'].get(p) == h for p, h in accepted.items()), 'accepted bytes changed during interruption')
        context['carrier']['ref'] = text(evidence['replacement_ref'])
        progress['state'] = 'implementing'
        return {'ok': True, 'context': context, 'effects': [], 'revalidate': revalidate,
                'remaining_paths': sorted(set(progress['allowed_paths']) - set(accepted))}
    elif action == 'candidate-ready':
        keys(evidence, {'dispatcher_ref', 'attempt', 'commit', 'expected_target_head', 'binding', 'plan_digest', 'checks', 'source', 'clean', 'changed_paths', 'file_hashes'})
        require(progress and evidence['dispatcher_ref'] == context['carrier']['ref'] and evidence['attempt'] == context['carrier']['attempt'], 'wrong dispatcher identity or attempt')
        if progress.get('candidate_evidence') is not None:
            require(progress['candidate_evidence'] == evidence, 'candidate identity cannot change; invalidate explicitly')
            return {'ok': True, 'context': context, 'effects': [], 'acknowledged': True}
        require(progress['state'] == 'implementing', 'candidate is not implementing')
        require(all(e['stopped'] and e['state'] in {'accepted', 'cancelled'} for e in progress['executions']), 'all executions must stop and be accepted')
        require(evidence['clean'] is True and evidence['binding'] == progress['binding'], 'candidate must be clean in bound worktree')
        require(set(paths(evidence['changed_paths'])) <= set(progress['allowed_paths']), 'candidate diff escapes scope')
        require(re.fullmatch('[0-9a-f]{40}', evidence['commit']) and evidence['plan_digest'] == progress['plan_digest'], 'candidate commit or plan changed')
        validate_checks(progress['validation_plan']['review_required'], evidence['checks'], evidence['commit'])
        validate_result_source(evidence['source'], evidence['checks'], context['carrier']['ref'])
        accepted_hashes = {}
        for execution in progress['executions']:
            accepted_hashes.update(execution['file_hashes'])
        require(all(evidence['file_hashes'].get(p) == h for p, h in accepted_hashes.items()), 'accepted bytes differ from candidate')
        progress.update(state='reviewable', candidate=evidence['commit'], tests=evidence['checks'],
                        expected_target_head=evidence['expected_target_head'], candidate_evidence=evidence)
    elif action == 'review-start':
        keys(evidence, {'candidate'})
        require(progress and progress['state'] in {'reviewable', 'reviewing'} and progress['candidate'] == evidence['candidate'], 'candidate is not reviewable')
        progress['state'] = 'reviewing'
    elif action == 'review-converged':
        keys(evidence, {'candidate', 'review', 'reference', 'native_evidence'})
        require(progress and progress['state'] == 'reviewing' and evidence['candidate'] == progress['candidate'], 'review has not started for exact candidate')
        validate_review_axes(progress['candidate'], progress['expected_target_head'], progress['plan_digest'], evidence['review'], context['carrier']['ref'])
        decision = {'controller_ref':context['controller_ref'], 'reference':text(evidence['reference']),
                    'review':evidence['review'], 'native_evidence':evidence['native_evidence']}
        keys(decision['native_evidence'], {'standards', 'spec'})
        for axis, receipt in decision['native_evidence'].items():
            keys(receipt, {'adapter','call_ref','response_ref','status','ref','raw'})
            for field in ('adapter','call_ref','response_ref'):
                text(receipt[field])
            require(receipt.get('status') == 'stopped' and receipt.get('ref') == evidence['review'][axis]['reviewer_ref'] and
                    receipt.get('response_ref') == evidence['review'][axis]['result_ref'] and receipt.get('raw'), 'native review evidence mismatch')
        progress.update(state='final-validation-pending', review_decision=decision, review_digest=digest(decision), attempts=[])
    elif action == 'validation-start':
        keys(evidence, {'attempt_id', 'dispatcher_ref', 'carrier_attempt', 'candidate', 'expected_target_head', 'plan_digest', 'review_digest', 'source_snapshot'})
        require(progress and progress['state'] == 'final-validation-pending', 'final validation requires converged review')
        require(evidence['dispatcher_ref'] == context['carrier']['ref'] and evidence['carrier_attempt'] == context['carrier']['attempt'], 'final validation must resume original dispatcher')
        require(all(evidence[k] == progress[k] for k in ('candidate','expected_target_head','plan_digest','review_digest')), 'final validation fixed point changed')
        text(evidence['attempt_id'])
        require(all(v['attempt_id'] != evidence['attempt_id'] for v in progress['attempts']), 'validation attempt reused')
        progress['attempts'].append({**evidence, 'required':copy.deepcopy(progress['validation_plan']['final_required']),
                                     'state':'running', 'checks':[], 'source':None})
        progress['state'] = 'validating'
        effects = [{'operation':'continue-host', 'ref':context['carrier']['ref'], 'validation_attempt':copy.deepcopy(progress['attempts'][-1])}]
    elif action == 'validation-result':
        keys(evidence, {'attempt_id', 'checks', 'source', 'source_snapshot'})
        require(progress and progress.get('attempts'), 'no final validation attempt')
        attempt = progress['attempts'][-1]
        require(evidence['attempt_id'] == attempt['attempt_id'], 'result belongs to another attempt')
        if attempt['state'] != 'running':
            require(attempt.get('result') == evidence, 'validation result identity cannot change')
            return {'ok':True, 'context':context, 'effects':[], 'acknowledged':True}
        require(progress['state'] == 'validating', 'validation is not active')
        validate_checks(attempt['required'], evidence['checks'], progress['candidate'], passed=False, complete=False)
        validate_result_source(evidence['source'], evidence['checks'], context['carrier']['ref'], attempt['attempt_id'])
        require(all(not old.get('source') or old['source']['response_ref'] != evidence['source']['response_ref'] for old in progress['attempts'][:-1]), 'command result response reused across attempts')
        passed = len(evidence['checks']) == len(attempt['required']) and evidence['source_snapshot'] == attempt['source_snapshot'] and all(check_passed(v) for v in evidence['checks'])
        attempt.update(state='passed' if passed else 'failed', checks=evidence['checks'], source=evidence['source'], result=evidence)
        progress['state'] = 'deliverable' if passed else 'final-validation-failed'
    elif action == 'validation-retry':
        keys(evidence, {'attempt_id', 'reference'})
        require(progress and progress['state'] == 'final-validation-failed' and progress['attempts'][-1]['attempt_id'] == evidence['attempt_id'], 'retry must name latest failed attempt')
        text(evidence['reference'])
        progress['attempts'][-1]['retry_decision'] = copy.deepcopy(evidence)
        progress['state'] = 'final-validation-pending'
    elif action == 'invalidate-candidate':
        keys(evidence, {'candidate', 'reference', 'reason'} | ({'stopped'} if 'stopped' in evidence else set()))
        require(progress and progress.get('candidate') == evidence['candidate'] and not progress.get('accepted_delivery'), 'accepted delivery cannot be replaced')
        require(progress['state'] != 'cancelled', 'cancelled validation cannot be invalidated')
        if progress['state'] == 'validating':
            require(evidence.get('stopped') is True, 'reconcile active validation before invalidation')
            progress['attempts'][-1].update(state='unknown', invalidated=True)
        text(evidence['reference']); text(evidence['reason'])
        progress.setdefault('validation_history', []).append({k:copy.deepcopy(progress[k]) for k in
            ('candidate','expected_target_head','candidate_evidence','tests','review_decision','review_digest','attempts') if k in progress} | {'invalidation':evidence})
        for key in ('expected_target_head','candidate_evidence','review_decision','review_digest','attempts'):
            progress.pop(key, None)
        progress.update(state='implementing', candidate=None, tests=[])
    elif action in {'delivery-ready', 'accept-delivery'}:
        keys(evidence, {'candidate', 'review', 'verification', 'dispatcher_ref'} | ({'delivery_digest'} if action == 'accept-delivery' else set()))
        validate_deliverable(progress, **{k:v for k,v in evidence.items() if k != 'delivery_digest'})
        if action == 'accept-delivery':
            require(progress.get('accepted_delivery') in {None, evidence['delivery_digest']}, 'accepted delivery cannot change')
            progress['accepted_delivery'] = text(evidence['delivery_digest'])
    elif action == 'receive':
        keys(evidence, {'delivery_id', 'source_ref', 'attempt', 'requirement_identity', 'commit', 'verified_commit_hash'})
        require(context['carrier'] is not None and evidence['source_ref'] == context['carrier']['ref'] and
                evidence['attempt'] == context['carrier']['attempt'], 'delivery source does not match trusted creation')
        completed = evidence['requirement_identity']
        keys(completed, {'path', 'sha256', 'version'})
        require(completed['path'] == context['requirement_identity']['path'] and type(completed['version']) is int and
                completed['version'] >= context['requirement_identity']['version'], 'delivery document path or version regressed')
        snapshot_only = evidence['commit'] is None and context['stage'] == 0 and context['topic_ref'] is not None
        require((snapshot_only or isinstance(evidence['commit'], str) and re.fullmatch('[0-9a-f]{40}', evidence['commit'])) and
                evidence['verified_commit_hash'] == completed['sha256'], 'delivery commit/hash not verified')
        text(evidence['delivery_id'])
        delivery_digest = digest(evidence)
        if 'delivery' in progress:
            require(progress['delivery'] == evidence, 'accepted delivery cannot be replaced')
            return {'ok': True, 'context': context, 'effects': [], 'acknowledged': True, 'delivery_digest': delivery_digest}
        require(progress['state'] == 'carrier-bound', 'carrier result is not eligible')
        context['requirement_identity'] = completed
        progress.update(state='result-received', delivery=evidence, delivery_digest=delivery_digest)
        return {'ok': True, 'context': context, 'effects': [], 'delivery_digest': delivery_digest}
    elif action == 'accept':
        keys(evidence, {'delivery_digest'})
        require(progress and progress['state'] in {'result-received', 'result-accepted'} and
                progress['delivery_digest'] == evidence['delivery_digest'], 'result must be verified before acceptance')
        progress['state'] = 'result-accepted'
    elif action == 'successor-ready':
        keys(evidence, {'ref', 'stage', 'input_digest', 'role', 'binding_verified', 'activated', 'confirmed', 'archive_ref'})
        require(progress and progress['state'] == 'result-accepted', 'accept before successor takeover')
        require(evidence['input_digest'] == progress['delivery_digest'] and evidence['confirmed'] is True,
                'successor frozen input or confirmation mismatch')
        require((context['stage'], evidence['stage']) in {(0, 1), (0, 2), (1, 2), (2, 3), (3, 4)}, 'illegal successor stage')
        require(evidence['binding_verified'] is True and (context['topic_ref'] is None or evidence['activated'] is True),
                'successor readiness and source activation required')
        text(evidence['ref'])
        require(evidence['role'] in {1: {'dedicated-problem-framing', 'current-problem-framing'}, 2: {'solution-designer'}, 3: {'implementation-dispatcher'}, 4: {'closure-agent'}}[evidence['stage']] and evidence['ref'] != context['carrier']['ref'], 'successor role or identity mismatch')
        require(evidence['archive_ref'] == context['carrier']['ref'], 'archive target must be the current visible carrier')
        progress.update(state='successor-ready', successor=evidence, archive_ref=evidence['archive_ref'])
    elif action == 'archive':
        keys(evidence, set())
        require(progress and progress['state'] in {'successor-ready', 'archive-pending', 'archived'}, 'verified takeover required before archive')
        if progress['state'] != 'archived':
            old = progress['archive_ref']
            require(old is not None and old == context['carrier']['ref'], 'archive target is not the frozen old task')
            operation = 'read-archive-state' if progress.get('archive_status') in {'unknown', 'requested'} else 'archive'
            progress.update(state='archive-pending', archive_status='requested')
            effects = [{'operation': operation, 'ref': old}]
    elif action == 'archive-result':
        keys(evidence, {'ref', 'status'})
        require(progress and progress['state'] == 'archive-pending' and evidence['ref'] == progress['archive_ref'], 'archive result mismatch')
        require(evidence['status'] in {'archived', 'not-archived', 'unknown', 'failed'}, 'invalid archive evidence')
        progress['archive_status'] = evidence['status']
        if evidence['status'] == 'archived':
            progress['state'] = 'archived'
    elif action == 'choose-dedicated':
        keys(evidence, {'intent'})
        require(evidence['intent'] == 'explicit-dedicated' and (progress is None or progress['state'] in {'prepared', 'current-task', 'cancelled', 'creation-failed'}), 'new explicit choice requires a stopped or unlaunched carrier')
        context.update(carrier=None, handoff_progress=None, preference={'topic_current': False, 'stage_current': False})
    elif action == 'cancel':
        keys(evidence, set())
        require(progress is not None, 'no attempt to cancel')
        if 'plan' in progress:
            require(progress['state'] in {'prepared', 'creation-pending', 'carrier-bound', 'creation-failed', 'cancelled'}, 'received results cannot be cancelled')
        progress['state'] = 'cancelled'
        if 'executions' in progress:
            effects = [({'operation': 'stop-native', 'ref': e['agent_ref']} if e['agent_ref'] is not None else {'operation': 'read-native-state', 'task_id': e['task_id'], 'allocation_digest': e['allocation_digest']}) for e in progress['executions'] if not e['stopped']]
            effects.append({'operation': 'stop-native', 'ref': context['carrier']['ref']})
    elif action == 'creation-result':
        keys(evidence, {'status', 'ref', 'attempt'})
        require(progress is not None and progress['state'] == 'creation-pending', 'attempt no longer eligible')
        require(context['carrier']['attempt'] == evidence['attempt'], 'wrong attempt')
        require(evidence['status'] in {'pending', 'unknown', 'failed', 'ready'}, 'invalid creation result')
        progress['creation_status'] = evidence['status']
        if evidence['status'] == 'ready':
            context['carrier']['ref'] = text(evidence['ref'])
            progress['state'] = 'carrier-bound'
        elif evidence['status'] == 'failed':
            progress['state'] = 'creation-failed'
        # Pending IDs are never usable task references. Unknown outcomes require reconciliation.
    else:
        raise ControlError('unknown action')
    return {'ok': True, 'context': context, 'effects': effects}


def selected_control(context, evidence):
    """Select a bounded slot using an exact plan or delivery identity."""
    successor = context.get('successor_control')
    if successor is None:
        selected = context
    else:
        identities = {text(evidence[k]) for k in ('control_plan_id', 'plan_id', 'attempt', 'delivery_digest', 'input_digest') if k in evidence}
        matches = []
        for slot in (context, successor):
            progress = slot.get('handoff_progress') or {}
            known = {progress.get('plan', {}).get('plan_id'), progress.get('delivery_digest')} - {None}
            if identities & known:
                matches.append(slot)
        require(len(matches) == 1, 'two control slots require an exact plan or delivery identity')
        selected = matches[0]
    if 'control_plan_id' in evidence:
        require((selected.get('handoff_progress') or {}).get('plan', {}).get('plan_id') == evidence['control_plan_id'], 'wrong control slot identity')
    return selected


def transition(request: dict[str, Any]) -> dict[str, Any]:
    keys(request, {'schema_version', 'action', 'actor_ref', 'context', 'evidence'})
    context = copy.deepcopy(request['context'])
    validate_context(context)
    evidence = copy.deepcopy(request['evidence'])
    require(isinstance(evidence, dict), 'evidence must be an object')
    action = request['action']
    authority = evidence.get('entry_authority') if action == 'prepare' else None
    if authority is not None:
        validate_entry_authority(authority)
    progress = context.get('handoff_progress') or {}
    successor_prepare = (authority is not None and authority['kind'] != 'standalone'
        and authority['stage'] != context['stage'] and context['carrier'] is not None)
    if successor_prepare:
        require(authority['kind'] == 'wrapper-phase-run' and context['stage'] == 0
                and progress.get('state') == 'result-accepted', 'predecessor must be accepted before successor preparation')
        existing = context.get('successor_control')
        if existing is not None:
            old = existing.get('handoff_progress') or {}
            stopped = old.get('state') in {'cancelled', 'creation-failed'} or (not old and existing['carrier'] is None)
            require(stopped and old.get('plan', {}).get('entry_authority') != authority, 'successor slot already occupied')
        selected = {k: copy.deepcopy(v) for k, v in context.items() if k != 'successor_control'}
        selected.update(stage=authority['stage'], carrier=None, handoff_progress=None,
            preference={'topic_current': context['preference']['topic_current'], 'stage_current': False})
        context['successor_control'] = selected
    else:
        selected = selected_control(context, evidence)
    evidence.pop('control_plan_id', None)
    # A child transition sees a single slot, never a recursively routable context.
    single = {k: v for k, v in selected.items() if k != 'successor_control'}
    result = _transition_single({**request, 'context': single, 'evidence': evidence})
    if selected is context:
        successor = context.get('successor_control')
        context = result['context']
        if successor is not None:
            context['successor_control'] = successor
            if action == 'archive-result' and evidence.get('status') == 'archived':
                context = successor
    else:
        context['successor_control'] = result['context']
    return {**result, 'context': context}


def main():
    try:
        data = sys.stdin.buffer.read(MAX_BYTES + 1)
        require(len(data) <= MAX_BYTES, 'request exceeds byte limit')
        result = transition(decode_json(data.decode('utf-8')))
    except (ControlError, ValueError, UnicodeError, TypeError, KeyError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0

if __name__ == '__main__':
    sys.exit(main())
