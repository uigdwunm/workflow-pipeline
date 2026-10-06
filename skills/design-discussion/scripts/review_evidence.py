"""Immutable review claims and finite, plan-bound subject validation.

The caller authenticates native receipts and supplies exact Git fingerprints.
This module neither grants writer authority nor performs storage or host calls.
"""
from __future__ import annotations
import copy
import re
import entry_prepare as entry
import execution_plan
import workflow_control as control

require = entry.require


def subject(catalog, axis):
    require(isinstance(axis, str) and len(axis.encode()) <= 512,
            'review_incomplete', 'bounded review subject required')
    parts = axis.split(':')
    require((len(parts) == 3 and parts[0] == 'local') or (len(parts) == 2 and parts[0] == 'ticket'),
            'review_incomplete', 'exact local Ticket and behavior slice required')
    tickets = [ticket for ticket in catalog['tickets'] if ticket['ticket_id'] == parts[1]]
    require(len(tickets) == 1, 'review_incomplete', 'unknown review Ticket')
    ticket = tickets[0]
    if parts[0] == 'ticket':
        plan = ticket['plan']
        return (ticket, {'id': 'ticket', 'step_ids': [step['id'] for step in plan['steps'] + plan['artifact_steps']],
            'ac_refs': sorted({ref for group in plan['behavior_slices'] for ref in group['ac_refs']}),
            'verification_refs': [item['id'] for item in plan['verifications']],
            'read_only': sorted({path for group in plan['behavior_slices'] for path in group['read_only']}),
            'shared_state': sorted({name for group in plan['behavior_slices'] for name in group['shared_state']})},
            plan['steps'] + plan['artifact_steps'])
    groups = [group for group in ticket['plan']['behavior_slices'] if group['id'] == parts[2]]
    require(len(groups) == 1, 'review_incomplete', 'unknown review behavior slice')
    group = groups[0]
    steps = [step for step in ticket['plan']['steps'] + ticket['plan']['artifact_steps']
             if step['id'] in group['step_ids']]
    return ticket, group, steps


def local_required_checks(catalog, axis, worktree):
    """Index every frozen verification/command pair without collapsing shared commands."""
    ticket, group, _ = subject(catalog, axis)
    verifications = {item['id']: item for item in ticket['plan']['verifications']}
    required = []
    for verification_id in group['verification_refs']:
        require(verification_id in verifications, 'review_incomplete', 'unknown local verification ref')
        for index, command in enumerate(verifications[verification_id]['commands'], 1):
            required.append({'id': verification_id + ':' + str(index), 'category': 'focused',
                'command': command, 'cwd': worktree, 'pass_condition': 'exit 0',
                'allowed_skips': [], 'environment': None})
    return required


def validate_local_verification(catalog, axis, verification, candidate, plan_authority,
                                worktree, run_id, package_digest, dispatcher_ref):
    """Apply the original check/source validators to the frozen local command index."""
    require(isinstance(verification, dict) and set(verification) == {'plan_authority', 'checks', 'source'} and
            verification['plan_authority'] == plan_authority,
            'review_incomplete', 'current structured local verification and command source required')
    required = local_required_checks(catalog, axis, worktree)
    try:
        control.validate_checks(required, verification['checks'], candidate)
        control.validate_result_source(verification['source'], verification['checks'], dispatcher_ref)
    except control.ControlError as error:
        raise entry.PreparationError('review_incomplete', 'local regression evidence: ' + str(error)) from error
    raw = verification['source']['raw']
    require(raw.get('candidate') == candidate and raw.get('run_id') == run_id and
            raw.get('package_digest') == package_digest and raw.get('plan_authority') == plan_authority,
            'review_incomplete', 'original local command source changed its candidate, run, package or authority')
    require(isinstance(raw.get('environment'), dict) and raw['environment'] and
            isinstance(raw.get('fixture'), dict) and raw['fixture'],
            'review_incomplete', 'retain original local command environment and fixture evidence')
    return required


