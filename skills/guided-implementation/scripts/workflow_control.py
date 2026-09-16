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


def validate_review(candidate, review, verification, dispatcher_ref):
    require(isinstance(candidate, str) and re.fullmatch('[0-9a-f]{40}', candidate), 'invalid reviewed candidate')
    keys(review, {'standards', 'spec'})
    reviewers = []
    for axis in ('standards', 'spec'):
        keys(review[axis], {'candidate', 'reviewer_ref', 'status'})
        require(review[axis]['candidate'] == candidate and review[axis]['status'] == 'accepted', 'review does not accept exact candidate')
        reviewers.append(text(review[axis]['reviewer_ref']))
    require(len(set(reviewers)) == 2 and dispatcher_ref not in reviewers, 'independent two-axis review required')
    keys(verification, {'candidate', 'checks'})
    require(verification['candidate'] == candidate and isinstance(verification['checks'], list) and verification['checks'], 'candidate verification missing')


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


def validate_progress(progress):
    require(isinstance(progress, dict), 'invalid control checkpoint')
    state = progress.get('state')
    if 'configuration' in progress:
        validate_selection(progress['configuration'])
    if 'executions' in progress:
        allowed = {'state', 'binding', 'allowed_paths', 'protected_paths', 'authority_digest', 'executions', 'candidate', 'tests', 'configuration', 'git_baseline_commit'}
        require(state in {'dispatcher-pending', 'implementing', 'candidate', 'cancelled'}, 'invalid dispatcher state')
        validate_binding(progress['binding'])
        paths(progress['allowed_paths']); paths(progress['protected_paths'], empty=True)
        require(isinstance(progress['executions'], list), 'executions must be a list')
        for execution in progress['executions']:
            required = {'task_id', 'paths', 'read_only', 'behavior', 'tests', 'git_operations', 'configuration', 'agent_ref', 'state', 'stopped', 'file_hashes', 'allocation_digest'}
            require(isinstance(execution, dict) and required <= set(execution) <= required | {'git_snapshot'}, 'invalid execution envelope fields')
            require(execution['state'] in {'dispatch-pending', 'assigned', 'received', 'accepted', 'cancelled'} and type(execution['stopped']) is bool, 'invalid execution state')
            validate_selection(execution['configuration'])
            text(execution['task_id']); text(execution['behavior']); text(execution['allocation_digest'])
            require(execution['agent_ref'] is None or isinstance(execution['agent_ref'], str) and execution['agent_ref'].strip(), 'invalid execution identity')
            require(execution['git_operations'] == [] and isinstance(execution['tests'], list) and execution['tests'], 'invalid execution constraints')
            if 'git_snapshot' in execution:
                keys(execution['git_snapshot'], {'head', 'branch', 'index_hash', 'files'})
                hashes(execution['git_snapshot']['files'])
                require(set(execution['git_snapshot']['files']) == set(execution['paths']), 'allocation snapshot paths mismatch')
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
        keys(plan, {'target', 'project', 'title', 'missing_context', 'configuration', 'next_step', 'archive_ref', 'gate_open', 'stage', 'controller_ref', 'topic_ref', 'requirement_identity', 'task_count', 'plan_id'})
        validate_selection(plan['configuration'])
        require(type(plan['task_count']) is int and plan['task_count'] == 1 and plan['plan_id'] == digest({k: v for k, v in plan.items() if k != 'plan_id'}), 'prepared plan digest changed')
    require(set(progress) <= allowed, 'unknown checkpoint fields')


def validate_context(context):
    keys(context, CONTEXT_FIELDS)
    require(type(context['schema_version']) is int and context['schema_version'] == 1, 'unsupported control schema')
    text(context['controller_ref'])
    if context['topic_ref'] is not None:
        text(context['topic_ref'])
    require(type(context['stage']) is int and context['stage'] in range(5), 'invalid stage')
    require(context['topic_ref'] is None or isinstance(context['topic_ref'], str), 'invalid topic')
    keys(context['preference'], {'topic_current', 'stage_current'})
    require(all(type(v) is bool for v in context['preference'].values()), 'invalid preference')
    if context['carrier'] is not None:
        keys(context['carrier'], {'kind', 'ref', 'attempt'})
        require(context['carrier']['kind'] in {'dedicated-stage', 'implementation-dispatcher', 'closure-agent'}, 'invalid carrier kind')
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


