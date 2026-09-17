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


class PreflightBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name).resolve()
        cls.packages = cls.root / 'packages'
        subprocess.run([sys.executable,str(ROOT/'scripts/build_skills.py'),'--output',str(cls.packages)],check=True)
        cls.names = ('design-discussion','problem-framing','solution-design','guided-implementation','change-closure')

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def call(self, stage, action, entries, **extra):
        request = {'stage':stage,'action':action,'registry':{'source':'host-current-skills','entries':entries}, **extra}
        result = subprocess.run([sys.executable,str(self.packages/self.names[stage]/'scripts/skill_preflight.py')],
            input=json.dumps(request),capture_output=True,text=True)
        self.assertNotIn('Traceback',result.stderr)
        response = json.loads(result.stdout)
        self.assertEqual(result.returncode,0 if response['ok'] else 1)
        return response

    def own(self, stage):
        return {'name':self.names[stage],'entry':str(self.packages/self.names[stage]/'SKILL.md'),'source':'host'}

    def external(self, name, directory='matt'):
        path=self.root/directory/name/'SKILL.md'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('---\nname: '+name+'\ndescription: external test fixture\n---\n')
        return {'name':name,'entry':str(path),'source':'project'}

    def test_action_dependencies_are_checked_only_when_selected(self):
        cases = [(0,'discuss',[]),(1,'route',['ask-matt']),
            (1,'grill-with-docs',['grill-with-docs','grilling','domain-modeling']),
            (2,'spec',['to-spec']),(2,'decide-tickets',['ask-matt']),(2,'tickets',['to-tickets']),
            (2,'adr',['domain-modeling']),(3,'dispatch',['implement','tdd','code-review']),
            (3,'review',['code-review']),(4,'route',['ask-matt'])]
        for stage,action,required in cases:
            with self.subTest(stage=stage,action=action):
                entries=[self.own(stage)]
                for name in required:
                    missing=self.call(stage,action,entries)
                    self.assertEqual(missing['error']['required_skill'],name)
                    self.assertEqual(missing['error']['action'],action)
                    entries.append(self.external(name))
                self.assertTrue(self.call(stage,action,entries)['ok'])
        self.assertTrue(self.call(0,'entry',[self.own(0)])['ok'])
        missing=self.call(0,'selected-capability',[self.own(0)],required_skills=['research'])
        self.assertEqual(missing['error']['required_skill'],'research')

    def test_selected_registry_wins_and_real_file_aliases_deduplicate(self):
        own=self.own(1); one=self.external('ask-matt','one'); two=self.external('ask-matt','two')
        alias=self.root/'alias';alias.mkdir(exist_ok=True)
        link=alias/'SKILL.md';link.symlink_to(one['entry'])
        linked={**one,'entry':str(link)}
        self.assertTrue(self.call(1,'route',[own,one,linked],diagnostic_roots=[str(self.root/'two')])['ok'])
        self.assertEqual(self.call(1,'route',[own,one,two])['error']['code'],'registry_ambiguous')
        self.assertEqual(self.call(1,'route',[own,{**one,'enabled':False}])['error']['code'],'skill_not_active')
        self.assertEqual(self.call(1,'route',[own,{'name':'ask-matt'}])['error']['code'],'invalid_registry')

    def test_link_and_unreadable_entry_errors_remain_distinct(self):
        own=self.own(1)
        for kind in ('dangling','loop','utf8','permission'):
            with self.subTest(kind=kind):
                directory=self.root/kind;directory.mkdir(exist_ok=True)
                entry=directory/'SKILL.md'
                if kind=='dangling':entry.symlink_to(directory/'missing')
                elif kind=='loop':entry.symlink_to(entry)
                elif kind=='utf8':entry.write_bytes(b'\xff')
                else:entry.write_text('---\nname: ask-matt\n---\n');entry.chmod(0)
                try:
                    result=self.call(1,'route',[own,{'name':'ask-matt','entry':str(entry),'source':'host'}])
                    self.assertEqual(result['error']['code'],{'dangling':'entry_unavailable','loop':'symlink_loop','utf8':'invalid_entry','permission':'unreadable'}[kind])
                finally:
                    if kind=='permission':entry.chmod(0o644)

    def test_nonregular_manifest_is_a_structured_package_error(self):
        manifest=self.packages/'change-closure/package.json'
        original=manifest.read_bytes()
        manifest.unlink();manifest.mkdir()
        try:
            response=self.call(3,'entry',[self.own(3),self.own(4)],target_stages=[4])
            self.assertEqual(response['error']['code'],'invalid_package')
            self.assertEqual(response['error']['required_skill'],'change-closure')
        finally:
            manifest.rmdir();manifest.write_bytes(original)

    def test_manifest_shape_and_compatibility_are_verified_before_target_entry(self):
        import copy
        stage=4;manifest=self.packages/self.names[stage]/'package.json';original=manifest.read_bytes()
        try:
            for change in ('bad-record','release','format','key','same-key-release','missing'):
                with self.subTest(change=change):
                    data=json.loads(original)
                    if change=='bad-record':data['files']['SKILL.md']=[]
                    elif change=='release':data['release']=None
                    elif change=='format':data['format_version']=99
                    elif change=='key':data['compatibility_key']['ledger_write']=99
                    elif change=='same-key-release':data['release']='1.0.1'
                    manifest.write_text(json.dumps(data))
                    if change=='missing':manifest.unlink()
                    response=self.call(3,'entry',[self.own(3),self.own(4)],target_stages=[4])
                    self.assertEqual(response['ok'],change=='same-key-release')
                    if not response['ok']:self.assertEqual(response['error']['required_skill'],'change-closure')
        finally:manifest.write_bytes(original)


if __name__=='__main__': unittest.main()
