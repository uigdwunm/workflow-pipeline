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
    require(len(parts) == 3 and parts[0] == 'local',
            'review_incomplete', 'exact local Ticket and behavior slice required')
    tickets = [ticket for ticket in catalog['tickets'] if ticket['ticket_id'] == parts[1]]
    require(len(tickets) == 1, 'review_incomplete', 'unknown review Ticket')
    ticket = tickets[0]
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
        'reviewer_ref', 'native_stop', 'native_stop_ref', 'findings', 'status'},
        'review_incomplete', 'immutable claim fields are incomplete')
    require(type(claim['schema_version']) is int and claim['schema_version'] == 1 and claim['layer'] == 'local' and
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
