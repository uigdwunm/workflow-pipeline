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

from workflow_control import ControlError, MAX_BYTES, keys, path, paths, require, transition, decode_json, validate_context, execution_record, direct_implementation, adoption_source, implementation_policy, current_accepted_results


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


def index_entries(repository):
    """Retain every index stage and -v flag for each exact path."""
    require(not git(repository, 'ls-files', '-u', '-z'),
            'planning requires a resolved Git index')
    entries = {}
    raw = git(repository, 'ls-files', '--stage', '-v', '-z')
    for record in filter(None, raw.split(b'\0')):
        metadata, separator, name = record.partition(b'\t')
        require(separator and name and metadata.rsplit(b' ', 1)[-1] == b'0',
                'planning requires stage-0 index entries')
        try:
            relative = name.decode('utf-8')
        except UnicodeDecodeError as error:
            raise ControlError('planning requires UTF-8 index paths') from error
        entries.setdefault(relative, []).append(hashlib.sha256(record).hexdigest())
    require(all(len(records) == 1 for records in entries.values()),
            'planning requires one resolved index entry per path')
    return entries, hashlib.sha256(raw).hexdigest()


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



def review_fingerprints(repository, revision, paths):
    """Exact tree entries, including absence and symlink identity, for read-only review."""
    values = {}
    for relative in sorted(set(paths)):
        record = git(repository, 'ls-tree', '-z', commit(revision), '--', path(relative))
        if not record:
            values[relative] = {'type': 'absent', 'mode': None, 'sha256': None}
            continue
        header, name = record.rstrip(b'\0').split(b'\t', 1)
        mode, kind, blob = header.decode().split(' ')
        require(name.decode() == relative and kind == 'blob' and mode in {'100644', '100755', '120000'},
                'review source must be one exact file or symlink tree entry')
        body = git(repository, 'cat-file', 'blob', blob)
        values[relative] = {'type': 'symlink' if mode == '120000' else 'file',
                            'mode': mode, 'sha256': hashlib.sha256(body).hexdigest()}
    return values

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
    delivered = {**own, **{p: actual[p] for p in execution.get('adopt_paths', [])}}
    if accepting:
        require(delivered == execution.get('git_result_snapshot'), 'received execution bytes or modes changed before acceptance')
    return own, sorted(pending)


def verify_plan_read_only(repository, progress, allocation):
    """Bind implementation reads to current accepted content and mode."""
    from execution_plan import current_catalog, validate_plan_refs
    authority = progress['execution_authority']
    group = validate_plan_refs(current_catalog(authority), allocation['plan_refs'],
        allocation['paths'], allocation['read_only'], current_accepted_results(progress), authority)
    require(all(file_fingerprint(repository, relative) == fingerprint
                for relative, fingerprint in group['read_only_fingerprints'].items()),
            'accepted implementation read-only input changed')
    return group['read_only_fingerprints']


def changed_paths(repository, baseline):
    tracked = git(repository, 'diff', '--name-only', '-z', commit(baseline)).decode().split('\0')
    untracked = git(repository, 'ls-files', '--others', '--exclude-standard', '-z').decode().split('\0')
    return sorted(set(tracked + untracked) - {''})


def verified_planning_paths(repository, progress):
    """Exclude only exact activated Controller planning deltas from implementation diff."""
    planning = {}
    activated = set()
    for record in progress.get('plan_revisions', []):
        evidence = record['evidence']
        candidate = commit(evidence['planning_commit'])
        if evidence['planning_paths'] == []:
            require(evidence['git_facts']['changed_paths'] == [] and
                    candidate == evidence['snapshot']['head'],
                    'inline Controller revision changed Git bytes')
            git(repository, 'merge-base', '--is-ancestor', candidate, 'HEAD')
            continue
        parent = commit(evidence['git_facts']['parent'])
        git(repository, 'merge-base', '--is-ancestor', candidate, 'HEAD')
        parents = git(repository, 'rev-list', '--parents', '-n', '1', candidate).decode().split()
        require(parents == [candidate, parent], 'activated planning commit ancestry changed')
        actual = sorted(filter(None, git(repository, 'diff', '--no-renames', '--name-only', '-z',
                                          parent, candidate, '--').decode().split('\0')))
        require(actual == evidence['git_facts']['changed_paths'] and
                set(actual) <= set(evidence['planning_paths']),
                'activated planning commit changed outside its exact release')
        activated.add(candidate)
        for relative in actual:
            planning[relative] = committed_fingerprint(repository, candidate, relative)
    retained = progress.get('integration_retained')
    retained_revisions = set()
    retained_planning = set()
    if retained is not None:
        retained_paths = retained_closure_paths(repository, progress)
        retained_commit = commit(retained['commit'])
        require(commit(progress['integration_candidate']) == retained_commit,
                'retained closure commit differs from integration candidate')
        history = progress.get('validation_history', [])
        accepted = commit(history[-1]['candidate']) if history and history[-1].get('candidate') else retained_commit
        accepted_paths = set(history[-1].get('accepted_paths', [])) if history else set()
        if accepted_paths:
            # Only an exact committed acceptance can exempt documents inherited from an earlier repair.
            import workflow_progress
            proof = history[-1].get('proof_ref')
            require(isinstance(proof, dict), 'retained closure needs its exact accepted proof')
            facts, current, store = workflow_progress.read_implementation_delivery(proof['checkpoint_path'], proof)
            previous = store.read_evidence(facts['git_evidence_ref'], run_id=current.run_id)['value']
            previous_retained = previous.get('integration_retained') or {}
            require(facts['payload']['candidate_commit'] == accepted and
                    facts['execution_authority'] == progress['execution_authority'] and
                    set(previous_retained.get('paths', [])) == accepted_paths and
                    accepted_paths <= set(progress['execution_authority']['scope']['closure_paths']) and
                    all(committed_fingerprint(repository, accepted, path) ==
                        committed_fingerprint(repository, previous_retained['commit'], path)
                        for path in accepted_paths), 'retained closure proof differs from accepted bytes or scope')
        git(repository, 'merge-base', '--is-ancestor', accepted, retained_commit)
        git(repository, 'merge-base', '--is-ancestor', retained_commit, 'HEAD')
        actual = set(filter(None, git(repository, 'diff', '--no-renames', '--name-only', '-z',
                                      accepted, retained_commit).decode().split('\0')))
        require(actual | accepted_paths == retained_paths and
                incoming_changed_paths(repository, accepted, retained_commit) <= retained_paths and
                retained_paths <= set(progress['execution_authority']['scope']['closure_paths']),
                'retained closure history escapes its exact document paths')
        from execution_plan import _committed_file, collect_execution_plan, current_catalog
        authority = progress['execution_authority']
        catalog = current_catalog(authority)
        if authority.get('kind') == 'inline':
            require(catalog['commit'] is None and 'sources' not in catalog and
                    len(catalog['tickets']) == 1 and
                    catalog['tickets'][0]['source']['kind'] == 'inline',
                    'inline planning source shape changed')
            planning_sources = {}
        else:
            require(catalog['commit'] is not None and
                    isinstance(catalog.get('sources'), list) and bool(catalog['sources']),
                    'staged planning sources missing')
            planning_sources = {source['path']: source for source in catalog['sources']}
        retained_planning = retained_paths & set(planning_sources)
        retained_revisions = set(git(repository, 'rev-list',
                                     accepted + '..' + retained_commit).decode().splitlines())
        if retained_planning:
            source_paths = sorted(planning_sources)
            original_plans = {ticket['ticket_id']: ticket['plan'] for ticket in catalog['tickets']}
            record_baseline = {relative: _committed_file(repository, catalog['commit'], relative)['body']
                               for relative in retained_planning}
            for revision in retained_revisions:
                changed = set(filter(None, git(repository, 'diff', '--no-renames', '--name-only', '-z',
                    revision + '^1', revision).decode().split('\0')))
                if not changed & retained_planning:
                    continue
                checked = collect_execution_plan(str(repository), revision, source_paths,
                    progress['execution_authority']['scope'], catalog['requirement_identity'],
                    retained_record_baseline=record_baseline)
                require(checked['contract_digest'] == catalog['contract_digest'] and
                        {ticket['ticket_id']: ticket['plan'] for ticket in checked['tickets']} == original_plans and
                        all(source['mode'] == planning_sources[source['path']]['mode']
                            for source in checked['sources']),
                        'retained closure changed the current planning contract or function plan')
    for relative, expected in planning.items():
        if relative in retained_planning:
            expected = committed_fingerprint(repository, retained['commit'], relative)
        require(file_fingerprint(repository, relative) == expected,
                'activated planning bytes changed outside Controller window: ' + relative)
    for relative in set(planning) | retained_planning:
        tree = git(repository, 'ls-tree', '-z', 'HEAD', '--', relative)
        index = git(repository, 'ls-files', '--stage', '-v', '-z', '--', relative)
        header, separator, name = tree.partition(b'\t')
        fields = header.split(b' ')
        require(separator and name == relative.encode() + b'\0' and len(fields) == 3 and
                index == b'H ' + fields[0] + b' ' + fields[2] + b' 0\t' + name,
                'current planning Git index differs from HEAD: ' + relative)
    if planning or retained_planning:
        for revision in git(repository, 'rev-list', progress['git_baseline_commit'] + '..HEAD').decode().splitlines():
            changed = set(filter(None, git(repository, 'diff', '--no-renames', '--name-only', '-z',
                                          revision + '^1', revision).decode().split('\0')))
            require(not changed & (set(planning) | retained_planning) or
                    revision in activated | retained_revisions,
                    'unapproved commit changed protected planning bytes')
    return set(planning)


