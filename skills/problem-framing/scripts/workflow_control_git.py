#!/usr/bin/env python3
"""Read-only Git evidence adapter for the pure Workflow Control contract.

Tool identity/termination facts still come from the authenticated native or task
adapter. This boundary verifies repository facts instead of accepting booleans
about bytes, commits, scope, or cleanup as proof.
"""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from workflow_control import ControlError, MAX_BYTES, keys, path, require, transition, decode_json, validate_context, execution_record


def git(repository, *arguments):
    require(not any(key in os.environ for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR',
            'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES')), 'alternate Git environment cannot prove recovery state')
    result = subprocess.run(['git', '--literal-pathspecs', '-C', str(repository), *arguments], capture_output=True)
    require(result.returncode == 0, 'Git evidence failed: ' + ' '.join(arguments[:2]))
    return result.stdout


def commit(value):
    require(isinstance(value, str) and re.fullmatch('[0-9a-f]{40}', value), 'full Git commit required')
    return value


def file_hash(repository, relative):
    target = repository / path(relative)
    require(target.resolve().is_relative_to(repository) and not target.is_symlink() and target.is_file(), 'document path escapes repository or is not a regular file')
    return hashlib.sha256(target.read_bytes()).hexdigest()


def verify_delivery(repository, baseline, evidence):
    identity = evidence['requirement_identity']
    relative = path(identity['path'])
    revision = commit(evidence['commit'])
    git(repository, 'merge-base', '--is-ancestor', commit(baseline), revision)
    actual = hashlib.sha256(git(repository, 'show', revision + ':' + relative)).hexdigest()
    require(actual == identity['sha256'] == file_hash(repository, relative), 'delivery commit, document and hash differ')
    evidence['verified_commit_hash'] = actual
    return evidence


def snapshot(repository):
    return {'head': git(repository, 'rev-parse', 'HEAD').decode().strip(),
            'branch': git(repository, 'symbolic-ref', '--short', 'HEAD').decode().strip(),
            'index_hash': hashlib.sha256(git(repository, 'ls-files', '--stage', '-v', '-z')).hexdigest()}


def implementation_hash(repository, relative):
    target = repository / path(relative)
    require(target.resolve().is_relative_to(repository) and not target.is_symlink(), 'implementation path escapes repository')
    require(not any(parent.is_symlink() for parent in target.parents if parent != repository and repository in parent.parents),
            'implementation path traverses a symlink')
    if not target.exists():
        return None
    require(target.is_file(), 'assignment must expand directories into exact files')
    return hashlib.sha256(target.read_bytes()).hexdigest()


def file_fingerprint(repository, relative):
    content = implementation_hash(repository, relative)
    if content is None:
        return None
    mode = (repository / relative).stat().st_mode & 0o7777
    return hashlib.sha256(json.dumps([content, mode]).encode()).hexdigest()


def committed_fingerprint(repository, revision, relative):
    record = git(repository, 'ls-tree', '-z', commit(revision), '--', path(relative))
    if not record:
        return None
    require(record.startswith((b'100644 blob ', b'100755 blob ')), 'scoped source must be a regular file')
    content = hashlib.sha256(git(repository, 'show', revision + ':' + relative)).hexdigest()
    mode = 0o755 if record.startswith(b'100755') else 0o644
    return hashlib.sha256(json.dumps([content, mode]).encode()).hexdigest()


def execution_delta(repository, progress, execution, *, accepting=False):
    require('git_snapshot' in execution, 'execution has no verified Git allocation snapshot')
    before = execution['git_snapshot']
    current = snapshot(repository)
    require(all(current[k] == before[k] for k in ('head', 'branch', 'index_hash')), 'executor changed Git HEAD, branch or index')
    actual = {p: file_fingerprint(repository, p) for p in progress['allowed_paths']}
    changed = {p: value for p, value in actual.items() if value != before['files'][p]}
    own = {p: value for p, value in changed.items() if p in execution['paths']}
    pending = set()
    for p in set(changed) - set(own):
        owners = [peer for peer in progress['executions'] if peer is not execution and
                  p in peer['paths'] and peer['agent_ref'] is not None and
                  'git_snapshot' in peer and peer['git_snapshot']['files'][p] != actual[p]]
        proven = [peer for peer in owners if peer['stopped'] and peer['state'] in {'received', 'accepted'} and
                  p in peer.get('git_result_snapshot', {}) and peer['git_result_snapshot'][p] == actual[p]]
        if proven:
            continue
        active = [peer for peer in owners if peer['state'] == 'assigned' and not peer['stopped']]
        require(active, 'full allocation delta contains unassigned or unverified changes: ' + p)
        require(not accepting, 'parallel changes require matching stopped peer evidence before acceptance: ' + p)
        pending.update(peer['agent_ref'] for peer in active)
    if accepting:
        require(own == execution.get('git_result_snapshot'), 'received execution bytes or modes changed before acceptance')
    return own, sorted(pending)


