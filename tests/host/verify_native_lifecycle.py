#!/usr/bin/env python3
"""Explicit real-host acceptance; never part of unit-test discovery or installation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'skills/guided-implementation/scripts'))
from foreground_host import ForegroundHost, validate_configuration


def verify_lifecycle_completion(events, report, snapshot):
    relevant = [event for event in events if event.get('kind') == 'message' and
        event['message'].get('params',{}).get('item',{}).get('agentPath') == report['child_ref']]
    completed = [event for event in relevant if event['message']['params']['item'].get('kind') == 'completed']
    interacted = [event for event in relevant if event['message']['params']['item'].get('kind') == 'interacted']
    if (len({event['message']['params']['item']['id'] for event in completed}) != 2 or
            len({event['message']['params']['item']['id'] for event in interacted}) != 1 or
            not any(event['observed_at'] > max(item['observed_at'] for item in interacted) for event in completed)):
        raise RuntimeError('raw events do not prove the original task and its one followup both completed')
    native_ids = {event['message']['params']['item'].get('agentThreadId') for event in relevant}
    threads = [item for page in snapshot['pages'] for item in page['response']['data']]
    states = {item['id']:item['status'] for item in threads}
    if len(native_ids) != 1 or len(threads) != 1 or set(states) != native_ids or any(status != {'type':'idle'} for status in states.values()):
        raise RuntimeError('current lookup did not prove exactly the original child is idle')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path,
                        help='explicit Controller configuration for an existing isolated acceptance directory')
    parser.add_argument('--case', choices=['lifecycle','stop'], default='lifecycle')
    args = parser.parse_args()
    value = json.loads(args.input.read_text())
    workspace, output = Path(value['workspace']).resolve(), Path(value['output']).resolve()
    if workspace == ROOT or workspace.is_relative_to(ROOT) or not workspace.is_dir():
        raise SystemExit('acceptance requires an existing isolated directory outside the repository')
    output.mkdir(parents=True, exist_ok=False)
    configuration = validate_configuration(value['host'], value['controller_ref'], str(workspace),
                                          str(workspace / 'flow'), str(workspace / '.git'))
    settings = value['settings']
    events = []
    def journal(event):
        event = {**event,'observed_at':time.time()}
        events.append(event)
        with (output / 'events.jsonl').open('a') as stream:
            stream.write(json.dumps(event,ensure_ascii=False) + '\n')
            stream.flush(); os.fsync(stream.fileno())
    host = ForegroundHost(configuration,str(workspace),journal)
    report = {'product_module':str(Path(sys.modules['foreground_host'].__file__).resolve()),
              'module_sha256':hashlib.sha256(Path(sys.modules['foreground_host'].__file__).read_bytes()).hexdigest(),
              'package_digest':json.loads((ROOT / 'skills/guided-implementation/package.json').read_text())['bundle_digest'],
              'case':args.case,
              'cli_version':configuration['cli_version'], 'external_nested':'pending-external-dependency',
              'configuration':configuration,'started_at':time.time()}
    schema = {'type':'object','additionalProperties':False,'required':['child_ref','step'],
              'properties':{'child_ref':{'type':'string'},'step':{'type':'string'}}}
    try:
        host.start(output / 'stderr.log')
        thread = host.start_carrier('lifecycle',settings)
        first_prompt = (
            'This is an explicitly authorized, isolated real-host lifecycle acceptance. Use the registered '
            'subagent-governance skill and its actual Hook authority. Spawn exactly one native child, with '
            'the bounded task: wait ' + ('75' if args.case == 'lifecycle' else '300') + ' seconds using bounded waits, then return NATIVE_LIFECYCLE_DONE; '
            'no children, files, network or other work. Do not wait for it in this parent turn. Immediately '
            'return JSON with the exact mechanically returned canonical child_ref and step spawned. '
            'Do not use a user-visible task, a nested agent, an invented session, or bypass a governance gate.')
        first, first_turn = host.run_turn(thread,first_prompt,settings,schema)
        first = json.loads(first)
        ref = first['child_ref']
        report.update(carrier_thread=thread,first_turn=first_turn,child_ref=ref,first_finished_at=time.time(),pid=host.process.pid)
        if args.case == 'stop':
            report['steps'] = []
            for step, instruction in (
                ('paused','Explicitly pause the original child: use its exact native interrupt control and obtain actual terminal/stop evidence.'),
                ('resumed','Explicitly resume the original paused scope: send one followup to this same exact native target asking it to wait 300 seconds with bounded waits, then return NATIVE_LIFECYCLE_DONE. Return immediately after the followup receipt; do not wait.'),
                ('cancelled','Explicitly cancel the resumed original child: use its exact native interrupt control and obtain actual terminal/stop evidence. Do not follow up after cancellation.')):
                result, turn = host.run_turn(thread,
                    instruction + ' Original canonical native target: ' + ref + '. Retain truthful governance facts. '
                    'No new or nested child, replacement identity, file edits or remote work. Return JSON with '
                    'the same child_ref and step ' + step + '.',settings,schema)
                observed = json.loads(result)
                if observed != {'child_ref':ref,'step':step}:
                    raise RuntimeError('control response changed the original identity')
                snapshot = host.snapshot_lifecycle('lifecycle')
                native_ids = {event['message']['params']['item'].get('agentThreadId') for event in events
                    if event.get('kind') == 'message' and event['message'].get('params',{}).get('item',{}).get('agentPath') == ref}
                threads = [item for page in snapshot['pages'] for item in page['response']['data']]
                states = {item['id']:item['status'] for item in threads}
                if len(native_ids) != 1 or len(threads) != 1 or set(states) != native_ids:
                    raise RuntimeError('control lookup did not retain exactly the original child')
                if step in {'paused','cancelled'} and any(status != {'type':'idle'} for status in states.values()):
                    raise RuntimeError('current native lookup did not prove stopped turns')
                if step == 'resumed' and not all(status.get('type') == 'active' for status in states.values()):
                    raise RuntimeError('explicit followup did not produce a current active native turn')
                report['steps'].append({'step':step,'turn':turn,'snapshot':snapshot,'same_process_alive':host.process.poll() is None})
            native_items = [event['message']['params']['item'] for event in events if event.get('kind') == 'message' and
                event['message'].get('params',{}).get('item',{}).get('agentPath') == ref]
            created = {item['id'] for item in native_items if item['kind'] == 'started'}
            interrupted = {item['id'] for item in native_items if item['kind'] == 'interrupted'}
            if len(created) != 1 or len(interrupted) != 2 or not any(item['kind'] == 'interacted' for item in native_items):
                raise RuntimeError('raw native calls do not prove pause/resume/cancel on one identity')
            report.update(status='completed',single_layer_stop_resume_cancel='passed')
            return
        second, second_turn = host.run_turn(thread,
            'Continue this same isolated acceptance with the original native child ' + ref + '. '
            'Wait for its NATIVE_LIFECYCLE_DONE terminal result through the original native channel. '
            'Then send exactly one original-scope followup to this same exact native target: return '
            'NATIVE_LIFECYCLE_FOLLOWUP_DONE, with no other work. Wait for that result. Preserve truthful '
            'governance terminal/closure facts; followup is not a new identity. Do not spawn any replacement '
            'or nested child. Return JSON with that exact child_ref and step followed-up.',settings,schema)
        second = json.loads(second)
        if second['child_ref'] != ref or second['step'] != 'followed-up':
            raise RuntimeError('original child followup was not established')
        native = [event for event in events if event.get('kind') == 'message' and
                  event['message'].get('params',{}).get('item',{}).get('type') == 'subAgentActivity']
        relevant = [event for event in native if event['message']['params']['item'].get('agentPath') == ref]
        created = {event['message']['params']['item']['id'] for event in relevant
                   if event['message']['params']['item'].get('kind') == 'started'}
        completed = [event for event in relevant if event['message']['params']['item'].get('kind') == 'completed']
        interacted = [event for event in relevant if event['message']['params']['item'].get('kind') == 'interacted']
        if len(created) != 1 or not completed or not interacted:
            raise RuntimeError('raw native events do not prove one identity and its followup')
        if not any(event['observed_at'] > report['first_finished_at'] for event in completed):
            raise RuntimeError('raw child completion did not cross the first parent turn')
        snapshot = host.snapshot_lifecycle('lifecycle')
        verify_lifecycle_completion(events, report, snapshot)
        report.update(second_turn=second_turn,second_finished_at=time.time(),same_process_alive=host.process.poll() is None,
                      raw_native_events=len(relevant),snapshot=snapshot,
                      status='completed',single_layer_cross_turn='passed',same_identity_followup='passed')
    except BaseException as error:
        report.update(status='failed',error=str(error))
        raise
    finally:
        host.close()
        report.update(finished_at=time.time(),local_exit_code=host.process.returncode if host.process else None)
        (output / 'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False) + '\n')
    print(json.dumps({'status':'completed','report':str(output / 'report.json')}))


if __name__ == '__main__':
    main()
