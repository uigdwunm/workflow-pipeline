#!/usr/bin/env python3
"""Print a reproducible T13/T14 field acceptance worksheet; creates no tasks.

Run after automated validation. Supply a release root to record real bundle
identities. Host registration, Matt execution and Agent behavior stay explicitly
unverified until an authorized operator records real transcripts and handoffs.
"""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src/shared/scripts'))
from skill_preflight import STAGES, package_identity


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packages',type=Path,default=ROOT/'skills')
    args=parser.parse_args()
    identities={name:package_identity(str((args.packages/name/'SKILL.md').resolve())) for name in STAGES}
    rows=[]
    expected=(
        'Bootstrap one topic; confirm one requirement decision through the real discussion CLI; read it back.',
        'Run the selected actual Matt grilling route; commit and freeze exactly one requirement document.',
        'Use actual to-spec and selected ticket/ADR capabilities; publish planning and retain its Flow Worktree.',
        'Use an explicit complete standalone brief; execute the native dispatcher and both independent review axes; retain the candidate without merging.',
        'Exercise authorized standalone documentation closure and an inherited candidate; verify publication and cleanup through the existing protocol.',
    )
    for stage,name in enumerate(STAGES):
        rows.append({'test':'T13','stage':stage,'name':name,'status':'not_run',
            'setup':'In a disposable project and fresh authorized host task, activate only this workflow package plus this action’s actual external Matt Skills. Refresh the task registry.',
            'action':expected[stage],
            'acceptance':'Read resources only from the pinned package root. Complete this stage even when the successor is absent; report its missing status without performing it.',
            'evidence':{'transcript':None,'current_host_registry':None,'package_identity':identities[name],
                'matt_entries_and_actions':None,'requirement_path_commit_hash':None,'native_role_receipts':None,
                'handoff':None,'git_before_after':None,'ledger_and_binding':None}})
    rows.append({'test':'T14','status':'not_run','setup':'Obtain authorization for disposable projects, native test tasks and any installation-link change. Preserve old immutable versions.',
        'steps':['Activate compatible Stage 2/3/4 packages; record current registry and pinned identities.',
            'Run one real 2→3→4 flow; verify the same frozen requirement, controller and Flow Worktree at each handoff.',
            'Run a discussion-attached route through real prepare, claim/ready, source activation, accept/finalize.',
            'In the disposable installation only, remove a pending successor after saving the current stage result. Verify no successor dispatch, premature archive or Phase advancement.',
            'Restore the same pinned version and refresh registry. Resume the original session/attempt at the first unfinished action; do not repeat planning, candidate or merge.',
            'Verify merge-success/cleanup-pending recovery runs cleanup only. Retain transcript, exact receipts, Git ancestry and resource-removal evidence.'],
        'evidence':{'transcript':None,'registry_and_identities':None,'native_role_receipts':None,'handoffs':None,'same_requirement_and_flow':None,'failure_and_recovery':None,'archive_order':None}})
    print(json.dumps({'automated_agent_claim':False,'installation_changes_authorized':False,'checks':rows,
        'local_sync':['Record actual registry links and old real roots/digests before requesting installation-change authorization.',
            'With that separate authorization, install to a new immutable directory using the user’s actual manager; keep old versions until active runs end.',
            'Switch new-task registration, refresh the host task and read back the effective registry plus package digest.',
            'If verification fails, restore the saved link and retain old runner versions for version-1 records. Never edit manager private state.']},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
