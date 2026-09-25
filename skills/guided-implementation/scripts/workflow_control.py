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
STAGE3_DEFAULT_CONFIGURATIONS = frozenset({
    ('gpt-6-luna', 'high'),
    ('gpt-6-luna', 'xhigh'),
    ('gpt-6-sol', 'medium'),
    ('gpt-6-sol', 'high'),
    ('gpt-6-sol', 'xhigh'),
})


def stage3_eligible_defaults(supported, required_capability, available_pairs):
    capabilities = {(item['model'], item['effort']): item['capability'] for item in supported}
    return {pair for pair in STAGE3_DEFAULT_CONFIGURATIONS & set(available_pairs)
            if pair not in capabilities or required_capability is None or
            capabilities[pair] is None or capabilities[pair] >= required_capability}

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


IMPLEMENTATION_CONDITIONS = {'behavior_fixed', 'single_responsibility', 'focused_verification', 'locations_known'}
IMPLEMENTATION_EXCLUSIONS = {'state_machine', 'concurrency', 'recovery', 'migration', 'public_interface',
    'cross_module_interface', 'data_format', 'permissions', 'workflow_state'}


def implementation_policy(value=None, *, controller_ref=None, requirement_identity=None, scope_digest=None, baseline=None):
    """Validate Controller evidence; semantic risk remains a reviewed judgment."""
    if value is None:
        return {'mode': 'full'}
    require(isinstance(value, dict), 'implementation policy must be an object')
    if value.get('mode') == 'full':
        keys(value, {'mode'})
        return copy.deepcopy(value)
    keys(value, {'mode', 'assessment'})
    require(value['mode'] == 'direct', 'unknown implementation mode')
    assessment = value['assessment']
    keys(assessment, {'reference', 'controller_ref', 'requirement_identity', 'scope_digest', 'baseline',
        'responsibility', 'implementation_paths', 'test_paths', 'behavior_ref', 'acceptance_ref',
        'checks', 'testing_seam', 'conditions', 'exclusions'})
    for key in ('reference', 'controller_ref', 'scope_digest', 'baseline', 'responsibility',
                'behavior_ref', 'acceptance_ref', 'testing_seam'):
        text(assessment[key])
    paths(assessment['implementation_paths']); paths(assessment['test_paths'])
    require(isinstance(assessment['checks'], list) and assessment['checks'], 'direct checks required')
    for check in assessment['checks']:
        text(check)
    for field, names, verdict, expected in (('conditions', IMPLEMENTATION_CONDITIONS, 'satisfied', True),
                                          ('exclusions', IMPLEMENTATION_EXCLUSIONS, 'present', False)):
        keys(assessment[field], names)
        for fact in assessment[field].values():
            keys(fact, {verdict, 'evidence'})
            require(fact[verdict] is expected, 'direct eligibility is unknown or excluded; use full')
            text(fact['evidence'])
    for name, expected in (('controller_ref', controller_ref), ('requirement_identity', requirement_identity),
                           ('scope_digest', scope_digest), ('baseline', baseline)):
        if expected is not None:
            require(assessment[name] == expected, 'direct assessment ' + name + ' mismatch')
    return copy.deepcopy(value)


def direct_implementation(progress):
    return progress.get('implementation_policy', {'mode': 'full'})['mode'] == 'direct'


def adoption_source(progress):
    escalation = progress.get('implementation_escalation')
    if escalation is not None:
        return escalation
    recovery = progress.get('recovery')
    if recovery and recovery['state'] == 'activated' and recovery.get('direct_policy'):
        return {'snapshot': recovery['snapshot'], 'snapshot_digest': recovery['snapshot_digest'],
                'pending_paths': recovery['ownership']['dispatcher']}
    return None


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
        allowed = {'state', 'binding', 'allowed_paths', 'protected_paths', 'authority_digest', 'executions', 'candidate', 'tests', 'configuration', 'git_baseline_commit', 'validation_plan', 'plan_digest', 'expected_target_head', 'candidate_evidence', 'review_decision', 'review_digest', 'attempts', 'validation_history', 'accepted_delivery', 'recovery', 'recovery_history', 'implementation_policy', 'implementation_escalation', 'implementation_target_head'}
        require(state in {'dispatcher-pending', 'implementing', 'reviewable', 'reviewing', 'final-validation-pending', 'validating', 'final-validation-failed', 'deliverable', 'cancelled'}, 'invalid dispatcher state')
        validate_binding(progress['binding'])
        paths(progress['allowed_paths']); paths(progress['protected_paths'], empty=True)
        require('implementation_policy' not in progress or isinstance(progress['implementation_policy'], dict), 'invalid checkpoint implementation policy')
        implementation_policy(progress.get('implementation_policy'))
        if 'implementation_escalation' in progress:
            escalation = progress['implementation_escalation']
            keys(escalation, {'dispatcher_ref', 'attempt', 'reference', 'reason', 'assessment_reference', 'stop',
                'snapshot', 'pending_paths', 'policy', 'snapshot_digest'})
            require(not direct_implementation(progress) and escalation['snapshot_digest'] == digest(escalation['snapshot']),
                    'escalation history changed')
            original = implementation_policy(escalation['policy'])
            require(original['mode'] == 'direct' and original['assessment']['reference'] == escalation['assessment_reference'],
                    'escalation policy changed')
            require(set(paths(escalation['pending_paths'], empty=True)) <= set(progress['allowed_paths']), 'escalation paths changed')
            require(escalation['stop'].get('git_snapshot') == escalation['snapshot'], 'escalation stop snapshot changed')
        validate_validation_plan(progress['validation_plan'], progress['binding'])
        require(progress['plan_digest'] == digest(progress['validation_plan']), 'frozen validation plan changed')
        if 'attempts' in progress:
            validate_attempt_identities(progress['attempts'])
        if 'recovery' in progress:
            validate_recovery(progress['recovery'], progress)
        if 'recovery_history' in progress:
            require(isinstance(progress['recovery_history'], list) and len(progress['recovery_history']) <= 32, 'invalid recovery history')
            for recovery in progress['recovery_history']:
                validate_recovery(recovery, progress)
        require(isinstance(progress['executions'], list), 'executions must be a list')
        for execution in progress['executions']:
            required = {'task_id', 'paths', 'read_only', 'behavior', 'tests', 'git_operations', 'configuration', 'agent_ref', 'state', 'stopped', 'file_hashes', 'allocation_digest'}
            require(isinstance(execution, dict) and required <= set(execution) <= required | {'git_snapshot', 'git_result_snapshot', 'adopt_paths', 'adoption_snapshot_digest', 'adoption_fingerprints', 'changed_paths', 'adopted_paths'}, 'invalid execution envelope fields')
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
        require(state in {'prepared', 'creation-pending', 'current-task', 'cancelled', 'creation-failed', 'carrier-bound', 'result-received', 'result-accepted', 'successor-ready', 'archive-pending', 'archived', 'handoff-complete'}, 'invalid dedicated state')
        plan = progress.get('plan')
        keys(plan, {'target', 'project', 'title', 'missing_context', 'configuration', 'next_step', 'archive_ref', 'gate_open', 'stage', 'controller_ref', 'topic_ref', 'requirement_identity', 'task_count', 'plan_id', 'entry_authority'})
        validate_entry_authority(plan['entry_authority'])
        validate_selection(plan['configuration'])
        require(type(plan['task_count']) is int and plan['task_count'] == 1 and plan['plan_id'] == digest({k: v for k, v in plan.items() if k != 'plan_id'}), 'prepared plan digest changed')
    if 'launch_input' in progress:
        text(progress['launch_input'])
    require(set(progress) <= allowed | {'launch_input'}, 'unknown checkpoint fields')


