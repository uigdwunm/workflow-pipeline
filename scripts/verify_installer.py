#!/usr/bin/env python3
"""Verify skills@1.6.0 discovery and isolated installation using an existing CLI.

Supply --cli /temporary/npm-prefix/node_modules/skills/bin/cli.mjs. This verifier
never installs the installer or touches the user's Skill directories. The pinned
CLI supports XDG_STATE_HOME; all installer state and project writes stay temporary.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src/shared/scripts'))
from skill_preflight import package_identity


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli',type=Path,required=True)
    args=parser.parse_args()
    cli=args.cli.resolve(strict=True)
    metadata=json.loads((cli.parents[1]/'package.json').read_text())
    if metadata.get('name')!='skills' or metadata.get('version')!='1.6.0':
        parser.error('requires the verified skills@1.6.0 installer')
    with tempfile.TemporaryDirectory() as temporary:
        root=Path(temporary).resolve();source=root/'source';project=root/'project';project.mkdir()
        subprocess.run([sys.executable,str(ROOT/'scripts/build_skills.py'),'--output',str(source/'skills')],check=True)
        shutil.copytree(ROOT/'src',source/'src',ignore=shutil.ignore_patterns('__pycache__'))
        env={**os.environ,'DISABLE_TELEMETRY':'1','DO_NOT_TRACK':'1','XDG_STATE_HOME':str(root/'state')}
        def run(*arguments):
            result=subprocess.run(['node',str(cli),'add',*arguments],cwd=project,env=env,capture_output=True,text=True)
            if result.returncode:raise RuntimeError(result.stdout+result.stderr)
            return result.stdout
        discovered=run(str(source),'--list')
        if 'Found 5 skills' not in discovered:raise RuntimeError(discovered)
        names=sorted(p.name for p in (source/'skills').iterdir())
        for name in names:
            if name not in discovered:raise RuntimeError('missing installer discovery: '+name)
        installed=[]
        for name in names:
            run(str(source/'skills'/name),'--skill',name,'--agent','codex','--copy','--yes')
            destination=project/'.agents/skills'/name
            original=package_identity(str(source/'skills'/name/'SKILL.md'))
            identity=package_identity(str(destination/'SKILL.md'))
            if identity['bundle_digest']!=original['bundle_digest']:raise RuntimeError('installer altered package bytes')
            installed.append({'name':name,'bundle_digest':identity['bundle_digest']})
        print(json.dumps({'installer':'skills@1.6.0','discovered':names,'installed':installed,
            'scope':'temporary project and XDG state only','agent_registration_verified':False},indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())