def validate_claim(claim):
    require(isinstance(claim, dict) and set(claim) == {
        'schema_version', 'claim_id', 'run_id', 'package_digest', 'layer', 'ticket_id', 'slice_id',
        'plan_refs', 'plan_authority', 'contract_digest', 'source_candidate', 'owned_fingerprints',
        'read_fingerprints', 'shared_state', 'dependency_claim_ids', 'ac_refs', 'verification',
        'reviewer_ref', 'native_stop', 'native_stop_ref', 'findings', 'status', 'requirement_refs'},
        'review_incomplete', 'immutable claim fields are incomplete')
    require(type(claim['schema_version']) is int and claim['schema_version'] == 1 and claim['layer'] in {'local', 'ticket', 'delivery'} and
            claim['status'] == 'accepted' and claim['findings'] == [] and
            re.fullmatch(r'[0-9a-f]{40}', claim['source_candidate']) and
            all(isinstance(claim[key], str) and claim[key]
                for key in ('run_id', 'package_digest', 'ticket_id', 'slice_id', 'reviewer_ref')),
            'review_incomplete', 'claim requires a complete accepted native result')
    require(claim['claim_id'] == entry.digest({key: value for key, value in claim.items() if key != 'claim_id'}),
            'review_incomplete', 'original immutable claim identity changed')
    verification = claim['verification']
    require(isinstance(verification, dict) and set(verification) == {
        'ids', 'required', 'checks', 'source_results', 'source_ref', 'source_digest',
        'environment_digest', 'fixture_digest'} and isinstance(verification['source_ref'], dict) and
        verification['source_ref'].get('run_id') == claim['run_id'] and
        verification['source_ref'].get('kind') == 'evidence' and
        all(isinstance(verification[key], str) and re.fullmatch(r'[0-9a-f]{64}', verification[key])
            for key in ('source_digest', 'environment_digest', 'fixture_digest')),
        'review_incomplete', 'claim lacks its immutable original local command source')
    try:
        control.validate_checks(verification['required'], verification['checks'], claim['source_candidate'])
    except control.ControlError as error:
        raise entry.PreparationError('review_incomplete', 'local claim regression did not pass: ' + str(error)) from error
    stop = claim['native_stop']
    require(isinstance(stop, dict) and stop.get('status') == 'stopped' and
            stop.get('ref') == claim['reviewer_ref'] and
            all(isinstance(stop.get(key), str) and stop[key] for key in ('adapter', 'call_ref', 'response_ref', 'action_id')) and
            isinstance(claim['native_stop_ref'], dict),
            'review_incomplete', 'claim lacks its original stopped native receipt')
    return copy.deepcopy(claim)


def claim_view(claim, reference):
    validate_claim(claim)
    return {key: copy.deepcopy(claim[key]) for key in (
        'claim_id', 'layer', 'ticket_id', 'slice_id', 'source_candidate', 'plan_authority', 'reviewer_ref')} | {
        'claim_ref': copy.deepcopy(reference), 'result_ref': claim['native_stop']['response_ref']}


def axis_for(claim):
    if claim['layer'] == 'local':
        return 'local:' + claim['ticket_id'] + ':' + claim['slice_id']
    return 'ticket:' + claim['ticket_id'] if claim['layer'] == 'ticket' else claim['slice_id']


def ticket_requirement_refs(catalog, ticket):
    required = catalog.get('requirement_refs', ['requirement:' + catalog['requirement_identity']['sha256']])
    named = [name for name in required if name.startswith('N')]
    return sorted({name for name in named if any(ref.startswith('PRD:' + name + ':') or ref == 'PRD:' + name
        for ref in ticket['plan']['contract_refs'])}) if named else required


def affected_claims(claims, targets, changed_paths, *, invalid_ids=()):
    """Conservative file/dependency closure; commit labels alone do not invalidate proof."""
    changed = set(changed_paths)
    by_id = {claim['claim_id']: claim for claim in claims.values()}
    affected = set(invalid_ids)
    unknown_shared = any(name not in target['known_paths'] for target in targets.values()
                         for name in target['shared_state'])
    if changed and (unknown_shared or changed - set().union(*(set(target['owned_fingerprints'])
                      for target in targets.values()))):
        affected.update(by_id)
    for axis, claim in claims.items():
        target = targets.get(axis)
        if target is None or any(claim[field] != target[field] for field in
                ('contract_digest', 'owned_fingerprints', 'read_fingerprints', 'shared_state', 'ac_refs', 'requirement_refs')):
            affected.add(claim['claim_id'])
            continue
        old_steps = [(ref['ticket_id'], ref['step_id'], ref['step_digest']) for ref in claim['plan_refs']]
        new_steps = [(ref['ticket_id'], ref['step_id'], ref['step_digest']) for ref in target['plan_refs']]
        if old_steps != new_steps or claim['verification']['required'] != target['required'] or                claim['dependency_claim_ids'] != target['dependency_claim_ids'] or target.get('missing_dependencies'):
            affected.add(claim['claim_id'])
    while True:
        more = {claim['claim_id'] for claim in by_id.values()
                if set(claim['dependency_claim_ids']) & affected}
        if more <= affected:
            return sorted(affected)
        affected.update(more)