def validate_recovery(recovery, progress):
    require(isinstance(recovery, dict), 'invalid recovery checkpoint')
    frozen_keys = {'dispatcher_ref', 'attempt', 'reference', 'reason', 'stop_receipts', 'call_receipts',
                   'host_evidence', 'snapshot', 'ownership', 'revalidate', 'authority_digest', 'remaining_paths'}
    if 'direct_policy' in recovery:
        frozen_keys.add('direct_policy')
        require(implementation_policy(recovery['direct_policy'])['mode'] == 'direct', 'invalid recovered direct policy')
    required = frozen_keys | {'recovery_id', 'snapshot_digest', 'state', 'decision', 'intent', 'receipts', 'activation'}
    require(isinstance(recovery, dict) and required <= set(recovery) <= required | {'releases'}, 'invalid recovery checkpoint')
    require(recovery['recovery_id'] == digest({k:recovery[k] for k in frozen_keys}) and
            recovery['snapshot_digest'] == digest(recovery['snapshot']), 'prepared recovery fingerprint changed')
    require(recovery['authority_digest'] == progress['authority_digest'], 'recovery authority changed')
    require(recovery['state'] in {'prepared', 'dispatch-pending', 'dispatch-unknown', 'not-created', 'ready', 'activated'}, 'invalid recovery state')
    for key in ('dispatcher_ref', 'attempt', 'reference', 'reason'):
        text(recovery[key])
    snapshot = recovery['snapshot']
    keys(snapshot, {'head', 'branch', 'index_hash', 'base', 'target', 'files'})
    hashes(snapshot['files'])
    require(set(snapshot['files']) == set(progress['allowed_paths'] + progress['protected_paths']), 'recovery snapshot scope changed')
    for key in ('head', 'base', 'target'):
        require(isinstance(snapshot[key], str) and re.fullmatch('[0-9a-f]{40}', snapshot[key]), 'invalid recovery commit')
    require(isinstance(snapshot['index_hash'], str) and re.fullmatch('[0-9a-f]{64}', snapshot['index_hash']), 'invalid recovery index')
    ownership = recovery['ownership']
    keys(ownership, {'accepted', 'unaccepted', 'dispatcher'})
    seen = set()
    for values in ownership.values():
        current = set(paths(values, empty=True))
        require(not current & seen and current <= set(progress['allowed_paths']), 'conflicting recovery ownership')
        seen.update(current)
    require(recovery['remaining_paths'] == sorted(set(progress['allowed_paths']) - set(ownership['accepted'])), 'remaining recovery scope changed')
    require(isinstance(recovery['receipts'], list) and len(recovery['receipts']) <= 64, 'invalid recovery receipts')
    require(isinstance(recovery.get('releases', []), list) and len(recovery.get('releases', [])) <= len(progress['executions']), 'invalid ownership releases')
    if recovery['state'] != 'prepared':
        decision, intent = recovery['decision'], recovery['intent']
        keys(decision, {'reference', 'authority_digest', 'attempt', 'remaining_paths', 'assume_paths'})
        text(decision['reference'])
        require(decision['authority_digest'] == recovery['authority_digest'] and decision['attempt'] == recovery['attempt'] and
                decision['remaining_paths'] == recovery['remaining_paths'] and decision['assume_paths'] == ownership['dispatcher'], 'recovery decision drifted')
        keys(intent, {'intent_id','recovery_id','snapshot_digest','role','write_authority','configuration','binding',
            'authority_digest','plan_digest','allowed_paths','protected_paths','remaining_paths','revalidate','ownership','original_ref','original_attempt'})
        require(intent['intent_id'] == digest([recovery['recovery_id'], decision]) and intent['recovery_id'] == recovery['recovery_id'] and
                intent['snapshot_digest'] == recovery['snapshot_digest'] and intent['write_authority'] is False and
                intent['role'] == 'implementation-dispatcher', 'invalid recovery dispatch intent')
        for key in ('configuration','binding','authority_digest','plan_digest','allowed_paths','protected_paths'):
            require(intent[key] == progress[key], 'recovery dispatch authority drifted')
        for key in ('remaining_paths','revalidate','ownership'):
            require(intent[key] == recovery[key], 'recovery dispatch scope drifted')
        require(intent['original_ref'] == recovery['dispatcher_ref'] and intent['original_attempt'] == recovery['attempt'], 'recovery original identity changed')
    for receipt in recovery['receipts']:
        keys(receipt, {'adapter','call_ref','response_ref','intent_id','status','ref','raw'})
        require(recovery['intent'] is not None and receipt['intent_id'] == recovery['intent']['intent_id'], 'recovery receipt belongs to another intent')
        require(receipt['status'] in {'ready','unknown','not-created'}, 'invalid recovery receipt status')
    require((recovery['activation'] is not None) == (recovery['state'] == 'activated'), 'recovery activation state mismatch')


