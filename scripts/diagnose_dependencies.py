#!/usr/bin/env python3
"""Filesystem-only diagnostics; never claims current Agent registration."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/shared/scripts'))
from skill_preflight import STAGES, PreflightError, preflight


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', required=True, choices=[str(i) for i in range(5)] + list(STAGES))
    parser.add_argument('--action', default='entry')
    parser.add_argument('--project', type=Path)
    parser.add_argument('--root', action='append', type=Path, default=[])
    parser.add_argument('--required-skill', action='append', default=[])
    args = parser.parse_args()
    stage = int(args.stage) if args.stage.isdigit() else STAGES.index(args.stage)
    roots = args.root or [Path.home()/'.codex/skills', Path.home()/'.agents/skills', Path.home()/'.cc-switch/skills']
    if os.environ.get('CODEX_HOME') and not args.root:
        roots.insert(0, Path(os.environ['CODEX_HOME'])/'skills')
    if args.project:
        roots = [args.project/'.agents/skills', args.project/'.codex/skills', *roots]
    try:
        result = preflight({'operation':'diagnose','stage':stage,'action':args.action,
            'required_skills':args.required_skill,'diagnostic_roots':[str(p.absolute()) for p in roots]})
    except PreflightError as exc:
        parser.error(f'{exc.code}: {exc}')
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
