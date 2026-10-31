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
SPEC = importlib.util.spec_from_file_location('scope_progress', SCRIPTS[0])
progress = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(progress)
START = '2020-01-01T10:00:00+08:00'


class ExecutionScopeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'progress.md'
        self.data = progress.create_document(self.path, self.root, '选表功能', '1', START)

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

    def finish_discussion(self):
        # 回放本次失误：模型自行把“仅讨论”混入要点，用户整体确认。
        points = {'goal': '增加选表功能', 'decisions': '无', 'scope': '本轮仅讨论，不启动实现',
                  'open_questions': '无', 'inferences': '无'}
        prepared = json.loads(self.cli('prepare-discussion', '--points', json.dumps(points), '--at', START).stdout)
        self.assertIn('默认连续执行至 3实现及收尾', prepared['confirmation_prompt'])
        self.cli('confirm-discussion', '--confirmation-id', prepared['confirmation_id'],
                 '--reply', '确认', '--source', '讨论要点后的用户答复', '--at', START)
        self.cli('complete', '--stage', '1', '--steps', json.dumps([{'step': 2, 'note': '已完成', 'at': START}]))
        result = subprocess.run([
            sys.executable, '-B', str(SCRIPTS[0].with_name('render-result.py')),
            '--file', str(self.path), '--task-id', self.data['task_id'], '--project', str(self.root),
            '--summary', '选表规则已确认', '--artifacts', '[]', '--recommended-stage', '2',
            '--recommendation', '需要方案', '--next-skill-available', 'true', '--at', START,
        ], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_overall_discussion_confirmation_does_not_disable_default_handoff(self):
        report = self.finish_discussion()
        self.assertIn('下一步将进入 2方案', report)
        self.assertNotIn('按用户要求在 1拷问 结束', report)
        self.cli('enter', '--stage', '2', '--at', START)
        for script in SCRIPTS:
            shown = json.loads(self.cli('show', script=script).stdout)
            self.assertEqual(shown['execution_scope'], {'end_stage': '3', 'user_instruction': None})

    def test_stage_limit_blocks_entry_until_user_explicitly_changes_scope(self):
        self.finish_discussion()
        self.cli('set-scope', '--end-stage', '2', '--scope-reply', '只做方案，先不要实现',
                 '--scope-source', '用户范围指令')
        self.cli('enter', '--stage', '2', '--at', START)
        for number in range(1, 5):
            self.cli('complete', '--stage', '2', '--steps', json.dumps([{'step': number, 'note': '已完成', 'at': START}]))
        self.cli('enter', '--stage', '3', '--at', START, success=False)
        before = json.loads(self.cli('show').stdout)['stages']
        self.cli('set-scope', '--end-stage', '3', '--scope-reply', '现在继续实现', '--scope-source', '用户后续指令')
        self.assertEqual(json.loads(self.cli('show').stdout)['stages'], before)
        self.cli('enter', '--stage', '3', '--at', START)

    def test_scope_change_requires_complete_user_evidence_and_is_atomic(self):
        for arguments in [[], ['--scope-reply', '只做方案'], ['--scope-source', '用户消息'],
                          ['--scope-reply', '', '--scope-source', '用户消息'],
                          ['--scope-reply', '只做方案', '--scope-source', '']]:
            self.cli('set-scope', '--end-stage', '2', *arguments, success=False)
        before = deepcopy(self.data)
        with self.assertRaises(progress.ProgressError):
            progress.set_scope(self.data, '2', '只做方案', '')
        self.assertEqual(self.data, before)

    def test_initial_limit_requires_evidence_and_create_does_not_reset_existing_scope(self):
        path = self.root / 'limited.md'
        prefix = [sys.executable, '-B', str(SCRIPTS[0]), 'create', '--output', str(path),
                  '--project', str(self.root), '--summary', '仅讨论', '--stage', '1', '--end-stage', '1']
        rejected = subprocess.run(prefix, capture_output=True, text=True, encoding='utf-8')
        self.assertNotEqual(rejected.returncode, 0)
        self.assertFalse(path.exists())
        accepted = subprocess.run(prefix + ['--scope-reply', '只讨论', '--scope-source', '用户原始消息'],
                                  capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        scope = progress.read_document(path)['execution_scope']
        reused = progress.create_document(path, self.root, '仅讨论', '1', START)
        self.assertEqual(reused['execution_scope'], scope)
        with self.assertRaises(progress.ProgressError):
            progress.create_document(path, self.root, '仅讨论', '1', START, scope=progress.make_execution_scope())

    def test_old_progress_without_scope_is_preserved_instead_of_assuming_unlimited_scope(self):
        content = self.path.read_text(encoding='utf-8')
        start, end = content.index('\n## 执行范围'), content.index('\n## 阶段时间')
        self.path.write_text(content[:start] + content[end:], encoding='utf-8')
        result = self.cli('show', success=False)
        self.assertIn('旧版本', result.stderr)

    def test_saved_report_keeps_original_scope_after_user_changes_it(self):
        self.finish_discussion()
        original = progress.read_document(self.path)['stages'][0]['report']
        self.cli('set-scope', '--end-stage', '2', '--scope-reply', '只做到方案', '--scope-source', '用户新指令')
        self.assertEqual(progress.read_document(self.path)['stages'][0]['report'], original)


if __name__ == '__main__':
    unittest.main()