def validate_context(context):
    require(isinstance(context, dict) and CONTEXT_FIELDS <= set(context) <= CONTEXT_FIELDS | {'successor_control', 'retired_handoffs'}, 'invalid context fields')
    require(len(json.dumps(context).encode()) <= MAX_BYTES, 'control history exceeds byte limit')
    if context.get('successor_control') is not None:
        successor = context['successor_control']
        require(isinstance(successor, dict) and not ({'successor_control', 'retired_handoffs'} & set(successor)), 'only one successor slot and top-level history allowed')
        validate_context(successor)
        require(successor['controller_ref'] == context['controller_ref'] and successor['topic_ref'] == context['topic_ref'], 'successor controller/topic mismatch')
    require(type(context['schema_version']) is int and context['schema_version'] == 4, 'legacy_run_requires_original_runtime: control schema ' + str(context.get('schema_version')))
    require(isinstance(context.get('retired_handoffs', {}), dict), 'invalid retired handoff history')
    for handoff_id, retired in context.get('retired_handoffs', {}).items():
        keys(retired, {'controller_ref', 'topic_ref', 'stage', 'carrier', 'requirement_identity', 'plan',
            'delivery', 'delivery_digest', 'successor', 'handoff_completed', 'archive_status', 'operations', 'conflicts'})
        require(retired['controller_ref'] == context['controller_ref'] and retired['topic_ref'] == context['topic_ref'], 'retired handoff owner mismatch')
        require(retired['handoff_completed'] is True and retired['carrier']['kind'] == 'dedicated-stage', 'invalid retired carrier')
        require(retired['delivery_digest'] == digest(retired['delivery']), 'retired delivery changed')
        require(retired['requirement_identity'] == retired['delivery']['requirement_identity'] and
                retired['delivery']['source_ref'] == retired['carrier']['ref'] and
                retired['delivery']['attempt'] == retired['carrier']['attempt'] and
                retired['successor']['input_digest'] == retired['delivery_digest'], 'retired delivery identity mismatch')
        require(retired['plan']['plan_id'] == digest({k:v for k,v in retired['plan'].items() if k != 'plan_id'}), 'retired plan changed')
        require(handoff_id == digest({k:retired[k] for k in ('controller_ref', 'topic_ref', 'carrier', 'delivery_digest')} | {'plan_id': retired['plan']['plan_id']}), 'retired handoff identity changed')
        validate_takeover_proof(retired['successor']['takeover_proof'], retired['carrier'], retired['plan'])
        require(retired['archive_status'] in {'not-requested', 'requested', 'unknown', 'failed', 'not-archived', 'archived'}, 'invalid retired archive status')
        require(isinstance(retired['operations'], list) and isinstance(retired['conflicts'], list), 'invalid archive audit')
        predecessor = None
        for sequence, operation in enumerate(retired['operations'], 1):
            keys(operation, {'operation_id', 'kind', 'ref', 'sequence', 'predecessor', 'status', 'receipt', 'invocation'})
            require(operation['kind'] in {'archive', 'read-archive-state'} and operation['ref'] == retired['carrier']['ref'] and operation['sequence'] == sequence and operation['predecessor'] == predecessor, 'archive operation chain changed')
            require(operation['operation_id'] == digest({'handoff_id': handoff_id, 'sequence': sequence, 'kind': operation['kind'], 'ref': operation['ref']}), 'archive operation identity changed')
            require(operation['status'] in {'intent', 'issued', 'unknown', 'failed', 'not-archived', 'archived'}, 'invalid archive operation status')
            if operation['invocation'] is not None:
                keys(operation['invocation'], {'adapter', 'invocation_id', 'operation_id', 'ref', 'receipt'})
                require(operation['invocation']['operation_id'] == operation['operation_id'] and operation['invocation']['ref'] == operation['ref'], 'archive invocation changed')
            require(operation['status'] == 'intent' or operation['invocation'] is not None, 'archive operation lost invocation')
            predecessor = operation['operation_id']
        for conflict in retired['conflicts']:
            keys(conflict, {'operation_id', 'status', 'receipt', 'after_sequence'})
            require(type(conflict['after_sequence']) is int and 0 < conflict['after_sequence'] <= len(retired['operations']) and
                    any(op['operation_id'] == conflict['operation_id'] for op in retired['operations']), 'invalid archive conflict provenance')
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


def select_configuration(evidence, available_pairs=None):
    required = {'role', 'required_capability', 'supported', 'user', 'frozen', 'previous',
                'receipt', 'can_override', 'inherited', 'upgrade_attempted'}
    require(isinstance(evidence, dict) and required <= set(evidence) <= required | {'preferred', 'preference_reason'},
            'unexpected or missing configuration fields')
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
    stage3_role = evidence['role'] in {'implementation-dispatcher', 'execution-agent'}
    reason = 'current adapter evidence and role requirement'
    if evidence['can_override'] is False:
        selected = resolve(evidence['inherited'])
        require(selected is not None, 'actual inherited configuration unavailable')
        require(user is None or user == selected, 'adapter cannot express user override')
        source = 'inherited'
    elif user is not None:
        selected, source = user, 'user'
    elif frozen is not None:
        selected, source = frozen, 'confirmed'
    elif stage3_role:
        require(evidence['upgrade_attempted'] is False, 'capability upgrade already attempted')
        preferred = resolve(evidence.get('preferred'))
        require(preferred is not None, 'Stage-3 role must select an available model and effort')
        basis = text(evidence.get('preference_reason'))
        native_pairs = {(item['model'], item['effort']) for item in supported}
        eligible = stage3_eligible_defaults(
            supported, evidence['required_capability'],
            native_pairs if available_pairs is None else native_pairs & set(available_pairs))
        defaults = [item for item in supported if (item['model'], item['effort']) in eligible]
        require(not defaults or preferred in defaults,
                'available Stage-3 default configurations take precedence')
        if evidence['required_capability'] is not None and preferred['capability'] is not None:
            require(preferred['capability'] >= evidence['required_capability'],
                    'selected configuration is below the known role requirement')
        selected, source = preferred, 'role'
        reason = ('Stage-3 default pool: ' if defaults else 'Stage-3 available-model fallback: ') + basis
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
    return {**selected, 'source': source, 'reason': reason,
            'receipt': evidence['receipt'], 'needs_decision': bool(needs_decision),
            'disclose': evidence['role'] != 'execution-agent', 'upgrade_attempted': source == 'role' and evidence['frozen'] is not None}