def retained_closure_paths(repository, progress):
    retained = progress.get('integration_retained') or {'paths':[]}
    for relative in retained['paths']:
        require(file_fingerprint(repository, relative) == committed_fingerprint(repository, retained['commit'], relative),
                'retained closure document changed during implementation recovery: ' + relative)
    return set(retained['paths'])


def incoming_changed_paths(repository, target, candidate):
    """Paths introduced by incoming commits, including later reversions."""
    changed = set()
    revisions = git(repository, 'rev-list', '--parents', commit(target) + '..' + commit(candidate)).decode().splitlines()
    for line in revisions:
        revision, *parents = line.split()
        require(parents, 'incoming history has an unrelated root')
        introduced = set(filter(None, git(repository, 'diff', '--no-renames', '--name-only', '-z',
            parents[0], revision, '--').decode().split('\0')))
        for parent in parents[1:]:
            introduced.intersection_update(filter(None, git(repository, 'diff', '--no-renames',
                '--name-only', '-z', parent, revision, '--').decode().split('\0')))
        changed.update(introduced)
    return changed


def verified_retired_history_paths(repository, progress, candidate, historical_paths, controller_ref):
    """Admit a retired helper only through its original accepted, committed lifecycle."""
    records = progress.get('plan_revisions', [])
    sources = progress.get('plan_revision_sources', [])
    require(len(records) == len(sources), 'plan revision history lacks original execution receipts')
    baseline = commit(progress['git_baseline_commit'])
    current = set(progress['allowed_paths'])
    protected = set(progress['protected_paths'])
    admitted = set()
    for relative in historical_paths - current - protected:
        original = committed_fingerprint(repository, baseline, relative)
        if (committed_fingerprint(repository, candidate, relative) != original or
                file_fingerprint(repository, relative) != original):
            continue
        for index, (record, source) in enumerate(zip(records, sources)):
            evidence = record['evidence']
            if (record['controller_ref'] != controller_ref or
                    source['revision_id'] != evidence['revision_id'] or
                    any(prior['revision_id'] != original['evidence']['revision_id']
                        for prior, original in zip(sources[:index], records[:index])) or
                    relative in evidence['new_scope']['implementation_paths'] or
                    relative in evidence['planning_paths'] or
                    evidence['snapshot']['files'].get(relative) != original):
                continue
            retirement = commit(evidence['snapshot']['head'])
            git(repository, 'merge-base', '--is-ancestor', retirement,
                commit(evidence['planning_commit']))
            git(repository, 'merge-base', '--is-ancestor',
                commit(evidence['planning_commit']), candidate)
            if relative in incoming_changed_paths(repository, retirement, candidate):
                continue
            accepted = [item for prior in sources[:index + 1]
                        for item in prior['executions']
                        if relative in item['paths'] and item['state'] == 'accepted' and
                           item['stopped'] is True and 'git_snapshot' in item and
                           'git_result_snapshot' in item]
            changes = [(item['git_snapshot']['head'], item['git_snapshot']['files'][relative],
                        item['git_result_snapshot'].get(relative,
                            item['git_snapshot']['files'][relative])) for item in accepted]
            changes = [change for change in changes if change[1] != change[2]]
            if len(changes) < 2 or changes[0][1] != original or changes[-1][2] != original:
                continue
            revisions = []
            for line in git(repository, 'rev-list', '--reverse', '--parents',
                            baseline + '..' + retirement).decode().splitlines():
                revision, *parents = line.split()
                if not parents or committed_fingerprint(repository, revision, relative) == \
                        committed_fingerprint(repository, parents[0], relative):
                    continue
                revisions.append((revision, parents))
            if len(revisions) != len(changes):
                continue
            if all(len(parents) == 1 and parent == start and
                   committed_fingerprint(repository, parent, relative) == before and
                   committed_fingerprint(repository, revision, relative) == after
                   for (revision, parents), (start, before, after) in zip(revisions, changes)
                   for parent in parents):
                admitted.add(relative)
                break
    return admitted


def recovery_snapshot(repository, progress):
    """Freeze index, committed ancestry and every scoped file including absence/mode."""
    baseline = progress['git_baseline_commit']
    actual = snapshot(repository)
    target = git(repository, 'rev-parse', progress['binding']['target_branch']).decode().strip()
    git(repository, 'merge-base', '--is-ancestor', baseline, actual['head'])
    git(repository, 'merge-base', '--is-ancestor', progress['binding']['base_commit'], actual['head'])
    # Unmerged target additions are not deletions authored by this dispatcher.
    scope_base = git(repository, 'merge-base', target, actual['head']).decode().strip()
    planning = verified_planning_paths(repository, progress)
    changed = set(changed_paths(repository, scope_base))
    # A committed addition followed by an unstaged deletion must not cancel out
    # scope evidence. Inspect committed, staged and unstaged deltas separately.
    for arguments in (('diff', '--name-only', '-z', scope_base, 'HEAD'),
                      ('diff', '--cached', '--name-only', '-z'), ('diff', '--name-only', '-z')):
        changed.update(filter(None, git(repository, *arguments).decode().split('\0')))
    if direct_implementation(progress) or 'design_input' in progress:
        # Final-tree diffs cannot reveal a committed out-of-scope addition
        # followed by a separate commit that removes it again.
        revisions = git(repository, 'rev-list', baseline + '..HEAD').decode().splitlines()
        for revision in revisions:
            changed.update(filter(None, git(repository, 'diff', '--no-renames', '--name-only', '-z',
                revision + '^1', revision).decode().split('\0')))
    for relative in progress['allowed_paths'] + progress['protected_paths']:
        before = committed_fingerprint(repository, baseline, relative)
        if relative not in planning and file_fingerprint(repository, relative) != before:
            changed.add(relative)
    changed.difference_update(planning)
    require(changed <= set(progress['allowed_paths']) and not changed & set(progress['protected_paths']),
            'recovery diff escapes authority or changes protected sources')
    actual.update(base=baseline, target=target,
        files={p: file_fingerprint(repository, p) for p in progress['allowed_paths'] + progress['protected_paths']})
    if 'design_input' in progress:
        return actual, {'designer': sorted(changed)}, []
    owners, accepted_fingerprints, revalidate = {}, {}, []
    from execution_plan import accepted_owned_fingerprints
    for result in ([] if progress.get('integration_source') is not None else progress.get('carried_results', [])):
        fingerprints = accepted_owned_fingerprints(result)
        require(fingerprints is not None, 'carried execution lacks verified owned fingerprints')
        for relative, fingerprint in fingerprints.items():
            owners[relative] = result['agent_ref']
            accepted_fingerprints[relative] = fingerprint
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


