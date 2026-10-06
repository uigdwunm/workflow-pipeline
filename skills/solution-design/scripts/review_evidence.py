"""Immutable review claims and finite, plan-bound subject validation.

The caller authenticates native receipts and supplies exact Git fingerprints.
This module neither grants writer authority nor performs storage or host calls.
"""
from __future__ import annotations
import copy
import re
import entry_prepare as entry
import execution_plan

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