def _transition_single(request: dict[str, Any]) -> dict[str, Any]:
    keys(request, {'schema_version', 'action', 'actor_ref', 'context', 'evidence'})
    require(type(request['schema_version']) is int and request['schema_version'] == 4, 'legacy_run_requires_original_runtime: control request schema ' + str(request.get('schema_version')))
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
    if progress and progress.get('recovery') and progress['recovery']['state'] != 'activated':
        require(action not in {'plan-execution', 'candidate-ready', 'validation-start'}, 'dispatcher recovery preparation has no write authority')
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
        require('implementation_policy' not in evidence or isinstance(evidence['implementation_policy'], dict), 'explicit policy must be an object')
        keys(evidence, {'binding', 'binding_verified', 'allowed_paths', 'protected_paths', 'authority_digest', 'testing_basis', 'validation_plan', 'configuration'} | ({'implementation_policy'} if 'implementation_policy' in evidence else set()))
        require(context['stage'] == 3 and progress is None and context['carrier'] is None, 'only one implementation dispatcher')
        require(evidence['binding_verified'] is True and isinstance(evidence['binding'], dict) and evidence['binding'], 'verified worktree required')
        validate_binding(evidence['binding'])
        allowed, protected = paths(evidence['allowed_paths']), paths(evidence['protected_paths'], empty=True)
        require(not set(allowed) & set(protected), 'protected scope overlap')
        text(evidence['testing_basis'])
        validate_validation_plan(evidence['validation_plan'], evidence['binding'])
        policy = implementation_policy(evidence.get('implementation_policy'), controller_ref=context['controller_ref'],
            requirement_identity=context['requirement_identity'], scope_digest=evidence['authority_digest'])
        if policy['mode'] == 'direct':
            require(set(policy['assessment']['checks']) <= {v['command'] for v in evidence['validation_plan']['review_required']},
                    'assessed checks must be covered by frozen review checks')
        attempt = digest(evidence)
        context['carrier'] = {'kind': 'implementation-dispatcher', 'ref': None, 'attempt': attempt}
        context['handoff_progress'] = {'state': 'dispatcher-pending', 'binding': evidence['binding'],
            'allowed_paths': allowed, 'protected_paths': protected, 'authority_digest': evidence['authority_digest'],
            'executions': [], 'candidate': None, 'tests': [], 'configuration': evidence['configuration'],
            'implementation_policy': policy,
            'validation_plan': evidence['validation_plan'], 'plan_digest': digest(evidence['validation_plan'])}
        return {'ok': True, 'context': context, 'attempt': attempt,
                'effects': [{'operation': 'spawn_native', 'role': 'implementation-dispatcher', 'configuration': evidence['configuration']}]}
    elif action == 'dispatcher-bound':
        keys(evidence, {'ref', 'attempt'})
        require(progress and progress['state'] == 'dispatcher-pending' and evidence['attempt'] == context['carrier']['attempt'], 'wrong dispatcher attempt')
        context['carrier']['ref'] = text(evidence['ref'])
        progress['state'] = 'implementing'
    elif action == 'escalate-implementation':
        keys(evidence, {'dispatcher_ref', 'attempt', 'reference', 'reason', 'assessment_reference', 'stop', 'snapshot', 'pending_paths'})
        require(progress and progress['state'] == 'implementing', 'escalation requires implementing state')
        require(evidence['dispatcher_ref'] == context['carrier']['ref'] and evidence['attempt'] == context['carrier']['attempt'],
                'escalation retains the original Dispatcher identity')
        require(direct_implementation(progress) and not progress.get('implementation_escalation') and not progress['executions'],
                'only one direct to full escalation is permitted')
        policy = progress['implementation_policy']
        require(evidence['assessment_reference'] == policy['assessment']['reference'], 'wrong direct assessment')
        text(evidence['reference']); text(evidence['reason'])
        stop = evidence['stop']
        require(stop.get('provenance') and stop.get('receipt', {}).get('status') == 'stopped' and
                stop['receipt'].get('ref') == context['carrier']['ref'] and stop.get('git_snapshot') == evidence['snapshot'],
                'escalation requires the original stopped byte snapshot')
        pending = paths(evidence['pending_paths'], empty=True)
        require(set(pending) <= set(progress['allowed_paths']), 'inherited paths escape scope')
        progress['implementation_escalation'] = {**copy.deepcopy(evidence), 'policy': copy.deepcopy(policy),
            'snapshot_digest': digest(evidence['snapshot']), 'pending_paths': pending}
        progress['implementation_policy'] = {'mode': 'full'}
        effects = [{'operation': 'continue-host', 'ref': context['carrier']['ref'], 'implementation_mode': 'full'}]
    elif action == 'plan-execution':
        keys(evidence, {'task_id', 'paths', 'read_only', 'behavior', 'tests', 'git_operations', 'configuration'} |
             (set(evidence) & {'adopt_paths', 'adoption_snapshot_digest', 'adoption_fingerprints'}))
        require(progress and progress['state'] == 'implementing', 'dispatcher is not accepting assignments')
        require(not direct_implementation(progress), 'direct implementation must escalate before execution allocation')
        text(evidence['task_id']); text(evidence['behavior'])
        assigned = paths(evidence['paths'])
        adopted = paths(evidence.get('adopt_paths', []), empty=True)
        if adopted:
            source = adoption_source(progress)
            require(source is not None and set(adopted) <= set(assigned) & set(source['pending_paths']) and
                    evidence.get('adoption_snapshot_digest') == source['snapshot_digest'], 'invalid inherited adoption scope or snapshot')
            require(evidence.get('adoption_fingerprints') == {p: source['snapshot']['files'][p] for p in adopted},
                    'adoption must bind original complete fingerprints')
        else:
            require(not evidence.get('adoption_snapshot_digest') and not evidence.get('adoption_fingerprints'),
                    'ordinary allocations cannot claim adoption evidence')
        paths(evidence['read_only'], empty=True)
        require(isinstance(evidence['tests'], list) and evidence['tests'], 'testing requirement is missing')
        require(evidence['git_operations'] == [], 'execution agents cannot mutate Git')
        require(set(assigned) <= set(progress['allowed_paths']) and not set(assigned) & set(progress['protected_paths']), 'assignment escapes implementation scope')
        require(all(e['task_id'] != evidence['task_id'] for e in progress['executions']), 'execution identity reused')
        require(all(e.get('stopped') is True or not set(e['paths']) & set(assigned) for e in progress['executions']), 'concurrent file assignment overlap')
        if progress.get('recovery'):
            historical_unaccepted = set(progress['recovery']['revalidate'])
            require(all(e['state'] == 'cancelled' or e['agent_ref'] not in historical_unaccepted or
                        not set(e['paths']) & set(assigned) for e in progress['executions']),
                    'historical recovery allocation must be accepted or explicitly released, never redispatched')
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
        keys(evidence, {'agent_ref', 'stopped', 'changed_paths', 'file_hashes', 'tests', 'git_unchanged'} |
             ({'adopted_paths'} if 'adopted_paths' in evidence else set()))
        execution = execution_record(progress, evidence['agent_ref'])
        require(execution['state'] in {'assigned', 'received'}, 'execution result requires an actual bound writer')
        require(evidence['stopped'] is True and evidence['git_unchanged'] is True, 'execution must stop without Git mutations')
        require(set(paths(evidence['changed_paths'], empty=True)) <= set(execution['paths']), 'actual diff escapes allocation')
        hashes(evidence['file_hashes'])
        adopted = paths(evidence.get('adopted_paths', []), empty=True)
        require(set(adopted) == set(execution.get('adopt_paths', [])), 'explicit allocated adoption result required')
        require(set(evidence['file_hashes']) == set(evidence['changed_paths']) | set(adopted), 'diff and adopted hashes incomplete')
        require(isinstance(evidence['tests'], list) and evidence['tests'], 'execution test evidence missing')
        execution.update(state='received', stopped=True, file_hashes=evidence['file_hashes'], tests=evidence['tests'])
        if adopted:
            execution.update(changed_paths=evidence['changed_paths'], adopted_paths=adopted)
    elif action == 'accept-execution':
        keys(evidence, {'agent_ref', 'file_hashes'})
        execution = execution_record(progress, evidence['agent_ref'])
        require(execution['state'] == 'received' and execution['stopped'] is True and
                evidence['file_hashes'] == execution['file_hashes'], 'execution bytes do not match received result')
        execution['state'] = 'accepted'
    elif action == 'prepare-dispatch-recovery':
        keys(evidence, {'dispatcher_ref', 'attempt', 'reference', 'reason', 'stop_receipts', 'call_receipts',
                        'host_evidence', 'snapshot', 'ownership', 'revalidate'})
        require(context['stage'] == 3 and context['carrier']['kind'] == 'implementation-dispatcher' and progress and
                'executions' in progress, 'recovery is only for the original Stage-3 dispatcher')
        require(evidence['dispatcher_ref'] == context['carrier']['ref'] and evidence['attempt'] == context['carrier']['attempt'],
                'recovery must retain the original dispatcher attempt')
        require(progress['state'] not in {'cancelled', 'deliverable', 'validating'},
                'ended authority or an unreconciled final validation attempt cannot recover')
        text(evidence['reference']); text(evidence['reason'])
        host = evidence['host_evidence']
        keys(host, {'stopped_refs', 'stop_receipts', 'call_receipts', 'lifecycle_digest', 'original_host'})
        required = {context['carrier']['ref']} | {e['agent_ref'] for e in progress['executions'] if e['agent_ref']}
        require(all(e['agent_ref'] is not None or e['state'] == 'cancelled' and e['stopped'] for e in progress['executions']),
                'unbound native call requires reconciliation')
        require(required <= set(host['stopped_refs']) and host['stop_receipts'] == evidence['stop_receipts'] and
                host['call_receipts'] == evidence['call_receipts'], 'complete original host evidence required')
        frozen = {**evidence, 'authority_digest': progress['authority_digest'],
                  'remaining_paths': sorted(set(progress['allowed_paths']) - set(evidence['ownership']['accepted']))}
        if direct_implementation(progress):
            frozen['direct_policy'] = copy.deepcopy(progress['implementation_policy'])
        identity = digest(frozen)
        recovery = {**frozen, 'recovery_id': identity, 'snapshot_digest': digest(evidence['snapshot']),
                    'state': 'prepared', 'decision': None, 'intent': None, 'receipts': [], 'activation': None}
        previous = progress.get('recovery')
        if previous is not None:
            if previous['recovery_id'] == identity:
                return {'ok': True, 'context': context, 'effects': [], 'recovery': previous, 'acknowledged': True}
            require(previous['state'] == 'activated', 'retain the existing recovery preparation')
            require(len(progress.setdefault('recovery_history', [])) < 32, 'recovery history bound reached')
            progress['recovery_history'].append(copy.deepcopy(previous))
        progress['recovery'] = recovery
        return {'ok': True, 'context': context, 'effects': [], 'recovery': recovery}
    elif action == 'release-recovery-allocation':
        keys(evidence, {'recovery_id', 'snapshot_digest', 'agent_ref', 'reference', 'fingerprints'})
        recovery = (progress or {}).get('recovery')
        require(recovery and recovery['state'] == 'activated' and recovery['recovery_id'] == evidence['recovery_id'] and
                recovery['snapshot_digest'] == evidence['snapshot_digest'], 'original activated recovery required')
        text(evidence['reference'])
        execution = execution_record(progress, evidence['agent_ref'])
        previous = next((r for r in recovery.get('releases', []) if r['agent_ref'] == evidence['agent_ref']), None)
        if previous:
            require(previous == evidence, 'allocation release decision conflict')
            return {'ok':True, 'context':context, 'effects':[], 'acknowledged':True}
        require(execution['stopped'] and execution['state'] in {'assigned', 'received'} and
                evidence['agent_ref'] in recovery['revalidate'], 'only stopped unaccepted historical ownership can release')
        require(evidence['fingerprints'] == {p:recovery['snapshot']['files'][p] for p in execution['paths']},
                'allocation bytes changed since Controller snapshot assumption')
        execution['state'] = 'cancelled'
        recovery.setdefault('releases', []).append(evidence)
    elif action in {'dispatch-recovery-intent', 'dispatch-recovery-result', 'recover-dispatch'}:
        recovery = (progress or {}).get('recovery')
        require(context['stage'] == 3 and recovery is not None and evidence.get('recovery_id') == recovery['recovery_id'],
                'exact prepared recovery required')
        if action == 'dispatch-recovery-result':
            keys(evidence, {'recovery_id', 'receipt'})
            receipt = evidence['receipt']
            keys(receipt, {'adapter', 'call_ref', 'response_ref', 'intent_id', 'status', 'ref', 'raw'})
            require(recovery['intent'] is not None and receipt['intent_id'] == recovery['intent']['intent_id'], 'original dispatch intent required')
            for field in ('adapter', 'call_ref', 'response_ref'):
                text(receipt[field])
            require(receipt['status'] in {'ready', 'unknown', 'not-created'}, 'invalid replacement outcome')
            previous = next((r for r in recovery['receipts'] if r['response_ref'] == receipt['response_ref']), None)
            if previous:
                require(previous == receipt, 'replacement response identity conflict')
                return {'ok': True, 'context': context, 'effects': [], 'recovery': recovery, 'acknowledged': True}
            require(recovery['state'] in {'dispatch-pending', 'dispatch-unknown'}, 'reconcile only the pending original creation')
            require(isinstance(receipt['raw'], dict) and bool(receipt['raw']), 'original native response required')
            if receipt['status'] == 'ready':
                text(receipt['ref'])
                require(receipt['ref'] not in recovery['host_evidence']['stopped_refs'] and
                        receipt['raw'].get('write_authority') is False, 'replacement must be new and prepared without write authority')
            else:
                require(receipt['ref'] is None, 'non-ready replacement cannot bind a ref')
            require(len(recovery['receipts']) < 64, 'recovery receipt bound reached')
            recovery['receipts'].append(receipt)
            recovery['state'] = {'ready':'ready', 'unknown':'dispatch-unknown', 'not-created':'not-created'}[receipt['status']]
        else:
            keys(evidence, {'recovery_id', 'snapshot_digest', 'decision'} | ({'replacement_ref', 'snapshot'} if action == 'recover-dispatch' else set()))
            decision = evidence['decision']
            keys(decision, {'reference', 'authority_digest', 'attempt', 'remaining_paths', 'assume_paths'})
            text(decision['reference'])
            require(evidence['snapshot_digest'] == recovery['snapshot_digest'] and
                    decision['authority_digest'] == recovery['authority_digest'] == progress['authority_digest'] and
                    decision['attempt'] == recovery['attempt'] and decision['remaining_paths'] == recovery['remaining_paths'] and
                    decision['assume_paths'] == recovery['ownership']['dispatcher'], 'Controller must assume the exact prepared snapshot and scope')
            require(recovery['decision'] is None or recovery['decision'] == decision, 'recovery decision cannot change')
            if action == 'dispatch-recovery-intent':
                if recovery['intent'] is not None and recovery['state'] != 'not-created':
                    return {'ok': True, 'context': context, 'effects': [], 'recovery': recovery, 'acknowledged': True}
                require(recovery['state'] in {'prepared', 'not-created'}, 'replacement is not dispatchable')
                recovery['decision'] = decision
                recovery['intent'] = {'intent_id': digest([recovery['recovery_id'], decision]),
                    'recovery_id': recovery['recovery_id'], 'snapshot_digest': recovery['snapshot_digest'],
                    'role': 'implementation-dispatcher', 'write_authority': False,
                    'configuration': progress['configuration'], 'binding': progress['binding'],
                    'authority_digest': progress['authority_digest'], 'plan_digest': progress['plan_digest'],
                    'allowed_paths': progress['allowed_paths'], 'protected_paths': progress['protected_paths'],
                    'remaining_paths': recovery['remaining_paths'], 'revalidate': recovery['revalidate'],
                    'ownership': recovery['ownership'], 'original_ref': recovery['dispatcher_ref'], 'original_attempt': recovery['attempt']}
                recovery['state'] = 'dispatch-pending'
                effects = [{'operation': 'spawn_native', 'request': recovery['intent']}]
            else:
                activation = {k:v for k,v in evidence.items() if k != 'snapshot'}
                if recovery['activation'] is not None:
                    require(recovery['activation'] == activation, 'recovery activation conflict')
                    return {'ok': True, 'context': context, 'effects': [], 'recovery': recovery, 'acknowledged': True}
                require(recovery['state'] == 'ready' and evidence['replacement_ref'] == recovery['receipts'][-1]['ref'], 'actual prepared successor required')
                require(evidence['snapshot'] == recovery['snapshot'], 'recovery snapshot drifted before activation')
                require(context['carrier']['ref'] == recovery['dispatcher_ref'] and context['carrier']['attempt'] == recovery['attempt'], 'original dispatcher changed')
                context['carrier'].update(ref=evidence['replacement_ref'], attempt=digest([recovery['recovery_id'], evidence['replacement_ref']]))
                recovery.update(state='activated', activation=activation)
                if recovery.get('direct_policy'):
                    progress['implementation_policy'] = {'mode': 'full'}
                # Previous checks are diagnostic history, never evidence for the new attempt.
                progress.setdefault('validation_history', []).append({k:copy.deepcopy(progress[k]) for k in
                    ('candidate', 'tests', 'candidate_evidence', 'review_decision', 'review_digest', 'attempts') if k in progress})
                for key in ('candidate_evidence', 'review_decision', 'review_digest', 'attempts', 'accepted_delivery'):
                    progress.pop(key, None)
                progress.update(state='implementing', candidate=None, tests=[])
                for execution in progress['executions']:
                    execution['stopped'] = True
        return {'ok': True, 'context': context, 'effects': effects, 'recovery': recovery,
                'remaining_paths': recovery['remaining_paths'], 'revalidate': recovery['revalidate']}
    elif action == 'candidate-ready':
        keys(evidence, {'dispatcher_ref', 'attempt', 'commit', 'expected_target_head', 'binding', 'plan_digest', 'checks', 'source', 'clean', 'changed_paths', 'file_hashes'} | ({'direct_provenance'} if 'direct_provenance' in evidence else set()))
        require(progress and evidence['dispatcher_ref'] == context['carrier']['ref'] and evidence['attempt'] == context['carrier']['attempt'], 'wrong dispatcher identity or attempt')
        if progress.get('candidate_evidence') is not None:
            require(progress['candidate_evidence'] == evidence, 'candidate identity cannot change; invalidate explicitly')
            return {'ok': True, 'context': context, 'effects': [], 'acknowledged': True}
        require(progress['state'] == 'implementing', 'candidate is not implementing')
        require(all(e['stopped'] and e['state'] in {'accepted', 'cancelled'} for e in progress['executions']), 'all executions must stop and be accepted')
        accepted = [e for e in progress['executions'] if e['state'] == 'accepted' and e['agent_ref'] is not None]
        if direct_implementation(progress):
            require(not progress['executions'], 'direct candidate cannot contain Execution allocations')
            proof = evidence.get('direct_provenance')
            require(isinstance(proof, dict) and proof.get('policy_digest') == digest(progress['implementation_policy']) and
                    proof.get('dispatcher_ref') == context['carrier']['ref'] and proof.get('attempt') == context['carrier']['attempt'] and
                    proof.get('baseline') == progress['git_baseline_commit'] and proof.get('candidate') == evidence['commit'],
                    'actual bound dispatcher provenance required')
            require(proof.get('stop', {}).get('receipt', {}).get('status') == 'stopped' and
                    proof['stop']['receipt'].get('ref') == context['carrier']['ref'] and proof['stop'].get('provenance'),
                    'causal native dispatcher stop required')
        else:
            require('direct_provenance' not in evidence, 'full candidates require execution provenance')
            require(accepted, 'Stage-3 candidate requires an accepted Execution Agent')
        require(evidence['clean'] is True and evidence['binding'] == progress['binding'], 'candidate must be clean in bound worktree')
        changed = set(paths(evidence['changed_paths']))
        if direct_implementation(progress):
            assessment = progress['implementation_policy']['assessment']
            require(changed <= set(assessment['implementation_paths'] + assessment['test_paths']), 'direct change escapes assessed locations')
        require(changed <= set(progress['allowed_paths']), 'candidate diff escapes scope')
        require(re.fullmatch('[0-9a-f]{40}', evidence['commit']) and evidence['plan_digest'] == progress['plan_digest'], 'candidate commit or plan changed')
        validate_checks(progress['validation_plan']['review_required'], evidence['checks'], evidence['commit'])
        validate_result_source(evidence['source'], evidence['checks'], context['carrier']['ref'])
        if direct_implementation(progress):
            require(set(proof.get('paths', [])) == changed and proof.get('fingerprints') ==
                    {p: proof['stop']['git_snapshot']['files'][p] for p in changed},
                    'direct candidate paths or fingerprints differ from its stopped snapshot')
        else:
            accepted_hashes = {}
            for execution in accepted:
                accepted_hashes.update(execution['file_hashes'])
            require(changed <= set(accepted_hashes), 'candidate contains implementation paths without accepted Execution Agent delivery')
            require(all(evidence['file_hashes'].get(p) == accepted_hashes[p] for p in changed),
                    'candidate bytes differ from accepted Execution Agent delivery')
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
        keys(evidence, {'ref', 'stage', 'input_digest', 'role', 'binding_verified', 'activated', 'confirmed', 'archive_ref', 'takeover_proof'})
        require(context['carrier'] is not None and context['carrier']['kind'] == 'dedicated-stage', 'visible task takeover requires a dedicated carrier; native stages use accepted/start')
        require(progress and progress['state'] == 'result-accepted', 'accept before successor takeover')
        require(evidence['input_digest'] == progress['delivery_digest'] and evidence['confirmed'] is True,
                'successor frozen input or confirmation mismatch')
        require((context['stage'], evidence['stage']) in {(0, 1), (0, 2), (1, 2), (2, 3), (3, 4)}, 'illegal successor stage')
        require(evidence['binding_verified'] is True and (context['topic_ref'] is None or evidence['activated'] is True),
                'successor readiness and source activation required')
        text(evidence['ref'])
        require(evidence['role'] in {1: {'dedicated-problem-framing', 'current-problem-framing'}, 2: {'solution-designer'}, 3: {'implementation-dispatcher'}, 4: {'closure-agent'}}[evidence['stage']] and evidence['ref'] != context['carrier']['ref'], 'successor role or identity mismatch')
        require(evidence['archive_ref'] == context['carrier']['ref'], 'archive target must be the current visible carrier')
        validate_takeover_proof(evidence['takeover_proof'], context['carrier'], progress['plan'])
        progress.update(state='handoff-complete', successor=evidence, archive_ref=evidence['archive_ref'])
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