def changed_paths(repository, baseline):
    tracked = git(repository, 'diff', '--name-only', '-z', commit(baseline)).decode().split('\0')
    untracked = git(repository, 'ls-files', '--others', '--exclude-standard', '-z').decode().split('\0')
    return sorted(set(tracked + untracked) - {''})


def recovery_snapshot(repository, progress):
    """Freeze index, committed ancestry and every scoped file including absence/mode."""
    baseline = progress['git_baseline_commit']
    actual = snapshot(repository)
    target = git(repository, 'rev-parse', progress['binding']['target_branch']).decode().strip()
    git(repository, 'merge-base', '--is-ancestor', baseline, actual['head'])
    git(repository, 'merge-base', '--is-ancestor', progress['binding']['base_commit'], actual['head'])
    changed = set(changed_paths(repository, target))
    # A committed addition followed by an unstaged deletion must not cancel out
    # scope evidence. Inspect committed, staged and unstaged deltas separately.
    for arguments in (('diff', '--name-only', '-z', target, 'HEAD'),
                      ('diff', '--cached', '--name-only', '-z'), ('diff', '--name-only', '-z')):
        changed.update(filter(None, git(repository, *arguments).decode().split('\0')))
    for relative in progress['allowed_paths'] + progress['protected_paths']:
        before = committed_fingerprint(repository, baseline, relative)
        if file_fingerprint(repository, relative) != before:
            changed.add(relative)
    require(changed <= set(progress['allowed_paths']) and not changed & set(progress['protected_paths']),
            'recovery diff escapes authority or changes protected sources')
    actual.update(base=baseline, target=target,
        files={p: file_fingerprint(repository, p) for p in progress['allowed_paths'] + progress['protected_paths']})
    owners, accepted_fingerprints, revalidate = {}, {}, []
    for execution in progress['executions']:
        if execution['state'] == 'cancelled':
            continue
        require(execution.get('git_snapshot') is not None, 'allocation lacks original Git ownership evidence')
        for p in execution['paths']:
            owners[p] = execution['agent_ref']
            if execution['state'] == 'accepted':
                require('git_result_snapshot' in execution, 'accepted allocation lacks complete result fingerprint')
                accepted_fingerprints[p] = execution['git_result_snapshot'].get(p, execution['git_snapshot']['files'][p])
            else:
                accepted_fingerprints.pop(p, None)
        if execution['state'] != 'accepted':
            revalidate.append(execution['agent_ref'])
    for relative, fingerprint in accepted_fingerprints.items():
        require(actual['files'][relative] == fingerprint,
                'accepted content, existence or mode changed: ' + relative)
    accepted = set(accepted_fingerprints)
    return actual, {'accepted': sorted(accepted), 'unaccepted': sorted(set(owners) - accepted),
                    'dispatcher': sorted(changed - set(owners))}, sorted(revalidate)


def verify_execution_start(repository, progress):
    """Require every pre-allocation implementation byte to have an owner."""
    baseline = commit(progress['git_baseline_commit'])
    expected = {p: committed_fingerprint(repository, baseline, p)
                for p in progress['allowed_paths']}
    recovery = progress.get('recovery')
    historical_refs = (set(recovery['host_evidence']['stopped_refs'])
                       if recovery is not None and recovery['state'] == 'activated' else set())
    accepted_before, accepted_after, active_paths = [], [], set()
    for execution in progress['executions']:
        if execution['state'] == 'accepted':
            require('git_result_snapshot' in execution,
                    'accepted execution has no verified result snapshot')
            (accepted_before if recovery is None or execution['agent_ref'] in historical_refs
             else accepted_after).append(execution['git_result_snapshot'])
        elif execution['state'] == 'assigned' and not execution['stopped']:
            active_paths.update(execution['paths'])
        else:
            require(execution['state'] == 'cancelled' and execution['stopped'],
                    'finish or reconcile the previous allocation before another assignment')
    for fingerprints in accepted_before:
        expected.update(fingerprints)
    if recovery is not None and recovery['state'] == 'activated':
        released = {item['agent_ref'] for item in recovery.get('releases', [])}
        assumed = set(recovery['decision']['assume_paths'])
        for execution in progress['executions']:
            if execution['agent_ref'] in released:
                assumed.update(execution['paths'])
        for relative in assumed:
            expected[relative] = recovery['snapshot']['files'][relative]
    for fingerprints in accepted_after:
        expected.update(fingerprints)
    for relative, fingerprint in expected.items():
        if relative not in active_paths:
            require(file_fingerprint(repository, relative) == fingerprint,
                    'implementation changed without an accepted Execution Agent: ' + relative)


