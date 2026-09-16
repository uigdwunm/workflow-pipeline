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
from pathlib import Path
import re
import subprocess
import sys

from workflow_control import ControlError, MAX_BYTES, keys, path, require, transition, decode_json, validate_context, execution_record


def git(repository, *arguments):
    result = subprocess.run(['git', '-C', str(repository), *arguments], capture_output=True)
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
            'index_hash': hashlib.sha256(git(repository, 'ls-files', '--stage', '-z')).hexdigest()}


def implementation_hash(repository, relative):
    target = repository / path(relative)
    require(target.resolve().is_relative_to(repository) and not target.is_symlink(), 'implementation path escapes repository')
    if not target.exists():
        return None
    require(target.is_file(), 'assignment must expand directories into exact files')
    return hashlib.sha256(target.read_bytes()).hexdigest()


def changed_paths(repository, baseline):
    tracked = git(repository, 'diff', '--name-only', '-z', commit(baseline)).decode().split('\0')
    untracked = git(repository, 'ls-files', '--others', '--exclude-standard', '-z').decode().split('\0')
    return sorted(set(tracked + untracked) - {''})


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
        all_changes = git(primary, 'diff', '--name-only', commit(payload['baseline']), merge).decode().splitlines()
        registered = git(primary, 'worktree', 'list', '--porcelain').decode().splitlines()
        branch = subprocess.run(['git', '-C', str(primary), 'show-ref', '--verify', '--quiet', 'refs/heads/' + binding['branch']], capture_output=True)
        require(branch.returncode in {0, 1}, 'cannot verify branch cleanup')
        evidence.update(ancestor_verified=True, changed_paths=all_changes,
            worktree_removed=not Path(binding['worktree']).exists() and 'worktree ' + binding['worktree'] not in registered,
            branch_removed=branch.returncode == 1)
    if action == 'start-dispatch':
        verify_binding(repository, evidence['binding'])
        evidence['binding_verified'] = True
    if action in {'plan-execution', 'execution-result', 'accept-execution', 'recover-dispatch', 'candidate'}:
        require(progress is not None, 'missing dispatcher checkpoint')
        verify_binding(repository, progress['binding'])
        require(set(changed_paths(repository, payload['baseline'])) <= set(progress['allowed_paths']), 'actual Git diff escapes implementation scope')
    if action == 'plan-execution':
        before = snapshot(repository)
        before['files'] = {p: implementation_hash(repository, p) for p in evidence['paths']}
    if action == 'execution-result':
        executions = [e for e in progress['executions'] if e['agent_ref'] == evidence['agent_ref']]
        require(len(executions) == 1 and 'git_snapshot' in executions[0], 'execution has no verified Git allocation snapshot')
        execution = executions[0]
        before = execution['git_snapshot']
        current = snapshot(repository)
        require(all(current[k] == before[k] for k in ('head', 'branch', 'index_hash')), 'executor changed Git HEAD, branch or index')
        actual = {p: implementation_hash(repository, p) for p in execution['paths']}
        evidence['file_hashes'] = {p: h for p, h in actual.items() if h != before['files'][p]}
        evidence['changed_paths'] = sorted(evidence['file_hashes'])
        evidence['git_unchanged'] = True
    if action in {'accept-execution', 'recover-dispatch'}:
        evidence['file_hashes'] = {p: implementation_hash(repository, p) for p in progress['allowed_paths']}
        if action == 'accept-execution':
            execution = execution_record(progress, evidence['agent_ref'])
            evidence['file_hashes'] = {p: evidence['file_hashes'][p] for p in execution['file_hashes']}
    if action == 'candidate':
        require(not git(repository, 'status', '--porcelain'), 'candidate working tree is dirty')
        actual_commit = snapshot(repository)['head']
        require(evidence['commit'] == actual_commit, 'candidate is not actual HEAD')
        evidence.update(clean=True, changed_paths=changed_paths(repository, payload['baseline']),
                        file_hashes={p: implementation_hash(repository, p) for p in progress['allowed_paths']})
    result = transition(request)
    if action in {'start-dispatch', 'start-closure'}:
        git(repository, 'merge-base', '--is-ancestor', commit(payload['baseline']), 'HEAD')
        result['context']['handoff_progress']['git_baseline_commit'] = payload['baseline']
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