def scope_revision_snapshot(repository, baseline, protected_paths, proposed_paths):
    """Require an unchanged published workspace before planning correction."""
    repository = Path(repository)
    baseline = commit(baseline)
    protected, proposed = (set(paths(value, empty=True)) for value in
                           (protected_paths, proposed_paths))
    require(not proposed & protected, 'scope correction includes protected source')
    actual = snapshot(repository)
    require(actual['head'] == baseline and not git(repository, 'status', '--porcelain'),
            'scope correction requires the unchanged clean planning baseline')
    files = {relative: file_fingerprint(repository, relative)
             for relative in sorted(protected | proposed)}
    return {**actual, 'baseline': baseline, 'files': files,
            'proposed_paths': sorted(proposed)}


def plan_revision_snapshot(repository, authority, protected_paths, planning_paths,
                           additional_paths=()):
    """Freeze the original implementation/index partitions before a planning write."""
    repository = Path(repository)
    actual = snapshot(repository)
    allowed = set(paths(authority['scope']['implementation_paths'], empty=True))
    allowed.update(paths(list(additional_paths), empty=True))
    protected = set(paths(protected_paths, empty=True))
    planning = set(paths(planning_paths, empty=authority.get('kind') == 'inline'))
    require(planning <= protected and planning <= set(authority['scope']['closure_paths']),
            'planning release escapes closure-owned protected paths')
    git(repository, 'merge-base', '--is-ancestor', commit(authority['candidate_commit']), actual['head'])
    if planning:
        require(not git(repository, 'status', '--porcelain', '-z', '--', *sorted(planning)),
                'planning source was edited or staged before the Controller release')
    require(all(file_fingerprint(repository, relative) ==
                committed_fingerprint(repository, authority['candidate_commit'], relative)
                for relative in protected),
            'protected source differs from the current sealed authority')
    entries, index_hash = index_entries(repository)
    require(actual['index_hash'] == index_hash,
            'Git index changed while freezing the planning snapshot')
    require(all(relative in entries for relative in planning),
            'planning source has no resolved index entry')
    actual['index_entries'] = entries
    actual['files'] = {relative: file_fingerprint(repository, relative)
                       for relative in sorted(allowed | protected)}
    actual['implementation_paths'] = sorted(allowed)
    actual['planning_paths'] = sorted(planning)
    return actual


def verify_plan_revision(repository, stopped, planning_commit):
    """Derive the exact one-commit planning delta without touching the index."""
    repository = Path(repository)
    candidate = commit(planning_commit)
    current = snapshot(repository)
    require(current['head'] == candidate and current['branch'] == stopped['branch'],
            'planning HEAD or branch differs from the stopped original Flow')
    parents = git(repository, 'rev-list', '--parents', '-n', '1', candidate).decode().split()
    require(parents == [candidate, stopped['head']],
            'planning candidate must have the frozen HEAD as its only parent')
    planning = set(stopped['planning_paths'])
    changed = set(filter(None, git(repository, 'diff', '--no-renames', '--name-only', '-z',
                                   stopped['head'], candidate, '--').decode().split('\0')))
    require(changed and changed <= planning, 'planning commit changed unapproved files')
    entries, index_hash = index_entries(repository)
    require(current['index_hash'] == index_hash,
            'Git index changed while verifying the planning commit')
    require({p: value for p, value in entries.items() if p not in planning} ==
            {p: value for p, value in stopped['index_entries'].items() if p not in planning},
            'implementation or unrelated Git index stages or flags changed during planning')
    require(all(file_fingerprint(repository, p) == before for p, before in stopped['files'].items()
                if p not in planning),
            'implementation or protected bytes changed during planning')
    require(all(file_fingerprint(repository, p) == committed_fingerprint(repository, candidate, p)
                for p in planning), 'planning worktree bytes differ from the planning commit')
    return {'parent': stopped['head'], 'commit': candidate,
            'changed_paths': sorted(changed), 'index_partition_unchanged': True,
            'implementation_partition_unchanged': True}


def verify_inline_plan_revision(repository, stopped, planning_commit):
    """An inline revision changes Controller metadata while preserving every Git byte."""
    repository = Path(repository)
    candidate = commit(planning_commit)
    require(stopped['planning_paths'] == [] and candidate == stopped['head'],
            'inline revision must keep the stopped HEAD and have no planning paths')
    current = snapshot(repository)
    require(all(current[key] == stopped[key] for key in ('head', 'branch', 'index_hash')),
            'inline revision changed Git HEAD, branch or index')
    require(all(file_fingerprint(repository, relative) == fingerprint
                for relative, fingerprint in stopped['files'].items()),
            'inline revision changed implementation or protected bytes')
    return {'parent': stopped['head'], 'commit': candidate, 'changed_paths': [],
            'index_partition_unchanged': True, 'implementation_partition_unchanged': True}


def verify_stopped_snapshot(repository, stopped, current):
    """Keep stopped bytes fixed while allowing an ordinary target advance."""
    require(isinstance(stopped, dict) and isinstance(current, dict) and set(stopped) == set(current),
            'stopped snapshot fields changed')
    require(all(stopped[key] == current[key] for key in stopped if key != 'target'),
            'stopped implementation bytes or Git state changed')
    old_target, new_target = commit(stopped['target']), commit(current['target'])
    git(repository, 'merge-base', '--is-ancestor', old_target, new_target)