def validate_takeover_proof(proof, carrier, plan):
    """Check causal evidence consistency; the existing trusted host ingress authenticates its origin."""
    def host_digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    keys(proof, {'ref', 'attempt', 'adapter', 'host_ref', 'invocation_id', 'response_id', 'stop_receipt', 'business_calls', 'business_calls_digest'})
    require(proof['ref'] == carrier['ref'] and proof['attempt'] == carrier['attempt'], 'stop proof carrier mismatch')
    for field in ('adapter', 'host_ref', 'invocation_id', 'response_id', 'business_calls_digest'):
        text(proof[field])
    observation = proof['stop_receipt']
    keys(observation, {'event_id', 'receipt', 'action_id', 'provenance', 'action_resolution'})
    receipt = observation['receipt']
    keys(receipt, {'adapter', 'receipt_ref', 'request_digest', 'attempt', 'role', 'event', 'status', 'ref', 'pending_id', 'configuration', 'raw'})
    provenance = observation['provenance']
    keys(provenance, {'call_ref', 'response_ref', 'action_id'})
    for field in ('event_id', 'action_id'):
        text(observation[field])
    for field in ('receipt_ref', 'request_digest'):
        text(receipt[field])
    require(receipt['adapter'] == proof['adapter'] and receipt['ref'] == carrier['ref'] and receipt['attempt'] == carrier['attempt'] and
            receipt['role'] == ('dedicated-discussion' if plan['stage'] == 0 else 'dedicated-problem-framing') and
            receipt['configuration'] == {key: plan['configuration'][key] for key in ('model', 'effort')} and
            receipt['status'] == 'stopped' and receipt['pending_id'] is None and receipt['event'] in {'result', 'lookup'}, 'original stopped host receipt required')
    require(isinstance(receipt['raw'], dict) and receipt['raw'], 'original raw stop response required')
    require(provenance == {'call_ref': proof['invocation_id'], 'response_ref': proof['response_id'], 'action_id': observation['action_id']}, 'stop response has no exact causal invocation')
    calls = proof['business_calls']
    keys(calls, {'invocations', 'responses', 'unresolved'})
    require(isinstance(calls['invocations'], dict) and isinstance(calls['responses'], dict) and calls['unresolved'] == [], 'unresolved business calls prevent takeover')
    for collection in ('invocations', 'responses'):
        for identity, value in calls[collection].items():
            require(isinstance(identity, str) and re.fullmatch('[0-9a-f]{64}', identity), 'invalid host call identity')
            text(value)
    if observation['action_resolution'] is not None:
        keys(observation['action_resolution'], {'action_id', 'outcome'})
        text(observation['action_resolution']['action_id'])
        require(observation['action_resolution']['outcome'] in {'completed', 'not-issued', 'cancelled'}, 'unresolved original action prevents takeover')
    require(calls['invocations'].get(host_digest([proof['adapter'], proof['invocation_id']])) == observation['action_id'], 'stop invocation is absent from reconciled calls')
    fingerprint = host_digest({'provenance': provenance, 'receipt': {key:value for key,value in receipt.items() if key != 'receipt_ref'},
                          'action_resolution': observation['action_resolution']})
    require(calls['responses'].get(host_digest([proof['adapter'], proof['response_id']])) == fingerprint and
            proof['business_calls_digest'] == host_digest(calls), 'stop response or business call reconciliation changed')


