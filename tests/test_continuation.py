# coding: utf-8
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = [ROOT / 'skills' / name / 'scripts/task-progress.py'
           for name in ('design-discussion', 'solution-design', 'guided-implementation')]
SPEC = importlib.util.spec_from_file_location('continuation_progress', SCRIPTS[0])
progress = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(progress)
START = '2020-01-01T10:00:00+08:00'
DEFAULT = {'continuous': True, 'mode_instruction': None, 'paused_stages': {}}


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'progress.md'
        self.data = progress.create_document(self.path, self.root, '选表功能，先讨论', '1', START)

    def cli(self, command, *arguments, script=None, success=True):
        before = self.path.read_bytes()
        result = subprocess.run([
            sys.executable, '-B', str(script or SCRIPTS[0]), command,
            '--file', str(self.path), '--task-id', self.data['task_id'], '--project', str(self.root),
            *arguments,
        ], capture_output=True, text=True, encoding='utf-8')
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')
            self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.path.with_name(self.path.name + '.lock').exists())
        return result

    def state(self, script=None):
        return json.loads(self.cli('show', script=script).stdout)['continuation']

    def finish_discussion(self, available='true'):
        points = {'goal': '增加选表功能', 'decisions': '无', 'scope': '先讨论选表规则',
                  'open_questions': '无', 'inferences': '无'}
        prepared = json.loads(self.cli('prepare-discussion', '--points', json.dumps(points), '--at', START).stdout)
        self.cli('confirm-discussion', '--confirmation-id', prepared['confirmation_id'],
                 '--reply', '确认', '--source', '用户对讨论要点的答复', '--at', START)
        self.cli('complete', '--stage', '1', '--steps', json.dumps([{'step': 2, 'note': '讨论结论已确认', 'at': START}]))
        return self.report('1', available)

    def report(self, stage, available='true'):
        arguments = [sys.executable, '-B', str(SCRIPTS[int(stage) - 1].with_name('render-result.py')),
                     '--file', str(self.path), '--task-id', self.data['task_id'], '--project', str(self.root),
                     '--summary', '选表规则及实现方案已确认', '--at', START]
        if stage == '1':
            arguments += ['--artifacts', '[]', '--recommended-stage', '2', '--recommendation', '需要方案',
                          '--next-skill-available', available]
        else:
            arguments += ['--artifacts', json.dumps([{'name': 'Spec', 'location': str(self.root / 'spec.md')}]),
                          '--implementation-skill-available', available]
        result = subprocess.run(arguments, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def finish_plan(self):
        self.cli('complete', '--stage', '2', '--steps', json.dumps([
            {'step': number, 'note': '方案及验收要求已确认', 'at': START} for number in (1, 2, 3)
        ]), script=SCRIPTS[1])
        report = self.report('2')
        self.cli('complete', '--stage', '2', '--steps', json.dumps([
            {'step': 4, 'note': '方案报告已展示', 'at': START}
        ]), script=SCRIPTS[1])
        return report

    def test_continuous_state_persists_through_waiting_confirmations_and_handoffs(self):
        self.assertEqual(self.state(), DEFAULT)
        before = self.path.read_bytes()
        for script in SCRIPTS:
            self.assertEqual(self.state(script), DEFAULT)
        self.assertEqual(self.path.read_bytes(), before)
        self.finish_discussion('unknown')
        self.cli('enter', '--stage', '2', '--at', START, script=SCRIPTS[1])
        self.assertIn('下一步将进入 3实现', self.finish_plan())
        self.cli('enter', '--stage', '3', '--at', START, script=SCRIPTS[2])
        for script in SCRIPTS:
            self.assertEqual(self.state(script), DEFAULT)
        shown = json.loads(self.cli('show').stdout)
        self.assertEqual([phase['stage'] for phase in shown['stages']], ['1', '2', '3'])

    def test_future_pause_preserves_mode_and_current_stage_work(self):
        self.cli('pause', '--stage', '3', '--reply', '方案完成后暂停实现', '--source', '用户暂停消息')
        self.assertTrue(self.state()['continuous'])
        self.finish_discussion()
        self.cli('enter', '--stage', '2', '--at', START)
        report = self.finish_plan()
        self.assertIn('3实现 已按用户要求暂停', report)
        self.cli('enter', '--stage', '3', '--at', START, success=False)
        self.cli('resume', '--stage', '3', '--reply', '恢复实现', '--source', '用户恢复消息')
        self.cli('enter', '--stage', '3', '--at', START)
        self.assertEqual(self.state(), DEFAULT)

    def test_pause_all_and_selective_resume_preserve_incomplete_progress(self):
        before = json.loads(self.cli('show').stdout)['stages']
        self.cli('pause', '--stage', 'all', '--reply', '所有阶段暂停', '--source', '用户消息')
        state = self.state()
        self.assertTrue(state['continuous'])
        self.assertEqual(set(state['paused_stages']), {'1', '2', '3'})
        paused = self.cli('complete', '--stage', '1', '--steps', '[{"step":2,"note":"讨论完成"}]', success=False)
        self.assertIn('暂停', paused.stderr)
        self.assertEqual(json.loads(self.cli('show').stdout)['stages'], before)
        self.cli('resume', '--stage', '1', '--reply', '恢复讨论', '--source', '用户后续消息')
        self.assertEqual(set(self.state()['paused_stages']), {'2', '3'})
        self.cli('resume', '--stage', 'all', '--reply', '全部恢复', '--source', '用户恢复消息')
        self.assertEqual(self.state(), DEFAULT)

    def test_manual_mode_persists_when_user_enters_a_later_stage(self):
        reply = '关闭连续模式，每阶段等我说下一步'
        self.cli('set-continuous', '--continuous', 'false', '--reply', reply, '--source', '用户关闭消息')
        original = self.state()
        self.assertFalse(original['continuous'])
        self.assertIn('等待用户指定下一步', self.finish_discussion())
        self.cli('enter', '--stage', '2', '--at', START, success=False)
        self.cli('enter', '--stage', '2', '--at', START, '--reply', '现在做方案', '--source', '用户进入方案消息')
        self.assertEqual(self.state(), original)
        phase = json.loads(self.cli('show').stdout)['stages'][-1]
        self.assertEqual(phase['entry_instruction'], {'text': '现在做方案', 'source': '用户进入方案消息'})
        self.assertIn('等待用户指定下一步', self.finish_plan())
        self.cli('enter', '--stage', '3', '--at', START, success=False)
        self.cli('set-continuous', '--continuous', 'true', '--reply', '开启连续模式', '--source', '用户开启消息')
        self.cli('enter', '--stage', '3', '--at', START)
        self.assertTrue(self.state()['continuous'])

    def test_mode_changes_and_pauses_preserve_each_others_instructions(self):
        self.cli('set-continuous', '--continuous', 'false', '--reply', '关闭连续模式', '--source', '模式消息')
        mode_instruction = self.state()['mode_instruction']
        self.cli('pause', '--stage', '2', '--reply', '方案暂停', '--source', '暂停消息')
        self.cli('resume', '--stage', '2', '--reply', '方案恢复', '--source', '恢复消息')
        self.assertEqual(self.state()['mode_instruction'], mode_instruction)
        self.assertFalse(self.state()['continuous'])
        self.cli('pause', '--stage', '3', '--reply', '实现暂停', '--source', '实现暂停消息')
        pauses = self.state()['paused_stages']
        self.cli('set-continuous', '--continuous', 'true', '--reply', '开启连续模式', '--source', '模式恢复消息')
        self.assertEqual(self.state()['paused_stages'], pauses)

    def test_state_changes_require_user_evidence_and_preserve_original_on_failure(self):
        for command, options in [('set-continuous', ['--continuous', 'false']),
                                 ('pause', ['--stage', '1']), ('resume', ['--stage', '1'])]:
            for evidence in ([], ['--reply', '暂停'], ['--source', '用户消息'],
                             ['--reply', '', '--source', '用户消息'], ['--reply', '暂停', '--source', '']):
                self.cli(command, *options, *evidence, success=False)
        before = deepcopy(self.data)
        with self.assertRaises(progress.ProgressError):
            progress.set_continuous(self.data, False, '关闭', '')
        self.assertEqual(self.data, before)
        for invalid in ('yes', 1, None):
            with self.assertRaises(progress.ProgressError):
                progress.set_continuous(self.data, invalid, '调整', '用户消息')
            self.assertEqual(self.data, before)

    def test_creation_can_record_explicit_manual_mode_and_reuse_keeps_state(self):
        path = self.root / 'manual.md'
        arguments = [sys.executable, '-B', str(SCRIPTS[0]), 'create', '--output', str(path),
                     '--project', str(self.root), '--summary', '选表功能', '--stage', '1', '--continuous', 'false']
        rejected = subprocess.run(arguments, capture_output=True, text=True, encoding='utf-8')
        self.assertNotEqual(rejected.returncode, 0)
        self.assertFalse(path.exists())
        accepted = subprocess.run(arguments + ['--reply', '关闭连续模式', '--source', '用户消息'],
                                  capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        state = progress.read_document(path)['continuation']
        reused = progress.create_document(path, self.root, '选表功能', '1', START)
        self.assertEqual(reused['continuation'], state)

    def test_paused_discussion_requires_resume_before_new_work(self):
        self.cli('pause', '--stage', '1', '--reply', '暂停讨论', '--source', '用户消息')
        result = self.cli('prepare-discussion', '--points', json.dumps({
            'goal': '选表', 'decisions': '无', 'scope': '选表', 'open_questions': '无', 'inferences': '无'
        }), success=False)
        self.assertIn('暂停', result.stderr)
        self.cli('enter', '--stage', '1', '--at', START, success=False)

    def test_legacy_endpoint_progress_is_preserved_for_its_original_version(self):
        content = self.path.read_text(encoding='utf-8')
        start, end = content.index('\n## 连续执行状态'), content.index('\n## 阶段时间')
        legacy = '\n## 执行范围\n\n```json\n' + json.dumps({
            'end_stage': '1', 'user_instruction': {'text': '先讨论', 'source': '旧消息'}
        }) + '\n```\n'
        self.path.write_text(content[:start] + legacy + content[end:], encoding='utf-8')
        result = self.cli('show', success=False)
        self.assertIn('旧版本', result.stderr)

    def test_saved_report_retains_original_state_after_explicit_pause(self):
        self.finish_discussion()
        original = progress.read_document(self.path)['stages'][0]['report']
        self.cli('pause', '--stage', '2', '--reply', '暂停方案', '--source', '用户新消息')
        self.assertEqual(progress.read_document(self.path)['stages'][0]['report'], original)

    def test_invalid_saved_states_are_rejected_without_rewriting_progress(self):
        original = deepcopy(self.data)
        for invalid in ({}, dict(DEFAULT, continuous=1), dict(DEFAULT, continuous=False),
                        dict(DEFAULT, paused_stages=[]), dict(DEFAULT, paused_stages={'4': {'text': '暂停', 'source': '用户'}}),
                        dict(DEFAULT, mode_instruction={'text': '关闭'}),
                        dict(DEFAULT, paused_stages={'2': {'text': '', 'source': '用户'}})):
            data = deepcopy(original)
            data['continuation'] = invalid
            self.path.write_text(progress.render(data), encoding='utf-8')
            self.cli('show', success=False)

    def test_retired_endpoint_commands_are_rejected_without_changing_progress(self):
        self.cli('set-scope', '--end-stage', '1', '--scope-reply', '先讨论', '--scope-source', '旧消息', success=False)


if __name__ == '__main__':
    unittest.main()