def verify_binding(repository, binding):
    result = subprocess.run([sys.executable, str(Path(__file__).with_name('supervision_protocol.py')), 'verify-worktree'],
        input=json.dumps({'binding': binding, 'platform_cwd': str(repository)}), text=True, capture_output=True)
    require(result.returncode == 0, 'Flow Worktree binding verification failed')


def verified_transition(payload):
    keys(payload, {'repository', 'baseline', 'request'})
    require(isinstance(payload['repository'], str) and Path(payload['repository']).is_absolute(), 'repository must be absolute')
    keys(payload['request'], {'schema_version', 'action', 'actor_ref', 'context', 'evidence'})
    validate_context(payload['request']['context'])
    require(isinstance(payload['request']['evidence'], dict), 'evidence must be an object')
    repository = Path(payload['repository']).resolve()
    require(repository.is_dir() and repository.is_absolute(), 'repository must exist')
    request = copy.deepcopy(payload['request'])
    action, evidence = request.get('action'), request.get('evidence', {})
    context = request.get('context', {})
    progress = context.get('handoff_progress')
    if progress and 'git_baseline_commit' in progress:
        require(payload['baseline'] == progress['git_baseline_commit'], 'frozen Git baseline changed')
    if action == 'receive':
        verify_delivery(repository, payload['baseline'], evidence)
    if action == 'start-closure':
        verify_binding(repository, evidence['binding'])
        require(git(repository, 'rev-parse', evidence['binding']['target_branch']).decode().strip() == evidence['verification']['expected_target_head'], 'target_changed')
        require(snapshot(repository)['head'] == evidence['candidate'] and not git(repository, 'status', '--porcelain'), 'closure must start at the clean accepted candidate')
    if action == 'closure-result' and evidence.get('implementation_problem') is None:
        require(progress is not None, 'missing closure checkpoint')
        binding = progress['binding']
        primary = Path(binding['repository'])
        merge = commit(evidence['merge'])
        accepted = commit(progress['candidate'])
        git(primary, 'merge-base', '--is-ancestor', accepted, merge)
        git(primary, 'merge-base', '--is-ancestor', merge, binding['target_branch'])
        closure_changes = set(git(primary, 'diff', '--name-only', accepted, merge).decode().splitlines())
        require(closure_changes <= set(progress['closure_paths']), 'closure changed implementation or protected paths')
        all_changes = git(primary, 'diff', '--name-only', commit(progress['verification']['expected_target_head']), merge).decode().splitlines()
        registered = git(primary, 'worktree', 'list', '--porcelain').decode().splitlines()
        branch = subprocess.run(['git', '-C', str(primary), 'show-ref', '--verify', '--quiet', 'refs/heads/' + binding['branch']], capture_output=True)
        require(branch.returncode in {0, 1}, 'cannot verify branch cleanup')
        evidence.update(ancestor_verified=True, changed_paths=all_changes,
            worktree_removed=not Path(binding['worktree']).exists() and 'worktree ' + binding['worktree'] not in registered,
            branch_removed=branch.returncode == 1)
    if action in {'start-dispatch', 'start-design'}:
        verify_binding(repository, evidence['binding'])
        if action == 'start-dispatch':
            evidence['binding_verified'] = True
    if action in {'plan-execution', 'assign', 'execution-result', 'accept-execution', 'prepare-dispatch-recovery', 'recover-dispatch', 'candidate-ready'}:
        require(progress is not None, 'missing dispatcher checkpoint')
        verify_binding(repository, progress['binding'])
        require(set(changed_paths(repository, git(repository, 'rev-parse', progress['binding']['target_branch']).decode().strip())) <= set(progress['allowed_paths']), 'actual Git diff escapes implementation scope')
    if action == 'prepare-dispatch-recovery':
        actual, ownership, revalidate = recovery_snapshot(repository, progress)
        evidence.update(snapshot=actual, ownership=ownership, revalidate=revalidate)
    if action == 'plan-execution':
        verify_execution_start(repository, progress)
        before = snapshot(repository)
        before['files'] = {p: file_fingerprint(repository, p) for p in progress['allowed_paths']}
    if action == 'assign':
        matches = [item for item in progress['executions']
                   if item['task_id'] == evidence['task_id'] and
                   item['allocation_digest'] == evidence['allocation_digest']]
        require(len(matches) == 1 and 'git_snapshot' in matches[0],
                'execution allocation has no verified preparation snapshot')
        allocation = matches[0]
        before = allocation['git_snapshot']
        current = snapshot(repository)
        require(all(current[key] == before[key] for key in ('head', 'branch', 'index_hash')),
                'Git state changed before Execution Agent binding')
        require(all(file_fingerprint(repository, relative) == before['files'][relative]
                    for relative in allocation['paths']),
                'assigned implementation changed before Execution Agent binding')
    if action == 'execution-result':
        execution = execution_record(progress, evidence['agent_ref'])
        result_snapshot, pending_peers = execution_delta(repository, progress, execution)
        actual_hashes = {p: implementation_hash(repository, p) for p in result_snapshot}
        require(set(evidence['changed_paths']) == set(actual_hashes) and evidence['file_hashes'] == actual_hashes,
                'reported changes must exactly match the actual allocation delta')
        evidence['git_unchanged'] = True
    if action == 'recover-dispatch':
        evidence['snapshot'] = recovery_snapshot(repository, progress)[0]
    if action == 'release-recovery-allocation':
        verify_binding(repository, progress['binding'])
        execution = execution_record(progress, evidence['agent_ref'])
        evidence['fingerprints'] = {p:file_fingerprint(repository, p) for p in execution['paths']}
    if action == 'accept-execution':
        evidence['file_hashes'] = {p: implementation_hash(repository, p) for p in progress['allowed_paths']}
        if action == 'accept-execution':
            execution = execution_record(progress, evidence['agent_ref'])
            execution_delta(repository, progress, execution, accepting=True)
            evidence['file_hashes'] = {p: evidence['file_hashes'][p] for p in execution['file_hashes']}
    if action in {'candidate-ready', 'review-start', 'review-converged', 'validation-start', 'validation-result', 'validation-retry', 'delivery-ready', 'accept-delivery'}:
        require(not set(changed_paths(repository, payload['baseline'])) & set(progress['protected_paths']), 'protected source changed')
    if action == 'candidate-ready':
        require(git(repository, 'rev-parse', progress['binding']['target_branch']).decode().strip() == evidence['expected_target_head'], 'target_changed')
        git(repository, 'merge-base', '--is-ancestor', evidence['expected_target_head'], evidence['commit'])
        require(not git(repository, 'status', '--porcelain'), 'candidate working tree is dirty')
        actual_commit = snapshot(repository)['head']
        require(evidence['commit'] == actual_commit, 'candidate is not actual HEAD')
        candidate_paths = changed_paths(repository, evidence['expected_target_head'])
        accepted_fingerprints = {}
        for execution in progress['executions']:
            if execution['state'] == 'accepted':
                require('git_result_snapshot' in execution,
                        'accepted execution has no verified result snapshot')
                accepted_fingerprints.update(execution['git_result_snapshot'])
        require(all(relative in accepted_fingerprints and
                    file_fingerprint(repository, relative) == accepted_fingerprints[relative]
                    for relative in candidate_paths),
                'candidate content or mode differs from accepted Execution Agent delivery')
        evidence.update(clean=True, changed_paths=candidate_paths,
                        file_hashes={p: implementation_hash(repository, p) for p in progress['allowed_paths']})
    if action in {'review-start', 'review-converged', 'validation-start', 'validation-result', 'validation-retry', 'delivery-ready', 'accept-delivery'}:
        require(progress is not None, 'missing implementation fixed point')
        verify_binding(repository, progress['binding'])
        require(not git(repository, 'status', '--porcelain') and snapshot(repository)['head'] == progress['candidate'], 'validation candidate changed or dirty')
        require(git(repository, 'rev-parse', progress['binding']['target_branch']).decode().strip() == progress['expected_target_head'], 'target_changed')
        require(set(changed_paths(repository, git(repository, 'rev-parse', progress['binding']['target_branch']).decode().strip())) <= set(progress['allowed_paths']), 'actual Git diff escapes implementation scope')
        if action in {'validation-start', 'validation-result'}:
            evidence['source_snapshot'] = {p:file_fingerprint(repository, p) for p in progress['allowed_paths'] + progress['protected_paths']}
    result = transition(request)
    if action in {'start-dispatch', 'start-design', 'start-closure'}:
        git(repository, 'merge-base', '--is-ancestor', commit(payload['baseline']), 'HEAD')
        result['context']['handoff_progress']['git_baseline_commit'] = payload['baseline']
    if action == 'execution-result':
        recorded = execution_record(result['context']['handoff_progress'], evidence['agent_ref'])
        recorded['git_result_snapshot'] = result_snapshot
        result['pending_peer_refs'] = pending_peers
    if action == 'plan-execution':
        result['context']['handoff_progress']['executions'][-1]['git_snapshot'] = before
    return result


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        require(len(raw) <= MAX_BYTES, 'request exceeds byte limit')
        result = verified_transition(decode_json(raw))
    except (ControlError, ValueError, KeyError, TypeError, OSError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0

if __name__ == '__main__':
    sys.exit(main())
