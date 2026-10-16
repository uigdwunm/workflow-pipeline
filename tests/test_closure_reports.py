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
from copy import deepcopy
from pathlib import Path
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[1]
SKILLS = ('design-discussion', 'solution-design', 'guided-implementation', 'change-closure')
PROGRESS_SCRIPTS = [REPOSITORY / 'skills' / skill / 'scripts/task-progress.py' for skill in SKILLS]
REPORT_SCRIPT = REPOSITORY / 'skills/change-closure/scripts/render-result.py'
SPEC = importlib.util.spec_from_file_location('closure_report', REPORT_SCRIPT)
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)
START = '2020-01-01T10:00:00+08:00'
END = '2020-01-01T10:05:00+08:00'
SUMMARY = '为配置加载增加必填项和格式校验'
REPORT_SUMMARY = '为配置加载增加必填项和格式校验，并交付实现与验证结果'
RESOURCES = '普通临时 Worktree 已清理；目标分支保留；Codex 托管 Worktree 不由本 Skill 管理'


class ClosureReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.worktree = self.root / 'worktree'
        self.worktree.mkdir()
        self.file = self.root / 'progress.md'
        self.closure = {
            'worktree': str(self.worktree), 'branch': 'codex/config-validation',
            'target_branch': 'main', 'implementation_commit': 'abcdef123',
            'artifacts': [{'name': 'Spec', 'location': str(self.worktree / 'spec.md')}],
        }
        self.artifacts = [{'name': '目标项目', 'location': str(self.project)},
                          {'name': '交付 PR', 'location': 'https://example.invalid/project/pull/42'}]
        self.create()

    def invoke(self, values, success=True):
        result = subprocess.run([sys.executable, '-B'] + values, capture_output=True, text=True, encoding='utf-8')
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')
        return result

    def create(self, stage='4', with_closure=True, summary=SUMMARY):
        values = [str(PROGRESS_SCRIPTS[int(stage) - 1]), 'create', '--output', str(self.file),
                  '--project', str(self.project), '--summary', summary, '--stage', stage, '--at', START]
        if with_closure:
            values += ['--closure', json.dumps(self.closure)]
        self.task_id = json.loads(self.invoke(values).stdout)['task_id']

    def progress(self, command, *arguments, script=None, success=True):
        return self.invoke([str(script or PROGRESS_SCRIPTS[3]), command, '--file', str(self.file),
                            '--task-id', self.task_id, '--project', str(self.project)] + list(arguments), success)

    def show(self, script=None):
        return json.loads(self.progress('show', script=script).stdout)

    def complete_four(self, stage='4', at='2020-01-01T10:04:00+08:00'):
        notes = ('实现提交、验证与审查已核实', '本地 main 无新增变化，文档已提交',
                 '本地交付提交 fedcba123，测试与审查通过；未执行远程发布', '本任务无需更新外部状态，已核对依据')
        for number, note in enumerate(notes, 1):
            self.progress('complete', '--stage', stage, '--step', str(number), '--at', at, '--note', note)

    def report_arguments(self, *arguments, task_id=None, project=None, artifacts=None, at=END,
                         summary=REPORT_SUMMARY):
        values = [str(REPORT_SCRIPT), '--file', str(self.file), '--task-id', task_id or self.task_id,
                  '--project', str(project or self.project), '--at', at,
                  '--artifacts', json.dumps(self.artifacts if artifacts is None else artifacts)]
        if summary is not None:
            values += ['--summary', summary]
        return values + list(arguments)

    def run_report(self, *arguments, success=True, **kwargs):
        return self.invoke(self.report_arguments(*arguments, **kwargs), success)

    def finished_report(self, **kwargs):
        return self.run_report('--complete', '--resources', RESOURCES, **kwargs)

    def assert_report_rejected(self, *arguments, **kwargs):
        before = self.file.read_bytes()
        self.run_report(*arguments, success=False, **kwargs)
        self.assertEqual(self.file.read_bytes(), before)

    def test_closure_input_is_in_same_file_and_readable_by_all_skill_copies(self):
        for script in PROGRESS_SCRIPTS:
            data = self.show(script=script)
            self.assertEqual(data['stages'][0]['closure'], self.closure)
            self.assertEqual(data['stages'][0]['steps'], [])
        self.assertEqual([path.name for path in self.root.iterdir() if path.is_file()], ['progress.md'])

    def test_closure_input_can_be_attached_before_first_step_and_is_not_overwritten(self):
        self.file.unlink()
        self.create(with_closure=False)
        self.progress('enter', '--stage', '4', '--closure', json.dumps(self.closure))
        before = self.file.read_bytes()
        self.progress('enter', '--stage', '4', '--closure', json.dumps(self.closure))
        self.assertEqual(self.file.read_bytes(), before)
        changed = {**self.closure, 'target_branch': 'release'}
        self.progress('enter', '--stage', '4', '--closure', json.dumps(changed), success=False)
        self.assertEqual(self.file.read_bytes(), before)
        self.file.unlink()
        self.create(with_closure=False)
        self.progress('complete', '--stage', '4', '--step', '1', '--note', '成果已核对')
        before = self.file.read_bytes()
        self.progress('enter', '--stage', '4', '--closure', json.dumps(self.closure), success=False)
        self.assertEqual(self.file.read_bytes(), before)

    def test_invalid_closure_input_never_changes_record(self):
        cases = [
            {**self.closure, 'worktree': 'relative'}, {**self.closure, 'branch': ''},
            {**self.closure, 'implementation_commit': 'invalid'}, {**self.closure, 'artifacts': []},
            {**self.closure, 'extra': 'unexpected'}, {**self.closure, 'branch': 'line\nbreak'},
            {**self.closure, 'artifacts': [{'name': 'Spec', 'location': 'javascript:alert(1)'}]},
            {**self.closure, 'artifacts': [{'name': 'Spec', 'location': 'https://example.invalid:bad/spec'}]},
        ]
        before = self.file.read_bytes()
        for value in cases:
            self.progress('enter', '--stage', '4', '--closure', json.dumps(value), success=False)
            self.assertEqual(self.file.read_bytes(), before)
        self.progress('enter', '--stage', '3', '--closure', json.dumps(self.closure), success=False)
        self.assertEqual(self.file.read_bytes(), before)

    def test_unicode_line_separators_are_rejected_in_closure_input_before_creation(self):
        before = self.file.read_bytes()
        for codepoint in (0x85, 0x2028, 0x2029):
            for field in ('branch', 'artifact_name'):
                with self.subTest(codepoint=hex(codepoint), field=field):
                    closure = deepcopy(self.closure)
                    if field == 'branch':
                        closure['branch'] = 'codex/config' + chr(codepoint) + 'validation'
                    else:
                        closure['artifacts'][0]['name'] = 'Spec' + chr(codepoint) + '续行'
                    output = self.root / f'rejected-{codepoint}-{field}.md'
                    result = self.invoke([
                        str(PROGRESS_SCRIPTS[3]), 'create', '--output', str(output),
                        '--project', str(self.project), '--summary', SUMMARY, '--stage', '4',
                        '--closure', json.dumps(closure)], success=False)
                    self.assertIn('单行文本', result.stderr)
                    self.assertFalse(output.exists())
                    self.assertFalse(output.with_name(output.name + '.lock').exists())
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(self.show()['stages'][0]['closure'], self.closure)

    def test_unicode_line_separators_in_report_inputs_do_not_end_or_corrupt_stage(self):
        self.complete_four()
        before = self.file.read_bytes()
        for codepoint in (0x85, 0x2028, 0x2029):
            with self.subTest(codepoint=hex(codepoint)):
                result = self.run_report('--complete', '--resources', '资源已处理' + chr(codepoint) + '续行',
                                         success=False)
                self.assertIn('单行文本', result.stderr)
                self.assertEqual(self.file.read_bytes(), before)
                artifact = [{'name': '成果' + chr(codepoint) + '续行', 'location': str(self.project)}]
                result = self.finished_report(artifacts=artifact, success=False)
                self.assertIn('单行文本', result.stderr)
                self.assertEqual(self.file.read_bytes(), before)
        result = self.finished_report()
        self.assertIn('**4归档已完成**', result.stdout)
        self.assertEqual(self.show()['stages'][0]['status'], '已完成')

    def test_report_starts_with_purpose_ends_stage_and_shows_only_used_stage(self):
        self.complete_four()
        result = self.finished_report()
        self.assertTrue(result.stdout.startswith('> **本次任务**\n'))
        self.assertLess(result.stdout.index(REPORT_SUMMARY), result.stdout.index('**4归档已完成**'))
        self.assertTrue(all(line.startswith('>') for line in result.stdout.splitlines()))
        self.assertIn('**4归档**：5 分钟 0 秒', result.stdout)
        self.assertIn('**流程累计用时**：5 分钟 0 秒', result.stdout)
        for stage in ('1拷问', '2方案', '3实现'):
            self.assertNotIn(stage, result.stdout)
        self.assertIn('本地交付提交 fedcba123', result.stdout)
        self.assertIn('测试与审查通过', result.stdout)
        self.assertIn('不由本 Skill 管理', result.stdout)
        phase = self.show()['stages'][0]
        self.assertEqual(phase['status'], '已完成')
        self.assertEqual(phase['finished_at'], END)
        self.assertEqual(phase['steps'][-1]['note'], RESOURCES)
        self.assertEqual(phase['steps'][-1]['finished_at'], END)
        self.assertTrue(self.file.exists())

    def test_report_uses_whole_task_summary_after_discussion_and_preserves_initial_summary(self):
        initial_summary = '讨论移除 readConfirmedSessionState；只讨论、不修改业务代码'
        whole_task_summary = '移除 readConfirmedSessionState 及重复控制状态，保留必要行为并交付实现与验证结果'
        self.file.unlink()
        self.create('1', with_closure=False, summary=initial_summary)
        for number in (1, 2, 3):
            self.progress('complete', '--stage', '1', '--step', str(number), '--note', '讨论结论已确认',
                          '--at', START)
        for stage in ('2', '3'):
            self.progress('enter', '--stage', stage, '--at', START)
            self.complete_four(stage, at=START)
        self.progress('enter', '--stage', '4', '--at', START, '--closure', json.dumps(self.closure))
        self.complete_four()
        previous_stages = deepcopy(self.show()['stages'][:-1])

        result = self.finished_report(summary=whole_task_summary)

        self.assertTrue(result.stdout.startswith('> **本次任务**\n>\n> ' + whole_task_summary + '\n'))
        self.assertNotIn(initial_summary, result.stdout)
        data = self.show()
        self.assertEqual(data['summary'], initial_summary)
        self.assertEqual(data['stages'][:-1], previous_stages)
        self.assertEqual(data['stages'][-1]['status'], '已完成')

    def test_missing_or_invalid_task_summary_emits_no_report_and_keeps_progress(self):
        self.complete_four()
        before = self.file.read_bytes()
        for summary in (None, '', '   ', '第一行\n第二行', '第一行\r第二行',
                        '任务\x85续行', '任务\u2028续行', '任务\u2029续行'):
            with self.subTest(summary=summary):
                result = self.finished_report(summary=summary, success=False)
                self.assertIn('--summary' if summary is None else '任务描述', result.stderr)
                self.assertEqual(self.file.read_bytes(), before)
                self.assertFalse(self.file.with_name(self.file.name + '.lock').exists())

    def test_handoff_report_aggregates_only_this_task_stages_and_excludes_stage_gaps(self):
        self.file.unlink()
        self.create('2', with_closure=False)
        self.complete_four('2', '2020-01-01T10:10:00+08:00')
        self.progress('enter', '--stage', '3', '--at', '2020-01-01T10:20:00+08:00')
        self.complete_four('3', '2020-01-01T10:35:00+08:00')
        self.progress('enter', '--stage', '4', '--at', '2020-01-01T10:50:00+08:00', '--closure', json.dumps(self.closure))
        self.complete_four('4', '2020-01-01T10:55:00+08:00')
        result = self.finished_report(at='2020-01-01T03:00:00Z')
        self.assertIn('**2方案**：10 分钟 0 秒', result.stdout)
        self.assertIn('**3实现**：15 分钟 0 秒', result.stdout)
        self.assertIn('**4归档**：10 分钟 0 秒', result.stdout)
        self.assertIn('**流程累计用时**：35 分钟 0 秒', result.stdout)
        self.assertNotIn('1拷问', result.stdout)
        self.assertEqual([phase['stage'] for phase in self.show()['stages']], ['2', '3', '4'])

    def test_report_retry_preserves_first_end_time_and_original_resource_result(self):
        self.complete_four()
        first = self.finished_report()
        before = self.file.read_bytes()
        repeated = self.finished_report(at='2020-01-01T10:20:00+08:00')
        readonly = self.run_report(at='2020-01-01T10:30:00+08:00')
        self.assertEqual(first.stdout, repeated.stdout)
        self.assertEqual(first.stdout, readonly.stdout)
        self.assertEqual(self.file.read_bytes(), before)
        self.assert_report_rejected('--resources', '另一资源结果')
        self.assert_report_rejected('--complete', '--resources', '另一资源结果')

    def test_incomplete_report_does_not_close_or_change_progress(self):
        for number in (1, 2):
            self.progress('complete', '--stage', '4', '--step', str(number), '--note', '已核对', '--at', START)
        before = self.file.read_bytes()
        result = self.run_report('--pending', '远程合并结果尚未核实，等待查询恢复', '--resources', '尚未清理资源')
        self.assertIn('**4归档未完成**', result.stdout)
        self.assertIn('阶段尚未结束', result.stdout)
        self.assertIn('远程合并结果尚未核实', result.stdout)
        self.assertIn(REPORT_SUMMARY, result.stdout)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertIsNone(self.show()['stages'][0]['finished_at'])
        self.assert_report_rejected('--complete', '--resources', RESOURCES)

    def test_completion_requires_inputs_four_steps_resources_and_no_pending_items(self):
        self.assert_report_rejected('--complete', '--resources', RESOURCES)
        self.complete_four()
        self.assert_report_rejected('--complete')
        self.assert_report_rejected('--complete', '--resources', '')
        self.assert_report_rejected('--complete', '--resources', RESOURCES, '--pending', '必要资源尚未清理')
        self.assert_report_rejected('--complete', '--resources', RESOURCES, at='2019-01-01T00:00:00Z')
        self.assert_report_rejected('--complete', '--resources', RESOURCES, at='2999-01-01T00:00:00Z')
        self.file.unlink()
        self.create(with_closure=False)
        self.complete_four()
        self.assert_report_rejected('--complete', '--resources', RESOURCES)
        self.run_report('--pending', '归档输入未核实')

    def test_cancelled_report_preserves_completed_steps_and_termination_time(self):
        self.progress('complete', '--stage', '4', '--step', '1', '--note', '成果已核实', '--at', START)
        self.progress('stop', '--stage', '4', '--note', '用户取消归档', '--at', END)
        before = self.file.read_bytes()
        result = self.run_report('--pending', '用户取消归档，合并交付未执行')
        self.assertIn('**4归档已终止**', result.stdout)
        self.assertIn(REPORT_SUMMARY, result.stdout)
        self.assertNotIn('阶段尚未结束', result.stdout)
        self.assertEqual(self.file.read_bytes(), before)
        self.assert_report_rejected('--complete', '--resources', RESOURCES)

    def test_identity_project_or_artifact_errors_emit_no_report_and_keep_file(self):
        self.complete_four()
        other = self.root / 'other'
        other.mkdir()
        self.assert_report_rejected('--complete', '--resources', RESOURCES, task_id='other-task')
        self.assert_report_rejected('--complete', '--resources', RESOURCES, project=other)
        for value in ([{'name': 'Spec', 'location': 'relative'}], [{'name': 'Spec', 'location': 'https://x:bad/y'}],
                      [{'name': 'Spec', 'location': 'javascript:alert(1)'}], [{'name': 'Spec', 'location': '/path', 'extra': 'x'}]):
            self.assert_report_rejected('--complete', '--resources', RESOURCES, artifacts=value)

    def test_corrupt_progress_emits_no_report_and_is_not_overwritten(self):
        self.file.write_text(self.file.read_text(encoding='utf-8') + '额外内容\n', encoding='utf-8')
        self.assert_report_rejected('--complete', '--resources', RESOURCES)

    def test_report_survives_worktree_removal_and_uses_final_artifact_locations(self):
        self.complete_four()
        artifact = self.project / 'spec.md'
        artifact.write_text('已交付方案', encoding='utf-8')
        shutil.rmtree(self.worktree)
        result = self.finished_report(artifacts=[{'name': 'Spec', 'location': str(artifact)}])
        self.assertIn(str(artifact), result.stdout)
        self.assertNotIn(str(self.worktree / 'spec.md'), result.stdout)
        self.assertEqual(self.show()['stages'][0]['closure'], self.closure)
        self.progress('cleanup', '--task-finished')
        self.assertFalse(self.file.exists())
        self.assertEqual(artifact.read_text(encoding='utf-8'), '已交付方案')
        self.assertTrue(result.stdout)

    def test_failed_atomic_update_emits_no_success_report_and_keeps_original(self):
        self.complete_four()
        before = self.file.read_bytes()
        arguments = ['render-result.py'] + self.report_arguments('--complete', '--resources', RESOURCES)[1:]
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, 'argv', arguments), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            with mock.patch.object(report.progress.os, 'replace', side_effect=OSError('写入失败')):
                with self.assertRaises(SystemExit) as raised:
                    report.main()
        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(output.getvalue(), '')
        self.assertIn('写入失败', errors.getvalue())
        self.assertEqual(self.file.read_bytes(), before)
        self.assertFalse(self.file.with_name(self.file.name + '.lock').exists())
        self.assertEqual(list(self.root.glob('.task-progress-*')), [])

    def test_output_failure_can_retry_report_without_changing_end_time(self):
        self.complete_four()
        arguments = ['render-result.py'] + self.report_arguments('--complete', '--resources', RESOURCES)[1:]
        with mock.patch.object(sys, 'argv', arguments), contextlib.redirect_stderr(io.StringIO()):
            with mock.patch('builtins.print', side_effect=OSError('输出失败')):
                with self.assertRaises(SystemExit):
                    report.main()
        phase = deepcopy(self.show()['stages'][0])
        self.assertEqual(phase['finished_at'], END)
        result = self.finished_report(at='2020-01-01T10:20:00+08:00')
        self.assertIn('**4归档已完成**', result.stdout)
        self.assertEqual(self.show()['stages'][0], phase)

    def test_script_pair_works_in_isolated_installation(self):
        self.complete_four()
        installation = self.root / 'installed'
        installation.mkdir()
        shutil.copyfile(REPORT_SCRIPT, installation / 'render-result.py')
        shutil.copyfile(PROGRESS_SCRIPTS[3], installation / 'task-progress.py')
        arguments = self.report_arguments('--complete', '--resources', RESOURCES)
        arguments[0] = str(installation / 'render-result.py')
        result = self.invoke(arguments)
        self.assertIn('**4归档已完成**', result.stdout)
        self.assertEqual(set(installation.iterdir()), {installation / 'render-result.py', installation / 'task-progress.py'})

    def test_markdown_metadata_results_and_artifacts_are_escaped(self):
        self.file.unlink()
        special = '文本 | [括号]、`代码`、<标签> 和 ' + chr(92)
        self.closure['branch'] = special
        self.closure['artifacts'] = [{'name': special, 'location': str(self.worktree / '[spec] |.md')}]
        self.create()
        self.complete_four()
        result = self.run_report('--complete', '--resources', special,
                                 artifacts=[{'name': special, 'location': str(self.project / '[spec] |.md')}],
                                 summary=special)
        self.assertTrue(result.stdout.startswith('> **本次任务**\n>\n> ' + report.progress.escape(special) + '\n'))
        self.assertIn(report.progress.escape(special), result.stdout)
        self.assertIn('%5Bspec%5D%20%7C.md', result.stdout)
        self.assertEqual(self.show()['stages'][0]['closure'], self.closure)


if __name__ == '__main__':
    unittest.main()