def verify_execution_start(repository, progress):
    """Require every pre-allocation implementation byte to have an owner."""
    baseline = commit(progress['git_baseline_commit'])
    expected = {p: committed_fingerprint(repository, baseline, p)
                for p in progress['allowed_paths']}
    if progress.get('integration_source') is not None:
        expected = {p: progress['integration_source']['snapshot']['files'][p]
                    for p in progress['allowed_paths']}
    else:
        revision_source = progress.get('revalidation_source')
        if revision_source is not None:
            require(revision_source['authority_digest'] == progress['plan_authority']['authority_digest'],
                    'plan revision adoption belongs to an older authority')
            expected.update({p: revision_source['snapshot']['files'][p]
                             for p in revision_source['pending_paths']})
        for result in progress.get('carried_results', []):
            from execution_plan import accepted_owned_fingerprints
            fingerprints = accepted_owned_fingerprints(result)
            require(fingerprints is not None, 'carried execution lacks verified owned fingerprints')
            expected.update(fingerprints)
    escalation = None if progress.get('integration_source') is not None else progress.get('implementation_escalation')
    if escalation is not None:
        inherited = escalation['snapshot']
        pending = set(escalation['pending_paths'])
        target = baseline
        if not progress['executions']:
            current = recovery_snapshot(repository, progress)[0]
            if {k:v for k,v in current.items() if k != 'target'} != {k:v for k,v in inherited.items() if k != 'target'}:
                # Dispatcher integration may change HEAD/index after escalation,
                # but cannot replace or accept any inherited implementation bytes.
                require(current['branch'] == inherited['branch'] and current['base'] == inherited['base'] and
                        all(current['files'][p] == inherited['files'][p] for p in pending) and
                        all(current['files'][p] == committed_fingerprint(repository, target, p)
                            for p in progress['allowed_paths'] if p not in pending),
                        'initial inherited snapshot changed before allocation')
                git(repository, 'merge-base', '--is-ancestor', inherited['head'], current['head'])
        expected.update({p: committed_fingerprint(repository, target, p)
                         for p in progress['allowed_paths'] if p not in pending})
        expected.update({p: inherited['files'][p] for p in pending})
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
        input=json.dumps({'binding': binding, 'platform_cwd': str(repository)}), text=True, encoding='utf-8', capture_output=True)
    require(result.returncode == 0, 'Flow Worktree binding verification failed')
    require(len(result.stdout.encode()) <= MAX_BYTES, 'Flow Worktree verifier output exceeds limit')
    try:
        report = decode_json(result.stdout.encode())
    except (ValueError, UnicodeError) as error:
        raise ControlError('Flow Worktree verifier returned invalid JSON') from error
    keys(report, {'binding', 'ok', 'current_commit', 'verified'})
    require(report['ok'] is True and report['verified'] is True and report['binding'] == binding,
            'Flow Worktree verifier returned a different binding')
    commit(report['current_commit'])
    return report


def verify_closure_delivery(progress):
    """Authenticate a finite Stage-4 view against its original committed C proof."""
    require(progress is None or isinstance(progress, dict), 'structured closure progress required')
    verification = (progress or {}).get('verification') or {}
    require(isinstance(verification, dict), 'structured closure verification required')
    if verification.get('kind') != 'implementation-verification-ref':
        return
    keys(verification, {'kind', 'predecessor', 'review_ref', 'verification_ref', 'expected_target_head',
                        'plan_authority', 'controller_ref', 'requirement_identity', 'view'})
    import workflow_progress
    expected = workflow_progress.closure_delivery_view(verification['predecessor'])
    require(verification == expected, 'closure view differs from the exact committed implementation proof')