def applicable_claims(claims, targets, candidate, plan_authority, changed_paths, *, invalid_ids=()):
    affected = set(affected_claims(claims, targets, changed_paths, invalid_ids=invalid_ids))
    records = {}
    for axis, claim in claims.items():
        target = targets[axis]
        record = {'kind': 'review-applicability-v1', 'algorithm': 1, 'claim_id': claim['claim_id'],
            'source_candidate': claim['source_candidate'], 'target_candidate': candidate,
            'target_plan_authority': copy.deepcopy(plan_authority), 'applicable': claim['claim_id'] not in affected,
            'compared': {key: copy.deepcopy(target[key]) for key in (
                'contract_digest', 'plan_refs', 'owned_fingerprints', 'read_fingerprints', 'shared_state',
                'dependency_claim_ids', 'ac_refs', 'requirement_refs', 'required')},
            'changed_paths': sorted(set(changed_paths))}
        record['applicability_id'] = entry.digest(record)
        records[axis] = record
    return records


def coverage_certificate(catalog, claims, applications, candidate, plan_authority, implementation_paths, *, delivery):
    """Every required AC/file reaches original local proof and complete native Ticket acceptance."""
    applicable = {axis: claim for axis, claim in claims.items() if applications.get(axis, {}).get('applicable')}
    edges, covered, ticket_ids = {}, set(), []
    for ticket in catalog['tickets']:
        identity = ticket['ticket_id']
        accepted = applicable.get('ticket:' + identity)
        require(accepted is not None, 'review_incomplete', 'Ticket acceptance is missing or no longer applicable: ' + identity)
        ticket_ids.append(accepted['claim_id'])
        for group in ticket['plan']['behavior_slices']:
            local = applicable.get('local:' + identity + ':' + group['id'])
            require(local is not None and local['claim_id'] in accepted['dependency_claim_ids'],
                    'review_incomplete', 'Ticket lacks applicable local slice coverage: ' + group['id'])
            covered.update(local['owned_fingerprints'])
            for ac in group['ac_refs']:
                edges.setdefault(ac, []).append(local['claim_id'])
        require(set(accepted['ac_refs']) == {ac for group in ticket['plan']['behavior_slices'] for ac in group['ac_refs']},
                'review_incomplete', 'complete Ticket AC acceptance is required')
    require(set(implementation_paths) <= covered, 'review_incomplete', 'changed implementation file lacks original local coverage')
    requirement_edges = {name: [claim['claim_id'] for claim in applicable.values()
        if claim['layer'] == 'ticket' and name in claim['requirement_refs']] for name in catalog['requirement_refs']}
    require(all(requirement_edges.values()), 'review_incomplete', 'requirement has no reachable accepted Ticket proof')
    axes = {}
    if delivery:
        for axis in ('standards', 'spec'):
            claim = applicable.get(axis)
            require(claim is not None and claim['layer'] == 'delivery' and set(ticket_ids) <= set(claim['dependency_claim_ids']),
                    'review_incomplete', 'delivery axis lacks complete cross-Ticket coverage: ' + axis)
            axes[axis] = claim['claim_id']
        require(applicable['standards']['reviewer_ref'] != applicable['spec']['reviewer_ref'],
                'identity_mismatch', 'delivery axes require independent native identities')
    certificate = {'kind': 'review-coverage-v1', 'candidate': candidate, 'plan_authority': copy.deepcopy(plan_authority),
        'requirement_identity': copy.deepcopy(catalog['requirement_identity']), 'delivery': delivery,
        'requirements': requirement_edges, 'acs': {key: sorted(set(value)) for key, value in edges.items()},
        'files': sorted(covered), 'tickets': ticket_ids, 'delivery_axes': axes,
        'claims': {axis: claim_view(claim, applications[axis]['claim_ref']) for axis, claim in applicable.items()},
        'applications': {axis: {key: applications[axis][key] for key in ('applicability_id', 'applicability_ref')}
                         for axis in applicable}}
    certificate['certificate_digest'] = entry.digest(certificate)
    return certificate


def certificate_view(certificate, reference, checkpoint):
    return {key: copy.deepcopy(certificate[key]) for key in
        ('kind', 'candidate', 'plan_authority', 'requirement_identity', 'delivery', 'certificate_digest')} | {
        'certificate_ref': copy.deepcopy(reference), 'checkpoint_path': checkpoint}
