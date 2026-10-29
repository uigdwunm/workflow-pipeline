# coding: utf-8
import contextlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY / 'skills/design-discussion/scripts/render-result.py'
SPEC = importlib.util.spec_from_file_location('discussion_report', SCRIPT)
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)
progress = report.progress
START = '2020-01-01T10:00:00+08:00'
INTERVIEW_END = '2020-01-01T10:04:00+08:00'
END = '2020-01-01T10:05:00+08:00'


class DiscussionReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.file = self.root / 'progress.md'
        self.data = progress.create_document(self.file, self.project, '首页整理', '1', START)
        self.task_id = self.data['task_id']
        points = {'goal': '检查首页结构', 'decisions': '保持业务行为', 'scope': '只讨论',
                  'open_questions': '如何划分职责', 'inferences': '无'}
        progress.prepare_discussion(self.data, points, START)
        progress.confirm_discussion(self.data, self.data['stages'][0]['discussions'][0]['id'],
                                    '确认以上要点', '本轮展示后的用户答复', START)
        progress.complete_steps(self.data, '1', [{'step': 2, 'note': '讨论结论已确认', 'at': INTERVIEW_END}])
        progress.write_document(self.file, self.data)

    def arguments(self, at=END, **overrides):
        values = {
            'file': str(self.file), 'task-id': self.task_id, 'project': str(self.project),
            'summary': '已确认职责边界与验收范围', 'artifacts': '[]',
            'recommended-stage': '2', 'recommendation': '需要细化技术方案',
            'continuous-mode': 'false', 'next-skill-available': 'unknown', 'at': at,
        }
        values.update(overrides)
        return [part for key, value in values.items() if value is not None for part in ('--' + key, value)]

    def invoke(self, success=True, script=SCRIPT, **kwargs):
        result = subprocess.run([sys.executable, '-B', str(script), *self.arguments(**kwargs)],
                                capture_output=True, text=True, encoding='utf-8')
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.file.with_name(self.file.name + '.lock').exists())
        return result

    def assert_rejected(self, **kwargs):
        before = self.file.read_bytes()
        result = self.invoke(success=False, **kwargs)
        self.assertEqual(result.stdout, '')
        self.assertEqual(self.file.read_bytes(), before)
        return result

    def test_report_is_saved_in_same_progress_and_closes_stage_with_recorded_times(self):
        result = self.invoke()
        phase = progress.read_document(self.file)['stages'][0]
        self.assertEqual(phase['status'], '已完成')
        self.assertEqual(phase['finished_at'], END)
        self.assertEqual(phase['steps'][-1]['id'], '1.3')
        self.assertEqual(phase['report']['markdown'], result.stdout)
        self.assertIn('2020-01-01 10:00:00+08:00', result.stdout)
        self.assertIn('**总耗时**：5 分钟 0 秒', result.stdout)
        self.assertIn('建议进入 **2方案**', result.stdout)
        self.assertIn('本次按用户要求在 1拷问 结束。', result.stdout)
        self.assertEqual([path.name for path in self.root.iterdir() if path.is_file()], ['progress.md'])

    def test_plain_complete_and_batch_cannot_replace_report_generation(self):
        before = self.file.read_bytes()
        for entries in ([{'step': 3, 'note': '报告已生成'}],
                        [{'step': 2, 'note': '讨论结论已确认'}, {'step': 3, 'note': '报告已生成'}]):
            result = subprocess.run([
                sys.executable, '-B', str(SCRIPT.with_name('task-progress.py')), 'complete',
                '--file', str(self.file), '--task-id', self.task_id, '--project', str(self.project),
                '--stage', '1', '--steps', json.dumps(entries),
            ], capture_output=True, text=True, encoding='utf-8')
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')
            self.assertEqual(self.file.read_bytes(), before)

    def test_incomplete_interview_or_unconfirmed_points_cannot_emit_completion(self):
        self.data['stages'][0]['steps'].pop()
        progress.write_document(self.file, self.data)
        self.assert_rejected()
        self.data['stages'][0]['steps'].clear()
        self.data['stages'][0]['discussions'][0]['confirmation'] = None
        progress.write_document(self.file, self.data)
        self.assert_rejected()

    def test_identity_stage_time_and_input_errors_keep_original_progress(self):
        for kwargs in ({'task-id': 'other'}, {'project': str(self.root)},
                       {'at': START}, {'at': '2999-01-01T00:00:00Z'},
                       {'summary': ''}, {'recommendation': ''}, {'artifacts': '[{}]'},
                       {'next-skill-available': 'invalid'}):
            with self.subTest(kwargs=kwargs):
                self.assert_rejected(**kwargs)
        progress.stop_stage(self.data, '1', '用户取消', INTERVIEW_END)
        progress.write_document(self.file, self.data)
        self.assert_rejected()
        progress.enter_stage(self.data, '2', END)
        progress.write_document(self.file, self.data)
        self.assert_rejected()

    def test_retry_replays_saved_markdown_without_extending_time_or_changing_inputs(self):
        first = self.invoke()
        before = self.file.read_bytes()
        retried = self.invoke(at='2020-01-01T10:10:00+08:00')
        self.assertEqual(first.stdout, retried.stdout)
        self.assertEqual(self.file.read_bytes(), before)
        self.assert_rejected(summary='另一个结论')
        self.assert_rejected(**{'recommended-stage': '3'})

    def test_output_failure_keeps_stage_open_and_retry_can_finish(self):
        before = self.file.read_bytes()
        with mock.patch.object(sys, 'argv', [str(SCRIPT), *self.arguments()]), \
                mock.patch('builtins.print', side_effect=OSError('output unavailable')), \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                report.main()
        self.assertNotEqual(error.exception.code, 0)
        self.assertEqual(self.file.read_bytes(), before)
        self.invoke()

    def test_write_failure_preserves_original_progress_and_returns_nonzero(self):
        before = self.file.read_bytes()
        captured = io.StringIO()
        with mock.patch.object(sys, 'argv', [str(SCRIPT), *self.arguments()]), \
                mock.patch.object(progress, 'write_document', side_effect=OSError('disk unavailable')), \
                contextlib.redirect_stdout(captured), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                report.main()
        self.assertNotEqual(error.exception.code, 0)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertIn('**下一步**', captured.getvalue())
        self.invoke()

    def test_invalid_stored_report_is_rejected_without_overwriting(self):
        self.invoke()
        data = progress.read_document(self.file)
        data['stages'][0]['report']['input_sha256'] = 'bad-digest'
        self.file.write_text(progress.render(data), encoding='utf-8')
        self.assert_rejected()

    def test_standalone_report_pair_and_following_skills_read_same_saved_report(self):
        installation = self.root / 'installed'
        installation.mkdir()
        for source in (SCRIPT, SCRIPT.with_name('task-progress.py')):
            shutil.copyfile(source, installation / source.name)
        result = self.invoke(script=installation / SCRIPT.name)
        for name in ('solution-design', 'guided-implementation'):
            script = REPOSITORY / 'skills' / name / 'scripts/task-progress.py'
            shown = subprocess.run([
                sys.executable, '-B', str(script), 'show', '--file', str(self.file),
                '--task-id', self.task_id, '--project', str(self.project),
            ], capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(shown.returncode, 0, shown.stderr)
            self.assertEqual(json.loads(shown.stdout)['stages'][0]['report']['markdown'], result.stdout)


if __name__ == '__main__':
    unittest.main()
