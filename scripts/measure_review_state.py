#!/usr/bin/env python3
"""Read original review/check sources and command logs; never mutate a run."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/shared/scripts'))
import entry_prepare as entry
import workflow_state_store as state_store

UNKNOWN = 'unknown'


def read_command_log(path):
    path = Path(path).absolute()
    entry.require(path.is_file() and not path.is_symlink(), 'invalid_log', 'regular original command log required')
    with path.open('rb') as stream:
        raw = stream.read(state_store.EVIDENCE_LIMIT + 1)
    entry.require(len(raw) <= state_store.EVIDENCE_LIMIT, 'state_capacity', 'command log exceeds evidence limit')
    text = raw.decode('utf-8', errors='replace')
    values = {}
    for key in ('real', 'user', 'sys'):
        matches = re.findall(r'(?m)^' + key + r' ([0-9]+(?:\.[0-9]+)?)$', text)
        values[key] = float(matches[0]) if len(matches) == 1 else UNKNOWN
    totals = re.findall(r'Ran ([0-9]+) tests across ([0-9]+) files.*?failures=([0-9]+), errors=([0-9]+), skipped=([0-9]+)', text)
    counts = dict(zip(('tests', 'files', 'failures', 'errors', 'skipped'), map(int, totals[-1]))) if len(totals) == 1 else UNKNOWN
    return {'path': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
        'wall_seconds': values['real'], 'cpu_user_seconds': values['user'], 'cpu_system_seconds': values['sys'],
        'started_at': UNKNOWN, 'finished_at': UNKNOWN, 'test_counts': counts,
        'classification': UNKNOWN, 'identity_binding': UNKNOWN}


def command_source(source):
    """Source identity remains tied to its actual command/response and output."""
    raw = source.get('raw', {})
    timing = raw.get('timing', {})
    if not isinstance(timing, dict):
        timing = {}
    return {'adapter': source.get('adapter', UNKNOWN), 'call_ref': source.get('call_ref', UNKNOWN),
        'response_ref': source.get('response_ref', UNKNOWN), 'checks': raw.get('checks', []),
        'started_at': timing.get('started_at', UNKNOWN), 'finished_at': timing.get('finished_at', UNKNOWN),
        'wall_seconds': timing.get('wall_seconds', UNKNOWN), 'cpu_seconds': timing.get('cpu_seconds', UNKNOWN),
        'classification': raw.get('classification', UNKNOWN), 'environment': raw.get('environment', UNKNOWN)}


def measure_checkpoint(path):
    store = state_store.StateStore(Path(path).resolve())
    current = store.open_current()
    entry.require(current is not None, 'missing_checkpoint', 'original current checkpoint required')
    slices, reviews, commands = [], [], {}
    for subject_id in current.root['subjects']:
        subject = store.read_subject(current, subject_id).value
        for identity, slot in subject.get('allocations', {}).items():
            metrics = slot.get('metrics', {})
            slices.append({'slice_id': metrics.get('slice_id', identity),
                'first_submission': metrics.get('first_submission', UNKNOWN),
                'submissions': metrics.get('submissions', UNKNOWN),
                'invocations': 1 + len(slot.get('invocation_history', [])),
                'model': slot.get('handoff', {}).get('selection', {}).get('model', UNKNOWN),
                'status': (slot.get('record') or {}).get('status', UNKNOWN)})
        groups = [subject.get('review_activity', {}), *subject.get('review_history', [])]
        for group in groups:
            for axis, slot in group.items():
                reviews.append({'axis': axis, 'action_id': slot['action_id'],
                    'candidate': slot['candidate'], 'ref': slot.get('ref', UNKNOWN),
                    'stopped': slot['stopped'], 'waiting_seconds': UNKNOWN,
                    'waiting_source': slot.get('call_ref', UNKNOWN),
                    'classification': slot.get('cost', {}).get('classification', UNKNOWN),
                    'classification_source': slot.get('cost', {}).get('source_ref', UNKNOWN)})
                source = slot.get('prepare', {}).get('verification', {}).get('source')
                if source:
                    commands[entry.digest(source)] = command_source(source)
        control = subject.get('control', {})
        context = control.get('context') or {}
        if control.get('store_ref') is not None:
            context, _ = state_store.StateStore(control['store_ref']['ledger_path']).read_ledger_control(control['store_ref'])
        progress = context.get('handoff_progress') or {}
        source = progress.get('candidate_evidence', {}).get('source')
        if source:
            commands[entry.digest(source)] = command_source(source)
        for checked in progress.get('ticket_checks', {}).values():
            commands[entry.digest(checked['source'])] = command_source(checked['source'])
        for transaction in subject.get('control_transactions', {}).values():
            source = transaction['request'].get('receipt')
            if source and source.get('adapter'):
                commands[entry.digest(source)] = command_source(source)
        for attempt in progress.get('attempts', []):
            if attempt.get('source'):
                commands[entry.digest(attempt['source'])] = command_source(attempt['source'])
    metadata = current.root['metadata']
    return {'run_id': current.run_id, 'package_digest': entry.digest(metadata.get('packages', {})),
        'revision': current.revision, 'slices': slices, 'reviews': reviews,
        'review_rounds': len({item['action_id'] for item in reviews}), 'command_sources': list(commands.values()),
        'store_reading': store.read_totals,
        'reading_coverage': 'original checkpoint and ledger Store reads; Git, host and entry_prepare unknown', 'environment': UNKNOWN, 'native_live_host': UNKNOWN}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path)
    parser.add_argument('--command-log', type=Path, action='append', default=[])
    args = parser.parse_args(argv)
    if args.checkpoint is None and not args.command_log:
        parser.error('provide an original checkpoint or command log')
    try:
        with state_store.measure_reads() as reading:
            checkpoint = measure_checkpoint(args.checkpoint) if args.checkpoint else UNKNOWN
        if checkpoint != UNKNOWN:
            checkpoint['store_reading'] = reading
        report = {'checkpoint': checkpoint,
            'command_logs': [read_command_log(path) for path in args.command_log],
            'end_to_end_wall_seconds': UNKNOWN, 'parallel_task_wall_seconds': UNKNOWN,
            'coordination_wait_seconds': UNKNOWN, 'peak_rss': UNKNOWN, 'external_read_bytes': UNKNOWN,
            'efficiency': 'evidence-insufficient',
            'limits': 'Command durations are independent samples; absent absolute intervals are not summed.'}
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0
    except entry.ERROR_TYPES as error:
        print(json.dumps({'error': {'code': getattr(error, 'code', 'measurement_failed'),
            'message': entry.error_message(error)}}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
