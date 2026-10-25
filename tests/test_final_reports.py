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
SKILLS = ('design-discussion', 'solution-design', 'guided-implementation')
PROGRESS_SCRIPTS = [REPOSITORY / 'skills' / skill / 'scripts/task-progress.py' for skill in SKILLS]
REPORT_SCRIPT = REPOSITORY / 'skills/guided-implementation/scripts/render-result.py'
SPEC = importlib.util.spec_from_file_location('final_report', REPORT_SCRIPT)
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)
START = '2020-01-01T10:00:00+08:00'
END = '2020-01-01T10:05:00+08:00'
SUMMARY = '为配置加载增加必填项和格式校验'
REPORT_SUMMARY = '为配置加载增加必填项和格式校验，并交付实现与验证结果'
RESOURCES = '普通临时 Worktree 已清理；目标分支保留；Codex 托管 Worktree 不由本 Skill 管理'
STEP_NOTES = (
    '工作区和审查基准已核实',
    '实现已提交，首次审查完成',
    '实现提交 abcdef123，验证与审查整改通过',
    '本地 main 无新增变化，文档已提交',
    '本地交付提交 fedcba123，整合检查通过；未执行远程发布',
    '本任务无需更新外部状态，已核对依据',
    RESOURCES,
)


class FinalReportTests(unittest.TestCase):
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
        self.artifacts = [{'name': '目标项目', 'location': str(self.project)}]
        self.create()

    def invoke(self, values, success=True):
        result = subprocess.run([sys.executable, '-B'] + values, capture_output=True, text=True, encoding='utf-8')
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')
        return result

    def create(self, stage='3', with_closure=True, summary=SUMMARY):
        values = [str(PROGRESS_SCRIPTS[int(stage) - 1]), 'create', '--output', str(self.file),
                  '--project', str(self.project), '--summary', summary, '--stage', stage, '--at', START]
        self.task_id = json.loads(self.invoke(values).stdout)['task_id']
        if with_closure:
            self.complete_through(3, at=START)
            self.attach_closure()

    def progress(self, command, *arguments, script=None, success=True):
        return self.invoke([str(script or PROGRESS_SCRIPTS[2]), command, '--file', str(self.file),
                            '--task-id', self.task_id, '--project', str(self.project)] + list(arguments), success)

    def show(self, script=None):
        return json.loads(self.progress('show', script=script).stdout)

    def attach_closure(self, value=None, **kwargs):
        return self.progress('enter', '--stage', '3', '--closure',
                             json.dumps(self.closure if value is None else value), **kwargs)

    def complete_through(self, last=7, at='2020-01-01T10:04:00+08:00'):
        completed = len(self.show()['stages'][-1]['steps'])
        for number in range(completed + 1, last + 1):
            self.progress('complete', '--stage', '3', '--steps', json.dumps([{'step': number, 'at': at, 'note': STEP_NOTES[number - 1]}]))

    def register_report(self, at=END):
        self.progress('complete', '--stage', '3', '--steps', json.dumps([{'step': 8, 'at': at, 'note': '最终报告已生成并保留'}]))

    def report_arguments(self, *arguments, task_id=None, project=None, artifacts=None, at=END,
                         summary=REPORT_SUMMARY, pending='无', delivery_method='本地合并'):
        values = [str(REPORT_SCRIPT), '--file', str(self.file), '--task-id', task_id or self.task_id,
                  '--project', str(project or self.project), '--at', at,
                  '--artifacts', json.dumps(self.artifacts if artifacts is None else artifacts)]
        for flag, value in (('--summary', summary), ('--pending', pending), ('--delivery-method', delivery_method)):
            if value is not None:
                values += [flag, value]
        return values + list(arguments)

    def run_report(self, *arguments, success=True, **kwargs):
        before = self.file.read_bytes()
        result = self.invoke(self.report_arguments(*arguments, **kwargs), success)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertFalse(self.file.with_name(self.file.name + '.lock').exists())
        return result

    def finished_report(self, **kwargs):
        return self.run_report('--complete', **kwargs)

    def test_closure_input_is_in_same_file_and_readable_by_all_skill_copies(self):
        for script in PROGRESS_SCRIPTS:
            data = self.show(script=script)
            self.assertEqual(data['stages'][0]['closure'], self.closure)
            self.assertEqual(len(data['stages'][0]['steps']), 3)
        self.assertEqual([path.name for path in self.root.iterdir() if path.is_file()], ['progress.md'])

    def test_closure_input_requires_completed_step_three_and_preserves_existing_input(self):
        self.file.unlink()
        self.create(with_closure=False)
        for count in range(4):
            before = self.file.read_bytes()
            if count < 3:
                result = self.attach_closure(success=False)
                self.assertIn('第 3 步后', result.stderr)
                self.assertEqual(self.file.read_bytes(), before)
                self.complete_through(count + 1, at=START)
            else:
                self.attach_closure()
        for script in PROGRESS_SCRIPTS:
            before = self.file.read_bytes()
            self.attach_closure(script=script)
            self.assertEqual(self.file.read_bytes(), before)
            self.attach_closure({**self.closure, 'target_branch': 'release'}, script=script, success=False)
            self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(self.show()['stages'][0]['started_at'], START)

    def test_first_closure_input_is_rejected_after_step_four_or_termination(self):
        for stopped in (False, True):
            with self.subTest(stopped=stopped):
                self.file.unlink()
                self.create(with_closure=False)
                self.complete_through(3 if stopped else 4, at=START)
                if stopped:
                    self.progress('stop', '--stage', '3', '--note', '用户取消', '--at', END)
                before = self.file.read_bytes()
                self.attach_closure(success=False)
                self.assertEqual(self.file.read_bytes(), before)

    def test_existing_closure_input_can_be_retried_after_later_steps(self):
        self.complete_through()
        self.finished_report()
        self.register_report()
        before = self.file.read_bytes()
        self.attach_closure()
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
            self.attach_closure(value, success=False)
            self.assertEqual(self.file.read_bytes(), before)
        for stage in ('1', '2'):
            self.progress('enter', '--stage', stage, '--closure', json.dumps(self.closure), success=False)
            self.assertEqual(self.file.read_bytes(), before)

    def test_unicode_line_separators_are_rejected_in_closure_input(self):
        before = self.file.read_bytes()
        for codepoint in (0x85, 0x2028, 0x2029):
            for field in ('branch', 'artifact_name'):
                with self.subTest(codepoint=hex(codepoint), field=field):
                    closure = deepcopy(self.closure)
                    if field == 'branch':
                        closure['branch'] = 'codex/config' + chr(codepoint) + 'validation'
                    else:
                        closure['artifacts'][0]['name'] = 'Spec' + chr(codepoint) + '续行'
                    result = self.attach_closure(closure, success=False)
                    self.assertIn('单行文本', result.stderr)
                    self.assertEqual(self.file.read_bytes(), before)
                    self.assertFalse(self.file.with_name(self.file.name + '.lock').exists())

    def test_unicode_line_separators_in_report_inputs_do_not_change_progress(self):
        self.complete_through()
        for codepoint in (0x85, 0x2028, 0x2029):
            with self.subTest(codepoint=hex(codepoint)):
                for kwargs in ({'delivery_method': '本地' + chr(codepoint) + '合并'},
                               {'pending': '无' + chr(codepoint) + '续行'},
                               {'artifacts': [{'name': '成果' + chr(codepoint) + '续行', 'location': str(self.project)}]}):
                    result = self.finished_report(success=False, **kwargs)
                    self.assertIn('单行文本', result.stderr)
        self.assertIn('**3实现已完成**', self.finished_report().stdout)
        self.assertEqual(self.show()['stages'][0]['status'], '进行中')

    def test_report_shows_purpose_result_sections_and_only_used_stage_without_closing_it(self):
        self.complete_through()
        result = self.finished_report()
        self.assertTrue(result.stdout.startswith('**本次任务**\n\n' + REPORT_SUMMARY + '\n'))
        self.assertLess(result.stdout.index(REPORT_SUMMARY), result.stdout.index('**3实现已完成**'))
        self.assertIn('**交付方式**：本地合并', result.stdout)
        self.assertIn('**目标分支**：main', result.stdout)
        sections = ('实现与验收', '交付结果', '项目状态', '资源处理', '成果位置', '未完成事项', '阶段用时')
        positions = [result.stdout.index('**' + name + '**') for name in sections]
        self.assertEqual(positions, sorted(positions))
        for number, section in ((2, 0), (3, 0), (4, 1), (5, 1), (6, 2), (7, 3)):
            body = result.stdout[positions[section]:positions[section + 1]]
            self.assertIn(report.progress.escape(STEP_NOTES[number - 1]), body)
        self.assertIn('**已验收实现提交**：abcdef123', result.stdout)
        self.assertIn('| 3实现 | 5 分钟 |', result.stdout)
        self.assertIn('| **累计** | **5 分钟** |', result.stdout)
        for absent in ('1拷问', '2方案', '4归档', 'change-closure', '是否开启', '是否进入'):
            self.assertNotIn(absent, result.stdout)
        phase = self.show()['stages'][0]
        self.assertEqual(phase['status'], '进行中')
        self.assertIsNone(phase['finished_at'])
        self.assertEqual(len(phase['steps']), 7)
        self.register_report()
        phase = self.show()['stages'][0]
        self.assertEqual(phase['finished_at'], END)
        self.assertEqual(phase['steps'][6]['note'], RESOURCES)
        self.assertEqual(phase['steps'][7]['finished_at'], END)

    def test_multiple_runs_aggregate_duration_and_preserve_terminated_history(self):
        self.progress('stop', '--stage', '3', '--note', '重新核对交付', '--at', END)
        first_run = deepcopy(self.show()['stages'][0])
        restarted = '2020-01-01T10:20:00+08:00'
        self.progress('enter', '--stage', '3', '--new-run', '--at', restarted)
        ongoing = self.run_report(at='2020-01-01T02:23:00+00:00', pending='交付待核实')
        self.assertIn('| 3实现 | 累计 8 分钟，共 2 轮（含已终止轮次）', ongoing.stdout)
        self.assertIn('阶段尚未结束', ongoing.stdout)
        self.complete_through(3, at='2020-01-01T02:24:00+00:00')
        self.attach_closure()
        self.complete_through(at='2020-01-01T02:24:00+00:00')
        ended = '2020-01-01T02:25:00+00:00'
        result = self.finished_report(at=ended)
        self.assertIn('| 3实现 | 累计 10 分钟，共 2 轮（含已终止轮次） |', result.stdout)
        self.assertIn('| **累计** | **10 分钟** |', result.stdout)
        self.register_report(at=ended)
        data = self.show()
        self.assertEqual(data['stages'][0], first_run)
        self.assertEqual(data['stages'][-1]['started_at'], restarted)
        self.assertEqual(data['stages'][-1]['finished_at'], ended)
        self.assertEqual(data['total_duration_seconds'], 600)

    def test_multiple_completed_runs_do_not_claim_terminated_runs(self):
        self.complete_through()
        self.finished_report()
        self.register_report()
        self.progress('enter', '--stage', '3', '--new-run', '--at', '2020-01-01T10:20:00+08:00')
        self.complete_through(3, at='2020-01-01T10:24:00+08:00')
        self.attach_closure()
        self.complete_through(at='2020-01-01T10:24:00+08:00')
        result = self.finished_report(at='2020-01-01T10:25:00+08:00')
        self.assertIn('| 3实现 | 累计 10 分钟，共 2 轮 |', result.stdout)
        self.assertNotIn('含已终止轮次', result.stdout)

    def test_equivalent_time_offsets_produce_same_duration_and_preserve_registered_time(self):
        self.complete_through()
        first = self.finished_report()
        ended = '2020-01-01T02:05:00+00:00'
        result = self.finished_report(at=ended)
        self.assertEqual(first.stdout, result.stdout)
        self.assertNotIn('共 1 轮', result.stdout)
        self.register_report(at=ended)
        self.assertEqual(self.show()['stages'][0]['finished_at'], ended)
        self.assertEqual(self.run_report(at=END).stdout, first.stdout)

    def test_report_uses_whole_task_summary_and_preserves_initial_summary_and_previous_stages(self):
        initial_summary = '讨论移除重复状态；只讨论、不修改业务代码'
        whole_task_summary = '移除重复状态，保留必要行为并交付实现与验证结果'
        self.file.unlink()
        self.create('1', with_closure=False, summary=initial_summary)
        for number in (1, 2, 3):
            self.progress('complete', '--stage', '1', '--steps', json.dumps([{'step': number, 'note': '讨论结论已确认', 'at': START}]))
        self.progress('enter', '--stage', '2', '--at', START)
        for number in (1, 2, 3, 4):
            self.progress('complete', '--stage', '2', '--steps', json.dumps([{'step': number, 'note': '方案已确认', 'at': START}]))
        self.progress('enter', '--stage', '3', '--at', START)
        self.complete_through(3, at=START)
        self.attach_closure()
        self.complete_through()
        previous_stages = deepcopy(self.show()['stages'][:-1])
        result = self.finished_report(summary=whole_task_summary)
        self.assertTrue(result.stdout.startswith('**本次任务**\n\n' + whole_task_summary + '\n'))
        self.assertNotIn(initial_summary, result.stdout)
        data = self.show()
        self.assertEqual(data['summary'], initial_summary)
        self.assertEqual(data['stages'][:-1], previous_stages)
        self.assertEqual(data['stages'][-1]['status'], '进行中')

    def test_missing_or_invalid_task_summary_emits_no_report_and_keeps_progress(self):
        self.complete_through()
        for summary in (None, '', '   ', '第一行\n第二行', '第一行\r第二行',
                        '任务\x85续行', '任务\u2028续行', '任务\u2029续行'):
            with self.subTest(summary=summary):
                result = self.finished_report(summary=summary, success=False)
                self.assertIn('--summary' if summary is None else '任务描述', result.stderr)

    def test_report_aggregates_only_this_task_stages_and_excludes_stage_gaps(self):
        self.file.unlink()
        self.create('2', with_closure=False)
        for number in (1, 2, 3, 4):
            self.progress('complete', '--stage', '2', '--steps', json.dumps([{'step': number, 'note': '方案完成', 'at': '2020-01-01T10:10:00+08:00'}]))
        self.progress('enter', '--stage', '3', '--at', '2020-01-01T10:20:00+08:00')
        self.complete_through(3, at='2020-01-01T10:35:00+08:00')
        self.attach_closure()
        self.complete_through(at='2020-01-01T10:35:00+08:00')
        result = self.finished_report(at='2020-01-01T02:45:00Z')
        self.assertIn('| 2方案 | 10 分钟 |', result.stdout)
        self.assertIn('| 3实现 | 25 分钟 |', result.stdout)
        self.assertIn('| **累计** | **35 分钟** |', result.stdout)
        self.assertNotIn('1拷问', result.stdout)
        self.assertEqual([phase['stage'] for phase in self.show()['stages']], ['2', '3'])

    def test_report_retry_uses_registered_end_time_and_recorded_resource_result(self):
        self.complete_through()
        first = self.finished_report()
        self.assertEqual(self.finished_report().stdout, first.stdout)
        self.register_report()
        repeated = self.finished_report(at='2020-01-01T10:20:00+08:00')
        readonly = self.run_report(at='2020-01-01T10:30:00+08:00')
        self.assertEqual(first.stdout, repeated.stdout)
        self.assertEqual(first.stdout, readonly.stdout)
        self.assertEqual(self.show()['stages'][0]['steps'][6]['note'], RESOURCES)
        self.run_report('--resources', '另一资源结果', success=False)
        self.run_report(pending='新的未完成事项', success=False)

    def test_incomplete_report_does_not_close_or_change_progress(self):
        result = self.run_report(delivery_method=None, pending='远程合并结果尚未核实，等待查询恢复')
        self.assertIn('**3实现未完成**', result.stdout)
        self.assertIn('阶段尚未结束', result.stdout)
        self.assertIn('**交付方式**：尚未核实', result.stdout)
        self.assertIn('尚无完成记录', result.stdout)
        self.assertIn('远程合并结果尚未核实', result.stdout)
        self.assertIn(REPORT_SUMMARY, result.stdout)
        self.assertIsNone(self.show()['stages'][0]['finished_at'])
        self.finished_report(success=False)
        self.run_report(pending='无', success=False)

    def test_completion_requires_explicit_pending_assessment(self):
        self.complete_through()
        result = self.finished_report(pending=None, success=False)
        self.assertIn('--pending', result.stderr)
        self.assertIsNone(self.show()['stages'][0]['finished_at'])

    def test_pending_scope_question_blocks_completion_and_preserves_current_run(self):
        self.complete_through()
        pending = '等待用户决定是否同步移除两处客户端语言判断；答复后继续验收和资源收尾'
        rejected = self.finished_report(pending=pending, success=False)
        self.assertIn('存在未完成事项', rejected.stderr)
        self.assertIn('**3实现已完成**', self.finished_report().stdout)
        phase = self.show()['stages'][0]
        self.assertEqual(phase['status'], '进行中')
        self.assertEqual(phase['started_at'], START)
        self.assertEqual(phase['run'], 1)

    def test_completion_requires_inputs_seven_steps_delivery_method_and_no_pending_items(self):
        for count in range(3, 7):
            self.complete_through(count)
            self.finished_report(success=False)
        self.complete_through()
        for kwargs in ({'delivery_method': None}, {'delivery_method': ''}, {'pending': '必要资源尚未清理'},
                       {'at': '2020-01-01T10:03:00+08:00'}, {'at': '2999-01-01T00:00:00Z'}):
            with self.subTest(kwargs=kwargs):
                self.finished_report(success=False, **kwargs)
        self.file.unlink()
        self.create(with_closure=False)
        self.complete_through()
        self.finished_report(success=False)
        self.run_report(pending='归档输入未核实')

    def test_report_requires_current_implementation_stage(self):
        self.file.unlink()
        self.create('2', with_closure=False)
        result = self.run_report(pending='尚未实现', success=False)
        self.assertIn('当前阶段不是 3实现', result.stderr)

    def test_cancelled_report_preserves_completed_steps_and_termination_time(self):
        self.progress('stop', '--stage', '3', '--note', '用户取消任务', '--at', END)
        result = self.run_report(pending='用户取消任务，合并交付未执行')
        self.assertIn('**3实现已终止**', result.stdout)
        self.assertIn(REPORT_SUMMARY, result.stdout)
        self.assertNotIn('阶段尚未结束', result.stdout)
        self.assertEqual(self.show()['stages'][0]['finished_at'], END)
        self.finished_report(success=False)
        self.run_report(pending='无', success=False)
        self.run_report(at=START, pending='用户取消任务', success=False)

    def test_identity_project_or_artifact_errors_emit_no_report_and_keep_file(self):
        self.complete_through()
        other = self.root / 'other'
        other.mkdir()
        self.finished_report(task_id='other-task', success=False)
        self.finished_report(project=other, success=False)
        for value in ([{'name': 'Spec', 'location': 'relative'}], [{'name': 'Spec', 'location': 'https://x:bad/y'}],
                      [{'name': 'Spec', 'location': 'javascript:alert(1)'}], [{'name': 'Spec', 'location': '/path', 'extra': 'x'}]):
            self.finished_report(artifacts=value, success=False)

    def test_empty_final_artifacts_are_allowed(self):
        self.complete_through()
        result = self.finished_report(artifacts=[])
        self.assertIn('**3实现已完成**', result.stdout)
        self.assertIn('无额外文件或链接', result.stdout)

    def test_corrupt_progress_emits_no_report_and_is_not_overwritten(self):
        self.file.write_text(self.file.read_text(encoding='utf-8') + '额外内容\n', encoding='utf-8')
        self.finished_report(success=False)

    def test_report_survives_worktree_removal_and_uses_final_artifact_locations(self):
        self.complete_through()
        artifact = self.project / 'spec.md'
        artifact.write_text('已交付方案', encoding='utf-8')
        shutil.rmtree(self.worktree)
        final_artifacts = [{'name': 'Spec', 'location': str(artifact)},
                           {'name': '交付 PR', 'location': 'https://example.invalid/project/pull/42'}]
        result = self.finished_report(artifacts=final_artifacts)
        for item in final_artifacts:
            self.assertIn(item['location'], result.stdout)
        self.assertNotIn(str(self.worktree / 'spec.md'), result.stdout)
        self.assertEqual(self.show()['stages'][0]['closure'], self.closure)
        self.progress('cleanup', '--task-finished', success=False)
        self.register_report()
        self.progress('cleanup', '--task-finished')
        self.assertFalse(self.file.exists())
        self.assertEqual(artifact.read_text(encoding='utf-8'), '已交付方案')
        self.assertTrue(result.stdout)

    def test_report_never_calls_progress_completion_or_write_operations(self):
        self.complete_through()
        before = self.file.read_bytes()
        arguments = ['render-result.py'] + self.report_arguments('--complete')[1:]
        output = io.StringIO()
        with mock.patch.object(sys, 'argv', arguments), contextlib.redirect_stdout(output):
            with mock.patch.object(report.progress, 'complete_steps', side_effect=AssertionError('不应登记进度')) as complete:
                with mock.patch.object(report.progress, 'write_document', side_effect=AssertionError('不应写入进度')) as write:
                    report.main()
        complete.assert_not_called()
        write.assert_not_called()
        self.assertIn('**3实现已完成**', output.getvalue())
        self.assertEqual(self.file.read_bytes(), before)
        self.assertFalse(self.file.with_name(self.file.name + '.lock').exists())
        self.assertEqual(list(self.root.glob('.task-progress-*')), [])

    def test_output_failure_keeps_stage_open_and_retry_does_not_register_completion(self):
        self.complete_through()
        before = self.file.read_bytes()
        arguments = ['render-result.py'] + self.report_arguments('--complete')[1:]
        with mock.patch.object(sys, 'argv', arguments), contextlib.redirect_stderr(io.StringIO()):
            with mock.patch('builtins.print', side_effect=OSError('输出失败')):
                with self.assertRaises(SystemExit) as raised:
                    report.main()
        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertIsNone(self.show()['stages'][0]['finished_at'])
        self.assertFalse(self.file.with_name(self.file.name + '.lock').exists())
        result = self.finished_report(at='2020-01-01T10:20:00+08:00')
        self.assertIn('**3实现已完成**', result.stdout)
        self.assertIsNone(self.show()['stages'][0]['finished_at'])

    def test_script_pair_works_in_isolated_installation(self):
        self.complete_through()
        installation = self.root / 'installed'
        installation.mkdir()
        shutil.copyfile(REPORT_SCRIPT, installation / 'render-result.py')
        shutil.copyfile(PROGRESS_SCRIPTS[2], installation / 'task-progress.py')
        arguments = self.report_arguments('--complete')
        arguments[0] = str(installation / 'render-result.py')
        before = self.file.read_bytes()
        result = self.invoke(arguments)
        self.assertIn('**3实现已完成**', result.stdout)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(set(installation.iterdir()), {installation / 'render-result.py', installation / 'task-progress.py'})

    def test_markdown_metadata_results_and_artifacts_are_escaped(self):
        special = '文本 | [括号]、`代码`、<标签> 和 ' + chr(92)
        self.file.unlink()
        self.closure['branch'] = special
        self.closure['artifacts'] = [{'name': special, 'location': str(self.worktree / '[spec] |.md')}]
        self.create()
        self.complete_through(6)
        self.progress('complete', '--stage', '3', '--steps', json.dumps([{'step': 7, 'at': START, 'note': special}]), success=False)
        self.progress('complete', '--stage', '3', '--steps', json.dumps([{'step': 7, 'at': END, 'note': special}]))
        result = self.finished_report(artifacts=[{'name': special, 'location': str(self.project / '[spec] |.md')}],
                                      summary=special, delivery_method=special)
        escaped = report.progress.escape(special)
        self.assertTrue(result.stdout.startswith('**本次任务**\n\n' + escaped + '\n'))
        self.assertIn('**资源处理**\n\n- ' + escaped, result.stdout)
        self.assertIn('**交付方式**：' + escaped, result.stdout)
        self.assertIn('%5Bspec%5D%20%7C.md', result.stdout)
        self.assertEqual(self.show()['stages'][0]['closure'], self.closure)


if __name__ == '__main__':
    unittest.main()
