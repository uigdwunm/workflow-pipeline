"""Preflight exercises real release trees and explicit current-host registry inputs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class PreflightTests(unittest.TestCase):
    def test_registry_selection_and_pinned_link_survive_upgrade(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            packages = root / 'packages'
            subprocess.run([sys.executable, str(ROOT/'scripts/build_skills.py'), '--output',str(packages)],check=True)
            linked = root / 'active'
            linked.symlink_to(packages / 'design-discussion',target_is_directory=True)
            script = packages / 'design-discussion/scripts/skill_preflight.py'
            def call(request, success=True):
                result=subprocess.run([sys.executable,str(script)],input=json.dumps(request),text=True,capture_output=True)
                self.assertEqual(result.returncode,0 if success else 1,result.stdout+result.stderr)
                return json.loads(result.stdout)
            request={'stage':0,'action':'discuss','registry':{'source':'host-current-skills','entries':[
                {'name':'design-discussion','entry':str(linked/'SKILL.md'),'source':'project'}]}}
            result=call(request)
            identity=result['packages']['design-discussion']
            self.assertEqual(identity['root'],str(packages/'design-discussion'))
            linked.unlink()
            linked.symlink_to(packages/'problem-framing',target_is_directory=True)
            self.assertTrue(call({'operation':'verify','identity':identity})['ok'])
            data=packages/'design-discussion/SKILL.md'
            data.write_text(data.read_text()+'\nchanged\n')
            changed=call({'operation':'verify','identity':identity},False)
            self.assertEqual(changed['error']['code'],'package_changed')

    def test_filesystem_candidates_never_become_active(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve()
            package=root/'packages'
            subprocess.run([sys.executable,str(ROOT/'scripts/build_skills.py'),'--output',str(package)],check=True)
            result=subprocess.run([sys.executable,str(package/'design-discussion/scripts/skill_preflight.py')],
                input=json.dumps({'stage':0,'action':'discuss','diagnostic_roots':[str(package)]}),capture_output=True,text=True)
            self.assertEqual(result.returncode,1)
            response=json.loads(result.stdout)
            self.assertEqual(response['error']['code'],'registry_unavailable')
            self.assertTrue(response['error']['candidates'])

if __name__=='__main__': unittest.main()