def verified_transition(payload):
    keys(payload, {'repository', 'baseline', 'request'})
    require(isinstance(payload['repository'], str) and Path(payload['repository']).is_absolute(), 'repository must be absolute')
    keys(payload['request'], {'schema_version', 'action', 'actor_ref', 'context', 'evidence'})
    validate_context(payload['request']['context'])
    require(isinstance(payload['request']['evidence'], dict), 'evidence must be an object')
    if payload['request']['context'].get('stage') == 4:
        verify_closure_delivery(payload['request']['context'].get('handoff_progress'))
        if payload['request']['action'] == 'start-closure':
            verify_closure_delivery(payload['request']['evidence'])
    repository = Path(payload['repository']).resolve()
    require(repository.is_dir() and repository.is_absolute(), 'repository must exist')
    request = copy.deepcopy(payload['request'])
    action, evidence = request.get('action'), request.get('evidence', {})
    context = request.get('context', {})
    progress = context.get('handoff_progress')
    if progress and 'git_baseline_commit' in progress:
        require(payload['baseline'] == progress['git_baseline_commit'], 'frozen Git baseline changed')
    if action in {'local-review', 'ticket-accept'}:
        import workflow_state_store as store_module
        import review_evidence
        checkpoint_path = evidence.get('claim_checkpoint')
        require(isinstance(checkpoint_path, str) and Path(checkpoint_path).is_absolute() and
                str(Path(checkpoint_path).resolve()) == checkpoint_path,
                'local claim requires its canonical Controller checkpoint')
        store = store_module.open_store(checkpoint_path)
        current = store.open_current()
        metadata = current.root['metadata']
        require(metadata['stage'] == 3 and metadata['controller_ref'] == context['controller_ref'] and
                metadata['binding'] == progress['binding'], 'local claim belongs to another Controller')
        original = store.read_subject(current, metadata['stage_subject']).value['control']
        if original.get('discussion') is None:
            recorded_context = original['context']
        else:
            recorded_context, _ = store_module.open_store(original['store_ref']['ledger_path']).read_ledger_control(original['store_ref'])
        require(recorded_context == context, 'local claim requires the current original C authority')
        view = evidence['claim']
        claim = store.read_evidence(view['claim_ref'], run_id=current.run_id,
            maximum=store_module.SUBJECT_LIMIT)['value']['review_claim']
        review_evidence.validate_claim(claim)
        require(review_evidence.claim_view(claim, view['claim_ref']) == view and
                claim['run_id'] == current.run_id and claim['package_digest'] == store_module.entry.digest(metadata['packages']) and
                claim['plan_authority'] == progress['plan_authority'], 'local claim identity or package changed')
        ticket = store.read_subject(current, 'ticket:' + claim['ticket_id'] + ':0').value
        slot = ticket.get('review_activity', {}).get(evidence['axis'])
        require(slot is not None and slot['stopped'] and slot['start']['applied'] and
                (slot.get('query') is None or slot['query']['resolved']) and
                slot['candidate'] == claim['source_candidate'] == evidence['candidate'] and
                slot['ref'] == claim['reviewer_ref'] and slot['action_id'] == claim['native_stop']['action_id'],
                'local claim lacks its committed original native review slot')
        stop = store.read_evidence(claim['native_stop_ref'], run_id=current.run_id)['value']['native_review_stop']
        require(stop in slot['receipts'] and stop['response_ref'] == evidence['result_ref'] and
                all(stop[key] == claim['native_stop'][key] for key in ('adapter', 'call_ref', 'response_ref', 'status', 'ref')),
                'local claim stop evidence does not match its original receipt')
        projection = slot['subject']
        from execution_plan import current_catalog
        required = review_evidence.validate_local_verification(current_catalog(progress['execution_authority']),
            evidence['axis'], slot['prepare']['verification'], evidence['candidate'], progress['plan_authority'],
            progress['binding']['worktree'], current.run_id, store_module.entry.digest(metadata['packages']),
            context['carrier']['ref'])
        source = store.read_evidence(claim['verification']['source_ref'], run_id=current.run_id,
            maximum=store_module.SUBJECT_LIMIT)['value']['local_check_source']
        require(source == slot['prepare']['verification']['source'] and
                store_module.entry.digest(source) == claim['verification']['source_digest'] and
                store_module.entry.digest(source['raw']['environment']) == claim['verification']['environment_digest'] and
                store_module.entry.digest(source['raw']['fixture']) == claim['verification']['fixture_digest'] and
                claim['verification']['required'] == required,
                'local claim changed its original command, environment or fixture proof')
        source_authority = store.read_evidence(claim['verification']['source_authority_ref'],
            run_id=current.run_id)['value']['review_source_authority']
        issuer = store.read_evidence(source_authority['issuer_ref'], run_id=current.run_id)['value']['review_issuer']
        original_dispatcher = review_evidence.validate_original_source(claim, source, source_authority, issuer)
        require(original_dispatcher == (context['carrier']['ref'], context['carrier']['attempt']) and
                source_authority['issuer_ref'] == slot['issuer_ref'] and
                source_authority['request_digest'] == store_module.entry.digest(slot['prepare']),
                'local claim changed its original C Dispatcher or prepared request')
        verify_review_native_anchor(store, current, claim, source_authority, stop, slot=slot)
        require(all(claim[key] == value for key, value in projection.items() if key != 'verification_refs') and
                claim['verification']['ids'] == projection['verification_refs'] and
                claim['verification']['checks'] == slot['prepare']['verification']['checks'],
                'local claim changed its original contract, reads or verification')
        observed = store.read_evidence(source_authority['stop_request_ref'], run_id=current.run_id)['value']
        report = read_review_semantic(store, current, stop, observed, source_authority['stage_subject'])
        require(isinstance(report, dict) and report.get('status') == 'accepted' and report.get('findings') == [] and
                report.get('checks') == claim['verification']['checks'] and
                report.get('ac_refs') == claim['ac_refs'] and
                set(report.get('read_paths', [])) == set(claim['read_fingerprints']) and
                report.get('dependency_claim_ids', []) == claim['dependency_claim_ids'] and
                report.get('requirement_refs', []) == claim['requirement_refs'],
                'original native reviewer did not accept this exact local contract')
        owned, reads = claim['owned_fingerprints'], claim['read_fingerprints']
        actual = review_fingerprints(repository, evidence['candidate'], set(owned) | set(reads))
        require(actual == owned | reads and snapshot(repository)['head'] == evidence['candidate'] and
                not git(repository, 'status', '--porcelain'), 'local reviewed source snapshot changed')
    if action == 'candidate-ready':
        verify_review_coverage(evidence['ticket_coverage'], progress, delivery=False)
    if action == 'review-converged':
        verify_review_coverage(evidence['coverage'], progress, delivery=True, extra=evidence['claims'])
        for axis in ('standards', 'spec'):
            view = evidence['claims'][axis]
            report = evidence['review'][axis]
            require(view['source_candidate'] == report['candidate'] and view['reviewer_ref'] == report['reviewer_ref'] and
                    view['result_ref'] == report['result_ref'], 'delivery report cannot relabel an original claim')
    if action in {'delivery-ready', 'accept-delivery'} and progress and progress.get('coverage'):
        verify_review_coverage(progress['coverage'], progress, delivery=True)
    if action == 'receive':
        verify_delivery(repository, payload['baseline'], evidence)
    if action == 'plan-revision-activate':
        require(progress is not None, 'missing dispatcher checkpoint')
        verify_binding(repository, progress['binding'])
        require(evidence['planning_paths'] == evidence['snapshot']['planning_paths'],
                'planning release paths changed')
        derived = (verify_inline_plan_revision if evidence['planning_paths'] == [] else
                   verify_plan_revision)(repository, evidence['snapshot'], evidence['planning_commit'])
        require(evidence['git_facts'] == derived, 'planning Git facts differ from actual commit')
        require(progress is not None and evidence['baseline_fingerprints'] ==
                {relative: committed_fingerprint(repository, progress['git_baseline_commit'], relative)
                 for relative in evidence['new_scope']['implementation_paths']},
                'plan revision baseline fingerprints differ from original implementation baseline')
        if progress.get('review_claims'):
            verify_plan_revision_reviews(evidence, progress)
    if action == 'capture-integration':
        require(progress is not None and 'integration_target_head' in progress,
                'final integration recovery is missing')
        verify_binding(repository, progress['binding'])
        target, accepted = progress['integration_target_head'], progress['integration_candidate']
        actual = snapshot(repository)
        git(repository, 'merge-base', '--is-ancestor', accepted, actual['head'])
        merging = git(repository, 'rev-parse', '--git-path', 'MERGE_HEAD').decode().strip()
        merging = Path(merging) if Path(merging).is_absolute() else repository / merging
        if merging.exists():
            require(actual['head'] == accepted and merging.read_text().strip() == target,
                    'unresolved merge differs from the final integration target')
        else:
            git(repository, 'merge-base', '--is-ancestor', target, actual['head'])
        retained = retained_closure_paths(repository, progress)
        planning = verified_planning_paths(repository, progress)
        pending = sorted(set(changed_paths(repository, target)) - retained - planning)
        require(set(pending) <= set(progress['allowed_paths']) and not set(pending) & set(progress['protected_paths']),
                'final integration changes escape implementation authority')
        for relative in set(progress['protected_paths']) - retained - planning:
            require(file_fingerprint(repository, relative) == committed_fingerprint(repository, payload['baseline'], relative),
                    'final integration changed protected source: ' + relative)
        actual['files'] = {p: file_fingerprint(repository, p) for p in progress['allowed_paths'] + progress['protected_paths']}
        evidence.update(snapshot=actual, pending_paths=pending)
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
        all_changes = git(primary, 'diff', '--name-only', commit(progress['verification']['expected_target_head']), merge).decode().splitlines()
        import supervision_protocol as supervision
        published = git(primary, 'rev-parse', merge + '^2').decode().strip()
        git(primary, 'merge-base', '--is-ancestor', accepted, published)
        try:
            disposition = supervision.verify_disposition(binding, published, merge)
        except supervision.ProtocolError as error:
            raise ControlError('worktree disposition verification failed: ' + error.message) from error
        evidence.update(ancestor_verified=True, changed_paths=all_changes, **disposition)
    if action in {'start-dispatch', 'start-design'}:
        verify_binding(repository, evidence['binding'])
        if action == 'start-dispatch':
            policy = implementation_policy(evidence.get('implementation_policy'), baseline=payload['baseline'])
            if policy['mode'] == 'direct':
                require(snapshot(repository)['head'] == payload['baseline'] and not git(repository, 'status', '--porcelain'),
                        'direct dispatch requires the clean original baseline; unaccepted bytes require full')
                assessment = policy['assessment']
                require(set(assessment['implementation_paths'] + assessment['test_paths']) <= set(evidence['allowed_paths']),
                        'direct assessment paths escape implementation scope')
            evidence['binding_verified'] = True
    if action in {'plan-execution', 'repair-execution', 'assign', 'execution-result', 'accept-execution', 'prepare-dispatch-recovery', 'recover-dispatch', 'candidate-ready', 'ticket-check', 'escalate-implementation'}:
        require(progress is not None, 'missing dispatcher checkpoint')
        verify_binding(repository, progress['binding'])
        scope_base = git(repository, 'rev-parse', progress['binding']['target_branch']).decode().strip()
        scope_base = git(repository, 'merge-base', scope_base, 'HEAD').decode().strip()
        excluded = retained_closure_paths(repository, progress) | verified_planning_paths(repository, progress)
        require(set(changed_paths(repository, scope_base)) - excluded <= set(progress['allowed_paths']),
                'actual Git diff escapes implementation scope')
    if action == 'escalate-implementation':
        actual = recovery_snapshot(repository, progress)[0]
        verify_stopped_snapshot(repository, evidence['snapshot'], actual)
        evidence['pending_paths'] = sorted(p for p in progress['allowed_paths'] if actual['files'][p] !=
            committed_fingerprint(repository, progress['git_baseline_commit'], p))
    if action == 'ticket-check':
        from execution_plan import current_catalog, ticket_check_paths
        selected = ticket_check_paths(current_catalog(progress['execution_authority']), evidence['ticket_id'],
            progress['allowed_paths'], progress['protected_paths'], progress['execution_authority'])
        require(snapshot(repository)['head'] == evidence['candidate'] and not git(repository, 'status', '--porcelain'),
                'early Ticket checks require the clean actual checked candidate')
        evidence['source_snapshot'] = {relative: file_fingerprint(repository, relative) for relative in selected}
    if action == 'prepare-dispatch-recovery':
        actual, ownership, revalidate = recovery_snapshot(repository, progress)
        evidence.update(snapshot=actual, ownership=ownership, revalidate=revalidate)
    if action in {'plan-execution', 'repair-execution'}:
        require('integration_target_head' not in progress or progress.get('integration_source') is not None,
                'capture final integration before revalidation allocations')
        if evidence.get('adopt_paths'):
            source = adoption_source(progress)
            require(source is not None, 'adoption requires an escalated direct snapshot')
            require(set(evidence['adopt_paths']) <= set(source['pending_paths']), 'unknown inherited paths')
            evidence['adoption_fingerprints'] = {p: source['snapshot']['files'][p] for p in evidence['adopt_paths']}
            require(all(file_fingerprint(repository, p) == fingerprint for p, fingerprint in evidence['adoption_fingerprints'].items()),
                    'adopted bytes changed since the Controller snapshot')
        verify_execution_start(repository, progress)
        verify_plan_read_only(repository, progress, evidence)
        before = snapshot(repository)
        before['files'] = {p: file_fingerprint(repository, p) for p in progress['allowed_paths']}
    if action == 'assign':
        matches = [item for item in progress['executions']
                   if item['task_id'] == evidence['task_id'] and
                   item['allocation_digest'] == evidence['allocation_digest']]
        require(len(matches) == 1 and 'git_snapshot' in matches[0],
                'execution allocation has no verified preparation snapshot')
        allocation = matches[0]
        verify_plan_read_only(repository, progress, allocation)
        before = allocation['git_snapshot']
        current = snapshot(repository)
        require(all(current[key] == before[key] for key in ('head', 'branch', 'index_hash')),
                'Git state changed before Execution Agent binding')
        require(all(file_fingerprint(repository, relative) == before['files'][relative]
                    for relative in allocation['paths']),
                'assigned implementation changed before Execution Agent binding')
    if action == 'execution-result':
        execution = execution_record(progress, evidence['agent_ref'])
        verify_plan_read_only(repository, progress, execution)
        require(all(file_fingerprint(repository, relative) == execution['git_snapshot']['files'][relative]
                    for relative in set(execution['read_only']) & set(progress['allowed_paths'])),
                'implementation read-only bytes changed during execution')
        result_snapshot, pending_peers = execution_delta(repository, progress, execution)
        actual_changes = set(result_snapshot)
        result_snapshot.update({p: file_fingerprint(repository, p) for p in execution.get('adopt_paths', [])})
        actual_hashes = {p: implementation_hash(repository, p) for p in result_snapshot}
        require(set(evidence['changed_paths']) == actual_changes and evidence['file_hashes'] == actual_hashes,
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
            verify_plan_read_only(repository, progress, execution)
            require(all(file_fingerprint(repository, relative) == execution['git_snapshot']['files'][relative]
                        for relative in set(execution['read_only']) & set(progress['allowed_paths'])),
                    'implementation read-only bytes changed before acceptance')
            execution_delta(repository, progress, execution, accepting=True)
            evidence['file_hashes'] = {p: evidence['file_hashes'][p] for p in execution['file_hashes']}
    if action in {'candidate-ready', 'review-start', 'review-converged', 'validation-start', 'validation-result', 'validation-retry', 'delivery-ready', 'accept-delivery'}:
        excluded = retained_closure_paths(repository, progress) | verified_planning_paths(repository, progress)
        require(not (set(changed_paths(repository, payload['baseline'])) - excluded) & set(progress['protected_paths']), 'protected source changed')
    if action == 'candidate-ready':
        require('integration_target_head' not in progress or progress.get('integration_source') is not None,
                'capture and revalidate the final integration before a replacement candidate')
        require(evidence.get('plan_authority', progress['plan_authority']) == progress['plan_authority'],
                'candidate names a historical function plan')
        evidence['plan_authority'] = copy.deepcopy(progress['plan_authority'])
        require(evidence['expected_target_head'] == progress.get('integration_target_head', progress['git_baseline_commit']),
                'candidate review base differs from its frozen authority')
        git(repository, 'merge-base', '--is-ancestor', evidence['expected_target_head'], evidence['commit'])
        require(not git(repository, 'status', '--porcelain'), 'candidate working tree is dirty')
        actual_commit = snapshot(repository)['head']
        require(evidence['commit'] == actual_commit, 'candidate is not actual HEAD')
        git(repository, 'merge-base', '--is-ancestor', progress['git_baseline_commit'], evidence['commit'])
        excluded = retained_closure_paths(repository, progress) | verified_planning_paths(repository, progress)
        candidate_paths = sorted(set(changed_paths(repository, evidence['expected_target_head'])) - excluded)
        history_paths = incoming_changed_paths(repository, evidence['expected_target_head'], evidence['commit']) - excluded
        retired = verified_retired_history_paths(repository, progress, evidence['commit'],
                                                history_paths, request['context']['controller_ref'])
        require(history_paths - retired <= set(progress['allowed_paths']) and
                not history_paths & set(progress['protected_paths']),
                'candidate history escapes implementation scope or changes protected sources')
        if not direct_implementation(progress):
            from execution_plan import current_catalog, current_accepted_steps
            catalog = current_catalog(progress['execution_authority'])
            accepted = current_accepted_results(progress)
            actual_fingerprints = {relative: file_fingerprint(repository, relative)
                                   for relative in progress['allowed_paths']}
            completed = current_accepted_steps(catalog, accepted,
                progress['execution_authority'], actual_fingerprints)
            missing = [identity for identity in catalog['ordered_step_ids'] if identity not in completed]
            require(not missing, 'candidate requires an accepted result for current function step: ' +
                    missing[0] if missing else 'candidate function steps are complete')
        from execution_plan import accepted_owned_fingerprints
        accepted_fingerprints = {}
        for result in ([] if progress.get('integration_source') is not None else progress.get('carried_results', [])):
            fingerprints = accepted_owned_fingerprints(result)
            require(fingerprints is not None, 'carried execution lacks verified owned fingerprints')
            accepted_fingerprints.update(fingerprints)
        for execution in progress['executions']:
            if execution['state'] == 'accepted':
                require('git_result_snapshot' in execution,
                        'accepted execution has no verified result snapshot')
                accepted_fingerprints.update(execution['git_result_snapshot'])
        if direct_implementation(progress):
            require(evidence['expected_target_head'] == progress.get('implementation_target_head'),
                    'direct target advanced; escalate to full before integration')
            require('direct_provenance' in evidence, 'actual native dispatcher provenance required')
            verify_stopped_snapshot(repository, evidence['direct_provenance']['stop'].get('git_snapshot'),
                                    recovery_snapshot(repository, progress)[0])
            accepted_fingerprints = {p: committed_fingerprint(repository, actual_commit, p) for p in candidate_paths}
            evidence['direct_provenance']['fingerprints'] = accepted_fingerprints
            evidence['direct_provenance']['paths'] = candidate_paths
        require(all(relative in accepted_fingerprints and
                    file_fingerprint(repository, relative) == accepted_fingerprints[relative]
                    for relative in candidate_paths),
                'candidate content or mode differs from accepted Execution Agent delivery')
        source = progress.get('integration_source') or progress.get('revalidation_source')
        if source is not None:
            require(set(source['pending_paths']) <= set(accepted_fingerprints),
                    'adopted bytes require fresh Execution Agent revalidation')
        evidence.update(clean=True, changed_paths=candidate_paths,
                        file_hashes={p: implementation_hash(repository, p) for p in progress['allowed_paths']})
    if action in {'review-start', 'review-converged', 'validation-start', 'validation-result', 'validation-retry', 'delivery-ready', 'accept-delivery'}:
        require(progress is not None, 'missing implementation fixed point')
        verify_binding(repository, progress['binding'])
        require(not git(repository, 'status', '--porcelain') and snapshot(repository)['head'] == progress['candidate'], 'validation candidate changed or dirty')
        excluded = retained_closure_paths(repository, progress) | verified_planning_paths(repository, progress)
        require(set(changed_paths(repository, progress['expected_target_head'])) - excluded <= set(progress['allowed_paths']), 'actual Git diff escapes implementation scope')
        if action in {'validation-start', 'validation-result'}:
            evidence['source_snapshot'] = {p:file_fingerprint(repository, p) for p in progress['allowed_paths'] + progress['protected_paths']}
    result = transition(request)
    if action in {'start-dispatch', 'start-design', 'start-closure'}:
        git(repository, 'merge-base', '--is-ancestor', commit(payload['baseline']), 'HEAD')
        result['context']['handoff_progress']['git_baseline_commit'] = payload['baseline']
        if action == 'start-dispatch' and direct_implementation(result['context']['handoff_progress']):
            result['context']['handoff_progress']['implementation_target_head'] = payload['baseline']
    if action == 'execution-result':
        recorded = execution_record(result['context']['handoff_progress'], evidence['agent_ref'])
        recorded['git_result_snapshot'] = result_snapshot
        result['pending_peer_refs'] = pending_peers
    if action in {'plan-execution', 'repair-execution'}:
        allocated = next(item for item in result['context']['handoff_progress']['executions']
                         if item['allocation_digest'] == result['allocation_digest'])
        allocated['git_snapshot'] = before
    # Git evidence is attached after the pure transition. Keep the original
    # checkpoint inside its bound capacity before any caller persists it.
    validate_context(result['context'])
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

def verify_plan_revision_reviews(evidence, progress):
    """Recompute plan applicability from the original C/native sources."""
    import workflow_progress as progression
    import workflow_state_store as state_store
    from workflow_control import digest
    proof = evidence.get('review_retention')
    require(isinstance(proof, dict), 'plan revision claim applicability is missing')
    path = Path(proof['checkpoint_path'])
    require(path.is_absolute() and str(path.resolve()) == str(path), 'claim checkpoint must be canonical')
    owner = progression.Progress(path, progression.read_active_owner(path))
    checkpoint = owner.state['control']['context']['handoff_progress']
    transaction = owner.state.get('plan_revision_transaction')
    require(checkpoint == progress and transaction and transaction['state'] == 'planning-committed' and
            transaction['proposed_authority'] == evidence['new_execution_authority'] and
            transaction['old_authority'] == progress['execution_authority'] and
            transaction['commit_input']['planning_commit'] == evidence['planning_commit'] and
            transaction['input']['revision_id'] == evidence['revision_id'],
            'claim applicability differs from the exact prepared revision')
    _, applications = owner.plan_revision_reviews(transaction)
    store = state_store.open_store(path)
    current = store.open_current()
    value = store.read_evidence(proof['reference'], run_id=current.run_id)['value']
    expected = {'kind': 'review-applicability-set-v1', 'candidate': evidence['planning_commit'],
        'plan_authority': evidence['new_authority'], 'applications': applications}
    require(value == expected and proof['digest'] == digest(expected) and
            proof['retained'] == sorted(app['claim_id'] for app in applications.values() if app['applicable']) and
            proof['affected'] == sorted(app['claim_id'] for app in applications.values() if not app['applicable']),
            'plan revision claim applicability differs from actual plan/fingerprints/dependencies')


def verify_review_coverage(view, progress, *, delivery, extra=None):
    """Recompute finite original-claim coverage against actual target Git bytes."""
    import workflow_progress as progression
    import workflow_state_store as state_store
    import review_evidence
    from workflow_control import validate_coverage_view
    validate_coverage_view(view, view['candidate'], progress['plan_authority'], delivery=delivery)
    path_value = Path(view['checkpoint_path'])
    require(path_value.is_absolute() and str(path_value.resolve()) == str(path_value), 'coverage checkpoint must be canonical')
    outer = progression.read_active_owner(path_value)
    owner = progression.Progress(path_value, outer)
    checkpoint = owner.state['control']['context']['handoff_progress']
    require(checkpoint['execution_authority'] == progress['execution_authority'] and checkpoint['binding'] == progress['binding'],
            'coverage differs from current original C authority')
    checkpoint['review_claims'] = {**checkpoint.get('review_claims', {}), **(extra or {})}
    claims, expected = owner.review_applications(view['candidate'])
    store = state_store.open_store(path_value)
    current = store.open_current()
    certificate = store.read_evidence(view['certificate_ref'], run_id=current.run_id)['value']['review_coverage']
    applications = {}
    for axis, app in expected.items():
        if axis not in certificate['applications']:
            continue
        ref = certificate['applications'][axis]['applicability_ref']
        observed = store.read_evidence(ref, run_id=current.run_id)['value']['review_applicability']
        require(observed == app, 'coverage applicability differs from actual fingerprints/dependency closure')
        applications[axis] = {**observed, 'applicability_ref': ref, 'claim_ref': certificate['claims'][axis]['claim_ref']}
    computed = review_evidence.coverage_certificate(checkpoint['execution_authority']['execution_plan']['catalog'],
        claims, applications, view['candidate'], progress['plan_authority'], progress['allowed_paths'], delivery=delivery)
    require(certificate == computed and certificate['certificate_digest'] == view['certificate_digest'],
            'coverage certificate has missing, stale or altered requirement/AC/file/native proof')


def verify_review_native_anchor(store, current, claim, authority, stop, *, slot=None):
    """Bind immutable objects to the original C CAS receipts, not merely each other."""
    import review_evidence
    import workflow_state_store as state_store
    axis = review_evidence.axis_for(claim)
    prepared = store.read_evidence(authority['prepare_request_ref'], run_id=current.run_id)['value']
    require(isinstance(prepared, dict) and prepared.get('operation') == 'prepare' and prepared.get('axis') == axis and
            prepared.get('candidate') == claim['source_candidate'] and
            prepared.get('actor_ref') == authority['originating_ref'] == current.root['metadata']['controller_ref'] and
            state_store.entry.digest(prepared) == authority['request_digest'] and
            prepared['verification']['checks'] == claim['verification']['checks'],
            'original native prepared request changed')
    if slot is not None:
        require(slot['action_id'] == claim['native_stop']['action_id'] and slot['issuer_ref'] == authority['issuer_ref'] and
                slot['prepare'] == prepared and slot['candidate'] == claim['source_candidate'] and
                slot['ref'] == claim['reviewer_ref'] and slot['stopped'] and slot['start']['applied'] and
                (not slot.get('query') or slot['query']['resolved']) and stop in slot['receipts'],
                'original native slot/action/issuer/stop differs from claim')
    key = store.receipt_key(current, actor=authority['originating_ref'], kind='review:prepare',
        subject=authority['stage_subject'], invocation=claim['native_stop']['action_id'])
    preparation = store.read_receipt(current, key, prepared)
    require(preparation is not None and preparation['action_id'] == claim['native_stop']['action_id'] and
            preparation['issuer_ref'] == authority['issuer_ref'],
            'review issuer lacks its original committed prepare receipt')
    observed = store.read_evidence(authority['stop_request_ref'], run_id=current.run_id)['value']
    raw_proof = ({key: stop['raw'][key] for key in ('size', 'sha256')}
        if stop['raw'].get('format') == 'review-raw-reference-v1' else
        dict(zip(('size', 'sha256'), state_store.raw_measure(stop['raw']))))
    require(observed == {'operation': 'observe', 'axis': axis, 'candidate': claim['source_candidate'],
        'actor_ref': authority['originating_ref'], 'action_id': claim['native_stop']['action_id'],
        'receipt': {**stop, 'raw': raw_proof}}, 'original native stopped request changed')
    key = store.receipt_key(current, actor=authority['originating_ref'], kind='review:observe',
        subject=authority['stage_subject'], invocation=claim['native_stop']['response_ref'])
    observation = store.read_receipt(current, key, observed)
    require(isinstance(observation, dict) and set(observation) == {
        'action_id', 'response_ref', 'native_stop_ref', 'native_stop_semantic_ref'} and
        {key: observation[key] for key in ('action_id', 'response_ref', 'native_stop_ref')} == {
        'action_id': claim['native_stop']['action_id'],
        'response_ref': claim['native_stop']['response_ref'], 'native_stop_ref': claim['native_stop_ref']},
        'native stop lacks its original committed observation receipt')
    report = read_review_semantic(store, current, stop, observed, authority['stage_subject'], proof=observation)
    require(isinstance(report, dict) and report.get('status') == 'accepted' and report.get('findings') == [] and
            set(report.get('ac_refs', [])) == set(claim['ac_refs']) and
            report.get('dependency_claim_ids', []) == claim['dependency_claim_ids'] and
            report.get('requirement_refs', []) == claim['requirement_refs'],
            'original stopped reviewer did not accept the claimed scope')
    if claim['layer'] != 'delivery':
        require(report.get('checks') == claim['verification']['checks'] and
                set(report.get('read_paths', [])) == set(claim['read_fingerprints']),
                'original stopped local reviewer did not accept these reads/checks')
    else:
        scope = report.get('review_scope', {})
        require(isinstance(scope, dict) and scope.get('layer') == 'delivery' and scope.get('mode') == 'cross-ticket-and-coverage' and
                scope.get('shared_state') == claim['shared_state'], 'original delivery scope changed')


def read_review_semantic(store, current, stop, observed, stage_subject, *, proof=None):
    """Consume only the complete semantic snapshot committed with original raw ingress."""
    if proof is None:
        key = store.receipt_key(current, actor=observed['actor_ref'], kind='review:observe',
            subject=stage_subject, invocation=stop['response_ref'])
        proof = store.read_receipt(current, key, observed)
    require(isinstance(proof, dict) and proof.get('action_id') == observed['action_id'] and
        proof.get('response_ref') == stop['response_ref'] and 'native_stop_semantic_ref' in proof,
        'native semantic lacks original committed observation')
    recorded = store.read_evidence(proof['native_stop_ref'], run_id=current.run_id)['value']['native_review_stop']
    require(recorded == stop, 'native semantic changed its original stopped receipt')
    value = store.read_evidence(proof['native_stop_semantic_ref'], run_id=current.run_id)['value']['native_stop_semantic']
    require(set(value) == {'kind', 'complete', 'report', 'binding', 'receipt', 'raw_proof', 'raw_manifest_ref'} and
        value['kind'] == 'native-stop-semantic-v1' and value['complete'] is True and
        value['binding'] == {key: observed[key] for key in ('axis', 'candidate', 'actor_ref', 'action_id')} and
        value['receipt'] == {key: stop[key] for key in ('adapter', 'call_ref', 'response_ref', 'status', 'ref')} and
        value['raw_proof'] == observed['receipt']['raw'], 'native semantic is incomplete or changed its source binding')
    raw = stop['raw']
    if raw.get('format') == 'review-raw-reference-v1':
        require(not raw['truncated'] and value['raw_manifest_ref'] == raw['evidence_ref'],
            'truncated native review cannot grant coverage')
        manifest = store.read_raw_manifest(raw['evidence_ref'], run_id=current.run_id)
        require(manifest['complete'] is True and manifest['size'] == raw['size'] and
            manifest['sha256'] == raw['sha256'] and manifest['native_receipt'] == {
                **value['receipt'], 'action_id': observed['action_id'], 'candidate': observed['candidate'],
                'originating_ref': observed['actor_ref']}, 'native semantic changed its complete original manifest')
    else:
        require(value['raw_manifest_ref'] is None, 'inline native semantic has an unexpected manifest')
    return value['report']


def verify_stored_review_coverage(view, progress, packages, run_id):
    """Read accepted immutable proof after its active Stage-3 subjects have retired."""
    import workflow_state_store as state_store
    import review_evidence
    from workflow_control import validate_coverage_view
    validate_coverage_view(view, progress['candidate'], progress['plan_authority'], delivery=True)
    store = state_store.open_store(view['checkpoint_path'])
    current = store.open_current()
    require(current.run_id == run_id, 'accepted coverage belongs to another run')
    certificate = store.read_evidence(view['certificate_ref'], run_id=run_id)['value']['review_coverage']
    require(certificate['certificate_digest'] == view['certificate_digest'] and
            certificate['certificate_digest'] == state_store.entry.digest({key: value for key, value in certificate.items() if key != 'certificate_digest'}),
            'accepted coverage certificate digest changed')
    claims, applications = {}, {}
    repository = Path(progress['binding']['worktree'])
    # Closure may retain only the surviving primary repository after Flow removal.
    if not repository.exists():
        repository = Path(progress['binding']['repository'])
    for axis, claim_view in certificate['claims'].items():
        claim = store.read_evidence(claim_view['claim_ref'], run_id=run_id)['value']['review_claim']
        review_evidence.validate_claim(claim)
        require(review_evidence.claim_view(claim, claim_view['claim_ref']) == claim_view and
                claim['package_digest'] == state_store.entry.digest(packages), 'accepted original review claim changed package or identity')
        stop = store.read_evidence(claim['native_stop_ref'], run_id=run_id)['value']['native_review_stop']
        require(all(stop[key] == claim['native_stop'][key] for key in ('adapter', 'call_ref', 'response_ref', 'status', 'ref')),
                'accepted review lost its original stopped native source')
        source = store.read_evidence(claim['verification']['source_ref'], run_id=run_id)['value']['local_check_source']
        require(state_store.entry.digest(source) == claim['verification']['source_digest'], 'accepted regression source changed')
        control_source = source['raw']
        require(control_source.get('stopped') is True and control_source.get('source_unchanged') is True and
                control_source.get('checks') == claim['verification']['checks'], 'accepted regression lacks original stopped checks')
        source_authority = store.read_evidence(claim['verification']['source_authority_ref'],
            run_id=run_id)['value']['review_source_authority']
        issuer = store.read_evidence(source_authority['issuer_ref'], run_id=run_id)['value']['review_issuer']
        review_evidence.validate_original_source(claim, source, source_authority, issuer)
        verify_review_native_anchor(store, current, claim, source_authority, stop)
        app_ref = certificate['applications'][axis]['applicability_ref']
        app = store.read_evidence(app_ref, run_id=run_id)['value']['review_applicability']
        compared = app['compared']
        require(app['claim_id'] == claim['claim_id'] and app['source_candidate'] == claim['source_candidate'] and
                app['target_candidate'] == view['candidate'] and app['target_plan_authority'] == progress['plan_authority'] and
                app['applicable'] is True and app['applicability_id'] == certificate['applications'][axis]['applicability_id'] and
                app['applicability_id'] == state_store.entry.digest({key: value for key, value in app.items() if key != 'applicability_id'}),
                'accepted applicability changed its original claim or target')
        actual = review_fingerprints(repository, view['candidate'], set(compared['owned_fingerprints']) | set(compared['read_fingerprints']))
        require(actual == compared['owned_fingerprints'] | compared['read_fingerprints'], 'accepted review target fingerprints changed')
        applications[axis] = {**app, 'applicability_ref': app_ref, 'claim_ref': claim_view['claim_ref']}
        claims[axis] = claim
    computed = review_evidence.coverage_certificate(progress['execution_authority']['execution_plan']['catalog'],
        claims, applications, view['candidate'], progress['plan_authority'], progress['allowed_paths'], delivery=True)
    require(computed == certificate, 'accepted coverage has missing requirement/AC/file/native proof')


if __name__ == '__main__':
    sys.exit(main())