def archive_transition(context, action, evidence):
    allowed = {'handoff_id'} if action == 'archive' else {'handoff_id', 'operation_id', 'ref', 'status', 'receipt'}
    keys(evidence, allowed | ({'control_plan_id'} if 'control_plan_id' in evidence else set()) |
         ({'call_lookup'} if action == 'archive' and 'call_lookup' in evidence else set()))
    handoff_id = text(evidence['handoff_id'])
    retired = context.get('retired_handoffs', {}).get(handoff_id)
    require(retired is not None and retired['handoff_completed'], 'unknown completed handoff')
    require(evidence.get('control_plan_id', retired['plan']['plan_id']) == retired['plan']['plan_id'], 'conflicting archive selector')
    ref = retired['carrier']['ref']
    operations = retired['operations']
    result = {'ok': True, 'context': context, 'effects': [], 'handoff_id': handoff_id, 'handoff_completed': True}
    if action == 'archive':
        require('call_lookup' not in evidence or operations and operations[-1]['status'] == 'intent', 'lookup does not name a pending intent')
        if retired['archive_status'] == 'archived':
            return {**result, 'acknowledged': True}
        if operations and operations[-1]['status'] in {'intent', 'issued'}:
            pending = operations[-1]
            if 'call_lookup' in evidence:
                lookup = evidence['call_lookup']
                keys(lookup, {'operation_id', 'ref', 'adapter', 'invocation_id', 'response_id', 'status', 'raw'})
                require(lookup['operation_id'] == pending['operation_id'] and lookup['ref'] == ref and
                        lookup['status'] == 'not-issued' and pending['status'] == 'intent', 'original unissued intent proof required')
                for field in ('adapter', 'invocation_id', 'response_id'):
                    text(lookup[field])
                require(isinstance(lookup['raw'], dict) and lookup['raw'], 'original call lookup response required')
                return {**result, 'effects': [{'operation': pending['kind'], 'ref': ref,
                    'handoff_id': handoff_id, 'operation_id': pending['operation_id']}], 'reconciled_unissued': True}
            if pending['status'] == 'intent' or pending['kind'] == 'read-archive-state':
                return {**result, 'acknowledged': True, 'pending_operation_id': pending['operation_id']}
            pending['status'] = 'unknown'
        kind = 'read-archive-state' if retired['archive_status'] in {'requested', 'unknown'} else 'archive'
        sequence = len(operations) + 1
        operation_id = digest({'handoff_id': handoff_id, 'sequence': sequence, 'kind': kind, 'ref': ref})
        operations.append({'operation_id': operation_id, 'kind': kind, 'ref': ref, 'sequence': sequence,
            'predecessor': operations[-1]['operation_id'] if operations else None, 'status': 'intent', 'receipt': None, 'invocation': None})
        retired['archive_status'] = 'requested'
        result['effects'] = [{'operation': kind, 'ref': ref, 'handoff_id': handoff_id, 'operation_id': operation_id}]
        return result
    require(evidence['ref'] == ref, 'archive result target mismatch')
    matches = [op for op in operations if op['operation_id'] == evidence['operation_id']]
    require(len(matches) == 1, 'unknown archive operation')
    operation = matches[0]
    receipt = evidence['receipt']
    require(isinstance(receipt, dict), 'archive receipt required')
    keys(receipt, {'adapter', 'invocation_id', 'response_id', 'ref', 'raw', 'no_write'})
    for field in ('adapter', 'invocation_id', 'response_id'):
        text(receipt[field])
    require(receipt['ref'] == ref and isinstance(receipt['raw'], dict) and type(receipt['no_write']) is bool, 'invalid archive receipt')
    require(evidence['status'] in {'issued', 'archived', 'not-archived', 'unknown', 'failed'}, 'invalid archive status')
    invocation = {'adapter': receipt['adapter'], 'invocation_id': receipt['invocation_id'],
                  'operation_id': operation['operation_id'], 'ref': ref}
    if evidence['status'] == 'issued':
        require(receipt['raw'] == {'operation_id': operation['operation_id'], 'operation': operation['kind'], 'ref': ref}, 'archive invocation must retain exact request')
        if operation['invocation'] is not None:
            require(operation['invocation'] == {**invocation, 'receipt': receipt}, 'conflicting archive invocation')
            return {**result, 'acknowledged': True}
        require(operation is operations[-1] and operation['status'] == 'intent', 'archive intent is no longer current')
        operation.update(status='issued', invocation={**invocation, 'receipt': copy.deepcopy(receipt)})
        return result
    require(operation['invocation'] is not None and all(operation['invocation'][key] == value for key, value in invocation.items()), 'archive receipt has no matching persisted invocation')
    frozen = {k: copy.deepcopy(evidence[k]) for k in ('status', 'receipt')}
    response_conflict = False
    for previous in operations:
        previous_receipt = (previous['receipt'] or {}).get('receipt')
        if previous_receipt and (previous_receipt['adapter'], previous_receipt['response_id']) == (receipt['adapter'], receipt['response_id']):
            response_conflict = previous is not operation or previous['receipt'] != frozen
    if operation['receipt'] == frozen:
        return {**result, 'acknowledged': True}
    if response_conflict or operation['receipt'] is not None or operation is not operations[-1]:
        conflict = {'operation_id': operation['operation_id'], **frozen}
        if any(all(previous[key] == value for key, value in conflict.items()) for previous in retired['conflicts']):
            return {**result, 'ok': False, 'acknowledged': True, 'error': 'conflicting archive receipt retained'}
        retired['conflicts'].append({**conflict, 'after_sequence': len(operations)})
        if retired['archive_status'] != 'archived':
            retired['archive_status'] = 'unknown'
        return {**result, 'ok': False, 'error': 'conflicting archive receipt; exact readback required'}
    require(evidence['status'] != 'not-archived' or operation['kind'] == 'read-archive-state', 'not-archived requires readback')
    status = evidence['status']
    if status == 'failed' and not receipt['no_write']:
        status = 'unknown'
    operation.update(status=status, receipt=frozen)
    if retired['archive_status'] != 'archived':
        # A query issued before a late conflicting write receipt cannot settle
        # that conflict. Only an operation begun after it can establish state.
        barrier = max((item['after_sequence'] for item in retired['conflicts']), default=0)
        retired['archive_status'] = status if operation['sequence'] > barrier else 'unknown'
    return result


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
    require(type(request['schema_version']) is int and request['schema_version'] == 4, 'legacy_run_requires_original_runtime: control request schema')
    require(request['actor_ref'] == context['controller_ref'], 'only authenticated controller writes control checkpoints')
    if action in {'archive', 'archive-result'}:
        result = archive_transition(context, action, evidence)
        validate_context(result['context'])
        return result
    if action == 'successor-ready':
        for handoff_id, retired in context.get('retired_handoffs', {}).items():
            if retired['delivery_digest'] == evidence.get('input_digest'):
                replay = dict(evidence)
                selector = replay.pop('control_plan_id', retired['plan']['plan_id'])
                require(selector == retired['plan']['plan_id'] and replay == retired['successor'], 'conflicting successor takeover')
                return {'ok': True, 'context': context, 'effects': [], 'handoff_id': handoff_id, 'handoff_completed': True, 'acknowledged': True}
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
        selected = {k: copy.deepcopy(v) for k, v in context.items() if k not in {'successor_control', 'retired_handoffs'}}
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

    else:
        context['successor_control'] = result['context']
    if action == 'successor-ready':
        require(selected is not context.get('successor_control'), 'successor cannot retire before promotion')
        old = result['context']
        progress = old['handoff_progress']
        frozen = {k: copy.deepcopy(old[k]) for k in ('controller_ref', 'topic_ref', 'stage', 'carrier', 'requirement_identity')}
        frozen.update({k: copy.deepcopy(progress[k]) for k in ('plan', 'delivery', 'delivery_digest', 'successor')})
        frozen['handoff_completed'] = True
        handoff_id = digest({k: frozen[k] for k in ('controller_ref', 'topic_ref', 'carrier', 'delivery_digest')} | {'plan_id': frozen['plan']['plan_id']})
        history = copy.deepcopy(context.get('retired_handoffs', {}))
        require(handoff_id not in history, 'conflicting retired handoff')
        history[handoff_id] = {**frozen, 'archive_status': 'not-requested', 'operations': [], 'conflicts': []}
        context = context.get('successor_control', context)
        context.pop('successor_control', None)
        context['retired_handoffs'] = history
        result.update(handoff_id=handoff_id, handoff_completed=True)
    require(len(json.dumps(context).encode()) <= MAX_BYTES, 'control history exceeds byte limit')
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