def transition(request: dict[str, Any]) -> dict[str, Any]:
    keys(request, {'schema_version', 'action', 'actor_ref', 'context', 'evidence'})
    require(type(request['schema_version']) is int and request['schema_version'] == 1, 'unsupported request schema')
    context = copy.deepcopy(request['context'])
    validate_context(context)
    require(request['actor_ref'] == context['controller_ref'], 'only authenticated controller writes control checkpoints')
    action, evidence = request['action'], copy.deepcopy(request['evidence'])
    require(isinstance(action, str), 'action must be a string')
    require(isinstance(evidence, dict), 'evidence must be an object')
    if action in {'prepare', 'start-dispatch', 'start-closure', 'plan-execution'} and 'configuration' in evidence:
        expected_role = {'prepare': 'dedicated-discussion' if context['stage'] == 0 else 'dedicated-problem-framing',
                         'start-dispatch': 'implementation-dispatcher', 'start-closure': 'closure-agent', 'plan-execution': 'execution-agent'}[action]
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
                        'next_step', 'archive_ref', 'gate_open'})
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
                'task_count': 1}
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
        keys(evidence, {'binding', 'binding_verified', 'allowed_paths', 'protected_paths', 'authority_digest', 'testing_basis', 'configuration'})
        require(context['stage'] == 3 and progress is None and context['carrier'] is None, 'only one implementation dispatcher')
        require(evidence['binding_verified'] is True and isinstance(evidence['binding'], dict) and evidence['binding'], 'verified worktree required')
        validate_binding(evidence['binding'])
        allowed, protected = paths(evidence['allowed_paths']), paths(evidence['protected_paths'], empty=True)
        require(not set(allowed) & set(protected), 'protected scope overlap')
        text(evidence['testing_basis'])
        attempt = digest(evidence)
        context['carrier'] = {'kind': 'implementation-dispatcher', 'ref': None, 'attempt': attempt}
        context['handoff_progress'] = {'state': 'dispatcher-pending', 'binding': evidence['binding'],
            'allowed_paths': allowed, 'protected_paths': protected, 'authority_digest': evidence['authority_digest'],
            'executions': [], 'candidate': None, 'tests': [], 'configuration': evidence['configuration']}
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
    elif action == 'candidate':
        keys(evidence, {'dispatcher_ref', 'commit', 'clean', 'changed_paths', 'file_hashes', 'tests', 'binding'})
        require(progress and progress['state'] == 'implementing' and evidence['dispatcher_ref'] == context['carrier']['ref'], 'only dispatcher may integrate candidate')
        require(all(e['stopped'] and e['state'] in {'accepted', 'cancelled'} for e in progress['executions']), 'all executions must stop and be accepted')
        require(evidence['clean'] is True and evidence['binding'] == progress['binding'], 'candidate must be clean in bound worktree')
        require(set(paths(evidence['changed_paths'])) <= set(progress['allowed_paths']), 'candidate diff escapes scope')
        require(re.fullmatch('[0-9a-f]{40}', evidence['commit']) and evidence['tests'], 'candidate commit and full verification required')
        accepted_hashes = {}
        for execution in progress['executions']:
            accepted_hashes.update(execution['file_hashes'])
        require(all(evidence['file_hashes'].get(p) == h for p, h in accepted_hashes.items()), 'accepted bytes differ from candidate')
        progress.update(state='candidate', candidate=evidence['commit'], tests=evidence['tests'])
    elif action == 'receive':
        keys(evidence, {'delivery_id', 'source_ref', 'attempt', 'requirement_identity', 'commit', 'verified_commit_hash'})
        require(context['carrier'] is not None and evidence['source_ref'] == context['carrier']['ref'] and
                evidence['attempt'] == context['carrier']['attempt'], 'delivery source does not match trusted creation')
        completed = evidence['requirement_identity']
        keys(completed, {'path', 'sha256', 'version'})
        require(completed['path'] == context['requirement_identity']['path'] and type(completed['version']) is int and
                completed['version'] >= context['requirement_identity']['version'], 'delivery document path or version regressed')
        require(re.fullmatch('[0-9a-f]{40}', evidence['commit']) and
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
