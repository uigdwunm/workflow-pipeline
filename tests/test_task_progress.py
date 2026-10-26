# coding: utf-8
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[1]
SKILLS = ('design-discussion', 'solution-design', 'guided-implementation')
SCRIPTS = [REPOSITORY / 'skills' / skill / 'scripts/task-progress.py' for skill in SKILLS]
SPEC = importlib.util.spec_from_file_location('task_progress', SCRIPTS[0])
progress = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(progress)
START = '2020-01-01T10:00:00+08:00'


class TaskProgressTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.file = self.root / 'progress.md'
        self.summary = '为配置加载增加必填项和格式校验'
        created = self.create()
        self.task_id = created['task_id']

    def run_cli(self, command, *arguments, script=None, success=True, task_id=None, project=None):
        values = [sys.executable, '-B', str(script or SCRIPTS[0]), command]
        if command != 'create':
            values += ['--file', str(self.file), '--task-id', task_id or self.task_id,
                       '--project', str(project or self.project)]
        result = subprocess.run(values + list(arguments), capture_output=True, text=True, encoding='utf-8')
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')
        return result

    def create(self, stage='2', at=START, **kwargs):
        result = self.run_cli('create', '--output', str(self.file), '--project', str(self.project),
                              '--summary', self.summary, '--stage', stage, '--at', at, **kwargs)
        return json.loads(result.stdout)

    def complete(self, stage, number, at, note=None, **kwargs):
        return self.run_cli('complete', '--stage', stage, '--steps', json.dumps([{'step': number, 'at': at, 'note': note or '已完成步骤 ' + str(number)}]), **kwargs)

    def show(self, **kwargs):
        return json.loads(self.run_cli('show', **kwargs).stdout)

    def test_batch_records_individual_times_and_closes_on_final_step(self):
        entries = [{'step': number, 'note': f'完成 {number}',
                    'at': f'2020-01-01T10:0{number}:00+08:00'} for number in range(1, 5)]
        result = json.loads(self.run_cli('complete', '--stage', '2', '--steps', json.dumps(entries)).stdout)
        phase = self.show()['stages'][0]
        self.assertEqual([item['note'] for item in phase['steps']], [item['note'] for item in entries])
        self.assertEqual([item['finished_at'] for item in phase['steps']], [item['at'] for item in entries])
        self.assertEqual(result['status'], '已完成')
        self.assertEqual(result['finished_at'], entries[-1]['at'])
        before = self.file.read_bytes()
        for entry in entries:
            entry['at'] = '2020-01-01T10:10:00+08:00'
        self.run_cli('complete', '--stage', '2', '--steps', json.dumps(entries))
        self.assertEqual(self.file.read_bytes(), before)

    def test_batch_default_time_is_shared_and_existing_records_can_be_retried(self):
        entries = [{'step': 1, 'note': '准备完成'}, {'step': 2, 'note': '实现完成'}]
        self.run_cli('complete', '--stage', '2', '--steps', json.dumps(entries))
        first = self.show()['stages'][0]['steps']
        self.assertEqual(first[0]['finished_at'], first[1]['finished_at'])
        before = self.file.read_bytes()
        self.run_cli('complete', '--stage', '2', '--steps', json.dumps(entries))
        self.assertEqual(self.file.read_bytes(), before)
        entries.append({'step': 3, 'note': '验收完成'})
        self.run_cli('complete', '--stage', '2', '--steps', json.dumps(entries))
        self.assertEqual(self.show()['stages'][0]['steps'][:2], first)

    def test_invalid_batch_preserves_all_records(self):
        first = {'step': 1, 'note': '完成', 'at': '2020-01-01T10:02:00+08:00'}
        invalid = [
            [], {}, None, [None], [1], [{'step': 1}],
            [{'step': True, 'note': '完成'}], [{'step': 1.0, 'note': '完成'}],
            [{'step': '1', 'note': '完成'}], [{'step': 1, 'note': 1}],
            [dict(first, extra='未知字段')], [dict(first, at=None)],
            [first, first], [first, {'step': 3, 'note': '跳步'}],
            [{'step': 2, 'note': '乱序'}, first],
            [first, {'step': 2, 'note': ''}],
            [first, {'step': 2, 'note': '完成', 'at': '2020-01-01T10:01:00+08:00'}],
            [first, {'step': 2, 'note': '完成', 'at': '2999-01-01T00:00:00Z'}],
        ]
        for entries in invalid:
            with self.subTest(entries=entries):
                self.assert_rejected_without_change('complete', '--stage', '2', '--steps', json.dumps(entries))
        self.assert_rejected_without_change('complete', '--stage', '2', '--steps', '[invalid')
        self.run_cli('complete', '--stage', '2', '--steps', json.dumps([first]))
        self.assert_rejected_without_change('complete', '--stage', '2', '--steps', json.dumps([
            dict(first, note='不同结果'), {'step': 2, 'note': '完成'}]))

    def test_failed_batch_does_not_mutate_in_memory_state(self):
        data = progress.read_document(self.file)
        original = deepcopy(data)
        with self.assertRaises(progress.ProgressError):
            progress.complete_steps(data, '2', [{'step': 1, 'note': '完成'}, {'step': 3, 'note': '跳步'}])
        self.assertEqual(data, original)

    def test_complete_rejects_retired_single_step_options(self):
        self.assert_rejected_without_change('complete', '--stage', '2', '--step', '1', '--note', '完成')
        entries = json.dumps([{'step': 1, 'note': '完成'}])
        self.assert_rejected_without_change('complete', '--stage', '2', '--step', entries)
        self.assert_rejected_without_change('complete', '--stage', '2', '--steps', entries, '--note', '完成')
        self.assert_rejected_without_change('complete', '--stage', '2', '--steps', entries, '--at', START)

    def finish_all(self, stage='2', script=None, minute=1):
        results = []
        for number in range(1, len(progress.STEPS[stage]) + 1):
            result = self.complete(stage, number, f'2020-01-01T10:{minute:02d}:00+08:00', script=script)
            results.append(json.loads(result.stdout))
        return results

    def assert_rejected_without_change(self, command, *arguments, **kwargs):
        before = self.file.read_bytes()
        self.run_cli(command, *arguments, success=False, **kwargs)
        self.assertEqual(self.file.read_bytes(), before)

    def test_create_records_stage_start_in_one_file_without_pending_rows(self):
        data = self.show()
        self.assertEqual(len(data['stages']), 1)
        self.assertEqual(data['stages'][0]['started_at'], START)
        self.assertEqual(data['stages'][0]['steps'], [])
        self.assertIn('2.1', data['current_step'])
        self.assertEqual([path.name for path in self.root.iterdir() if path.is_file()], ['progress.md'])
        document = self.file.read_text(encoding='utf-8')
        self.assertIn(self.summary, document)
        self.assertNotIn('待执行', document)
        self.assertNotIn('执行说明', document)

    def test_create_reuses_identity_without_resetting_start(self):
        before = self.file.read_bytes()
        created = self.create(at='2020-01-01T10:10:00+08:00')
        self.assertEqual(created['task_id'], self.task_id)
        self.assertEqual(created['started_at'], START)
        self.assertEqual(self.file.read_bytes(), before)

    def test_create_rejects_other_task_or_stage_and_preserves_file(self):
        for summary, stage in [('另一任务', '2'), (self.summary, '1')]:
            self.assert_rejected_without_change(
                'create', '--output', str(self.file), '--project', str(self.project),
                '--summary', summary, '--stage', stage)

    def test_each_stage_can_be_first_and_unused_stages_are_absent(self):
        for index, stage in enumerate(('1', '2', '3')):
            with self.subTest(stage=stage):
                self.file = self.root / (stage + '.md')
                self.task_id = self.create(stage, script=SCRIPTS[index])['task_id']
                self.finish_all(stage, script=SCRIPTS[index])
                data = self.show(script=SCRIPTS[index])
                self.assertEqual([item['stage'] for item in data['stage_durations']], [stage])
                self.assertEqual(len(data['stages'][0]['steps']), len(progress.STEPS[stage]))
                self.assertEqual(data['total_duration_seconds'], 60)

    def test_removed_stage_is_rejected_without_creating_or_changing_progress(self):
        for index, script in enumerate(SCRIPTS):
            with self.subTest(skill=SKILLS[index]):
                output = self.root / f'rejected-stage-{index}.md'
                self.run_cli('create', '--output', str(output), '--project', str(self.project),
                             '--summary', self.summary, '--stage', '4', script=script, success=False)
                self.assertFalse(output.exists())
                self.assertFalse(output.with_name(output.name + '.lock').exists())
                self.assert_rejected_without_change('enter', '--stage', '4', script=script)

    def test_implementation_ends_only_after_eighth_step(self):
        self.file = self.root / 'implementation-progress.md'
        self.task_id = self.create('3', script=SCRIPTS[2])['task_id']
        for number in range(1, 9):
            at = f'2020-01-01T10:{number:02d}:00+08:00'
            result = json.loads(self.complete('3', number, at, script=SCRIPTS[2]).stdout)
            if number < 8:
                self.assertEqual(result['status'], '进行中')
                self.assertIsNone(result['finished_at'])
                self.assertEqual(result['next_step']['id'], f'3.{number + 1}')
            else:
                self.assertEqual(result['status'], '已完成')
                self.assertEqual(result['finished_at'], at)
                self.assertIsNone(result['next_step'])
            if number in (4, 7):
                self.assert_rejected_without_change('cleanup', '--task-finished', script=SCRIPTS[2])
        data = self.show(script=SCRIPTS[0])
        self.assertEqual(len(data['stages'][0]['steps']), 8)
        self.assertEqual(data['total_duration_seconds'], 480)

    def test_one_completion_adds_one_row_and_advances_next_step(self):
        result = json.loads(self.complete('2', 1, '2020-01-01T10:02:00+08:00', '工作区已核对').stdout)
        data = self.show()
        item = data['stages'][0]['steps'][0]
        self.assertEqual(set(item), {'id', 'name', 'note', 'finished_at'})
        self.assertEqual(item['note'], '工作区已核对')
        self.assertEqual(item['finished_at'], '2020-01-01T10:02:00+08:00')
        self.assertEqual(result['next_step'], {'id': '2.2', 'name': '生成方案'})
        self.assertIsNone(data['stages'][0]['finished_at'])

    def test_final_step_automatically_ends_stage_with_same_event_time(self):
        results = self.finish_all()
        last = results[-1]
        phase = self.show()['stages'][0]
        self.assertEqual(last['status'], '已完成')
        self.assertIsNone(last['next_step'])
        self.assertEqual(phase['finished_at'], phase['steps'][-1]['finished_at'])
        self.assertEqual(self.show()['stage_durations'][0]['duration_seconds'], 60)

    def test_updates_are_compact_and_only_show_returns_history(self):
        for result in self.finish_all():
            self.assertNotIn('stages', result)
            self.assertNotIn('stage_durations', result)
            self.assertNotIn('records', result)
            self.assertEqual(result['task_id'], self.task_id)
        self.assertEqual(len(self.show()['stages'][0]['steps']), 4)

    def test_retries_and_reentry_preserve_first_times_and_results(self):
        self.complete('2', 1, '2020-01-01T10:02:00+08:00', '工作区已核对')
        before = self.file.read_bytes()
        self.complete('2', 1, '2020-01-01T10:10:00+08:00', '工作区已核对')
        self.run_cli('enter', '--stage', '2', '--at', '2020-01-01T10:12:00+08:00')
        self.assertEqual(self.file.read_bytes(), before)
        self.assert_rejected_without_change('complete', '--stage', '2', '--steps', json.dumps([{'step': 1, 'note': '不同结果'}]))
        for number in range(2, 5):
            self.complete('2', number, '2020-01-01T10:15:00+08:00')
        before = self.file.read_bytes()
        self.complete('2', 4, '2020-01-01T10:20:00+08:00')
        self.assertEqual(self.file.read_bytes(), before)

    def test_invalid_steps_or_results_do_not_change_file(self):
        for number in (0, -1, 2, 5, '2.1.1'):
            self.assert_rejected_without_change('complete', '--stage', '2', '--steps', json.dumps([{'step': number, 'note': '完成'}]))
        for note in ('', '  ', '结果\n换行', '结果\x1b控制符'):
            self.assert_rejected_without_change('complete', '--stage', '2', '--steps', json.dumps([{'step': 1, 'note': note}]))

    def test_unicode_line_separators_are_rejected_before_creating_progress(self):
        before = self.file.read_bytes()
        for index, script in enumerate(SCRIPTS, 1):
            for codepoint in (0x85, 0x2028, 0x2029):
                separator = chr(codepoint)
                values = (separator + '目标', '目标' + separator + '续行', '目标' + separator)
                for position, summary in enumerate(values):
                    with self.subTest(skill=index, codepoint=hex(codepoint), position=position):
                        output = self.root / f'rejected-{index}-{codepoint}-{position}.md'
                        result = self.run_cli(
                            'create', '--output', str(output), '--project', str(self.project),
                            '--summary', summary, '--stage', str(index), script=script, success=False)
                        self.assertIn('单行文本', result.stderr)
                        self.assertFalse(output.exists())
                        self.assertFalse(output.with_name(output.name + '.lock').exists())
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(self.show()['stages'][0]['steps'], [])

    def test_unicode_line_separators_do_not_corrupt_existing_progress(self):
        before = self.file.read_bytes()
        for index, script in enumerate(SCRIPTS, 1):
            for codepoint in (0x85, 0x2028, 0x2029):
                separator = chr(codepoint)
                values = (separator + '已核对', '已核对' + separator + '续行', '已核对' + separator)
                for position, note in enumerate(values):
                    with self.subTest(skill=index, codepoint=hex(codepoint), position=position):
                        result = self.run_cli('complete', '--stage', '2', '--steps', json.dumps([{'step': 1, 'note': note}]), script=script, success=False)
                        self.assertIn('单行文本', result.stderr)
                        self.assertEqual(self.file.read_bytes(), before)
                        self.assertFalse(self.file.with_name(self.file.name + '.lock').exists())
        self.complete('2', 1, '2020-01-01T10:01:00+08:00', '工作区核对通过')
        self.assertEqual(self.show()['stages'][0]['steps'][0]['note'], '工作区核对通过')

    def test_invalid_times_and_backdated_completion_do_not_change_file(self):
        for at in ('2020-01-01T09:59:00+08:00', '2020-01-01T10:01:00', '2999-01-01T00:00:00Z'):
            self.assert_rejected_without_change('complete', '--stage', '2', '--steps', json.dumps([{'step': 1, 'note': '完成', 'at': at}]))
        self.complete('2', 1, '2020-01-01T10:02:00+08:00')
        self.assert_rejected_without_change('complete', '--stage', '2', '--steps', json.dumps([{'step': 2, 'note': '完成', 'at': '2020-01-01T10:01:00+08:00'}]))

    def test_default_time_is_persisted_without_model_timestamp(self):
        result = json.loads(self.run_cli('complete', '--stage', '2', '--steps', json.dumps([{'step': 1, 'note': '工作区已核对'}])).stdout)
        item = self.show()['stages'][0]['steps'][0]
        self.assertIsNotNone(progress.timestamp(item['finished_at']).utcoffset())
        self.assertEqual(result['last_completed_step'], item['id'])

    def test_handoff_keeps_same_file_and_excludes_between_stage_wait(self):
        self.finish_all(minute=5)
        self.run_cli('enter', '--stage', '3', '--at', '2020-01-01T02:20:00Z', script=SCRIPTS[2])
        for number in range(1, 9):
            self.complete('3', number, '2020-01-01T02:24:00+00:00', script=SCRIPTS[2])
        data = self.show(script=SCRIPTS[1])
        self.assertEqual(data['task_id'], self.task_id)
        self.assertEqual([item['duration_seconds'] for item in data['stage_durations']], [300, 240])
        self.assertEqual(data['total_duration_seconds'], 540)
        self.assertEqual(len(list(self.root.glob('*.md'))), 1)

    def test_unfinished_or_overlapping_stage_cannot_handoff(self):
        self.assert_rejected_without_change('enter', '--stage', '3')
        self.assert_rejected_without_change('enter', '--stage', '2', '--new-run')
        self.finish_all(minute=5)
        self.assert_rejected_without_change('enter', '--stage', '3', '--at', '2020-01-01T10:04:00+08:00')

    def test_new_run_preserves_history_and_aggregates_stage_duration(self):
        self.finish_all(minute=5)
        old = deepcopy(self.show()['stages'][0])
        self.run_cli('enter', '--stage', '2', '--new-run', '--at', '2020-01-01T10:10:00+08:00')
        data = self.show()
        self.assertIsNone(data['total_duration_seconds'])
        self.assertEqual(data['known_duration_seconds'], 300)
        self.finish_all(minute=12)
        data = self.show()
        self.assertEqual(data['stages'][0], old)
        self.assertEqual([phase['run'] for phase in data['stages']], [1, 2])
        self.assertEqual(data['total_duration_seconds'], 420)

    def test_resume_uses_completed_rows_without_creating_wait_or_retry_steps(self):
        self.complete('2', 1, '2020-01-01T10:01:00+08:00', 'Worktree 已核对')
        before = self.file.read_bytes()
        # 暂停和恢复只读取；生成中的确认和返工留在原业务流程。
        self.show()
        self.run_cli('enter', '--stage', '2')
        self.assertEqual(self.file.read_bytes(), before)
        self.complete('2', 2, '2020-01-01T10:10:00+08:00', 'Spec 已修订并发布；本任务无需 Tickets')
        data = self.show()
        self.assertEqual([item['id'] for item in data['stages'][0]['steps']], ['2.1', '2.2'])
        self.assertEqual(data['stages'][0]['started_at'], START)
        self.assertIn('2.3', data['current_step'])

    def test_stop_preserves_completed_rows_and_allows_new_stage(self):
        self.complete('2', 1, '2020-01-01T10:01:00+08:00')
        old = deepcopy(self.show()['stages'][0]['steps'])
        self.run_cli('stop', '--stage', '2', '--note', '用户改为直接实现小改动', '--at', '2020-01-01T10:02:00+08:00')
        self.run_cli('enter', '--stage', '3', '--at', '2020-01-01T10:03:00+08:00')
        data = self.show()
        self.assertEqual(data['stages'][0]['status'], '已终止')
        self.assertEqual(data['stages'][0]['steps'], old)
        self.assertEqual(data['stages'][0]['note'], '用户改为直接实现小改动')
        self.assertEqual(data['stages'][1]['steps'], [])

    def test_implementation_can_redirect_to_design_and_return_using_same_file(self):
        self.file = self.root / 'implementation-progress.md'
        self.task_id = self.create('3', script=SCRIPTS[2])['task_id']
        self.run_cli('stop', '--stage', '3', '--note', '缺少 Spec 且超出小改动范围',
                     '--at', '2020-01-01T10:02:00+08:00', script=SCRIPTS[2])
        self.run_cli('enter', '--stage', '2', '--at', '2020-01-01T10:03:00+08:00', script=SCRIPTS[1])
        self.finish_all('2', script=SCRIPTS[1], minute=5)
        self.run_cli('enter', '--stage', '3', '--new-run', '--at', '2020-01-01T10:06:00+08:00', script=SCRIPTS[2])
        self.finish_all('3', script=SCRIPTS[2], minute=8)
        data = self.show(script=SCRIPTS[2])
        self.assertEqual(data['file'], str(self.file))
        self.assertEqual(data['task_id'], self.task_id)
        self.assertEqual([(phase['stage'], phase['run']) for phase in data['stages']], [('3', 1), ('2', 1), ('3', 2)])
        self.assertEqual(data['stages'][0]['status'], '已终止')
        self.assertEqual(data['stages'][0]['steps'], [])
        self.assertEqual([item['duration_seconds'] for item in data['stage_durations']], [120, 240])
        self.assertEqual(data['total_duration_seconds'], 360)

    def test_implementation_report_failure_keeps_stage_open_and_success_can_close_it(self):
        self.file = self.root / 'implementation-progress.md'
        self.task_id = self.create('3', script=SCRIPTS[2])['task_id']
        for number in range(1, 4):
            self.complete('3', number, '2020-01-01T10:05:00+08:00', script=SCRIPTS[2])
        closure = {
            'worktree': str(self.project), 'branch': 'codex/config-validation',
            'target_branch': 'main', 'implementation_commit': 'abcdef1',
            'artifacts': [{'name': '目标项目', 'location': str(self.project)}],
        }
        self.run_cli('enter', '--stage', '3', '--closure', json.dumps(closure), script=SCRIPTS[2])
        for number in range(4, 8):
            self.complete('3', number, '2020-01-01T10:06:00+08:00', script=SCRIPTS[2])
        before = self.file.read_bytes()
        saved_start = self.show(script=SCRIPTS[2])['stages'][0]['started_at']
        generated_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
        arguments = [
            sys.executable, '-B', str(REPOSITORY / 'skills/guided-implementation/scripts/render-result.py'),
            '--file', str(self.file), '--task-id', self.task_id, '--project', str(self.project),
            '--summary', '配置校验实现、验证、交付及资源清理已完成',
            '--implementation-result', '配置校验已实现',
            '--validation-result', '测试和审查通过',
            '--closure-result', '文档状态已更新，临时资源已清理',
            '--artifacts', json.dumps([{'name': '目标项目', 'location': str(self.project)}]),
            '--delivery-method', '本地合并', '--at', generated_at, '--complete', '--pending',
        ]
        failed = subprocess.run(arguments + ['仍有待核实事项'], capture_output=True, text=True, encoding='utf-8')
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(failed.stdout, '')
        self.assertEqual(self.file.read_bytes(), before)
        self.assertIsNone(self.show(script=SCRIPTS[2])['stages'][0]['finished_at'])
        report = subprocess.run(arguments + ['无'], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertIn('**3实现已完成**', report.stdout)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertIsNone(self.show(script=SCRIPTS[2])['stages'][0]['finished_at'])
        receipt = json.loads(self.complete('3', 8, generated_at, '最终报告已生成并保留', script=SCRIPTS[2]).stdout)
        self.assertEqual(receipt['status'], '已完成')
        phase = self.show(script=SCRIPTS[2])['stages'][0]
        self.assertEqual(phase['started_at'], saved_start)
        self.assertEqual(phase['finished_at'], generated_at)
        self.assertEqual(phase['steps'][-1]['finished_at'], generated_at)
        self.complete('3', 8, datetime.now(timezone.utc).isoformat(timespec='seconds'),
                      '最终报告已生成并保留', script=SCRIPTS[2])
        for script in SCRIPTS:
            self.assertEqual(self.show(script=script)['stages'][0], phase)

    def test_stop_requires_reason_and_does_not_rewrite_closed_stage(self):
        self.assert_rejected_without_change('stop', '--stage', '2', '--note', '')
        self.assert_rejected_without_change('stop', '--stage', '2', '--note', '取消', '--at', '2019-01-01T00:00:00Z')
        self.run_cli('stop', '--stage', '2', '--note', '取消', '--at', '2020-01-01T10:02:00+08:00')
        before = self.file.read_bytes()
        self.run_cli('stop', '--stage', '2', '--note', '取消', '--at', '2020-01-01T10:10:00+08:00')
        self.assertEqual(self.file.read_bytes(), before)
        self.assert_rejected_without_change('stop', '--stage', '2', '--note', '其他原因')
        self.assert_rejected_without_change('complete', '--stage', '2', '--steps', json.dumps([{'step': 1, 'note': '完成'}]))

    def test_completed_stage_cannot_be_reclassified_as_cancelled(self):
        self.finish_all()
        self.assert_rejected_without_change('stop', '--stage', '2', '--note', '用户结束整个任务')

    def test_task_or_project_mismatch_preserves_file(self):
        other = self.root / 'other-project'
        other.mkdir()
        for kwargs in ({'task_id': 'another-task'}, {'project': other}):
            self.assert_rejected_without_change('complete', '--stage', '2', '--steps', json.dumps([{'step': 1, 'note': '完成'}]), **kwargs)

    def test_markdown_special_characters_roundtrip_as_plain_text(self):
        special = r'路径含 |、[括号]、\、`代码`、<标签> 与 # 标题'
        self.complete('2', 1, '2020-01-01T10:01:00+08:00', special)
        self.assertEqual(self.show()['stages'][0]['steps'][0]['note'], special)
        self.file = self.root / 'special.md'
        self.project = self.root / '项目 | [A] (B)'
        self.project.mkdir()
        self.summary = special
        self.task_id = self.create()['task_id']
        data = self.show()
        self.assertEqual(data['summary'], special)
        self.assertEqual(data['project'], str(self.project.resolve()))

    def test_corrupt_document_is_never_overwritten_or_removed(self):
        original = self.file.read_text(encoding='utf-8')
        for corrupted in (original + '额外内容\n', original.replace('当前大步骤', '当前步骤'),
                          original.replace('进行中', '已完成'),
                          original.replace(progress.escape(self.task_id), 'invalid-id')):
            with self.subTest(corrupted=corrupted):
                self.file.write_text(corrupted, encoding='utf-8')
                self.assert_rejected_without_change('complete', '--stage', '2', '--steps', json.dumps([{'step': 1, 'note': '完成'}]))
                self.assert_rejected_without_change('cleanup', '--task-finished')
        self.file.write_text(original, encoding='utf-8')

    def test_missing_and_symlink_files_are_rejected_without_recreation(self):
        original = self.file.read_bytes()
        self.file.unlink()
        self.run_cli('show', success=False)
        self.assertFalse(self.file.exists())
        destination = self.root / 'external.md'
        destination.write_bytes(original)
        self.file.symlink_to(destination)
        self.run_cli('complete', '--stage', '2', '--steps', json.dumps([{'step': 1, 'note': '完成'}]), success=False)
        self.run_cli('cleanup', '--task-finished', success=False)
        self.assertEqual(destination.read_bytes(), original)
        self.assertTrue(self.file.is_symlink())

    def test_atomic_write_failure_and_active_lock_preserve_file(self):
        before = self.file.read_bytes()
        lock = self.file.with_name(self.file.name + '.lock')
        lock.mkdir()
        self.run_cli('complete', '--stage', '2', '--steps', json.dumps([{'step': 1, 'note': '完成'}]), success=False)
        self.assertEqual(self.file.read_bytes(), before)
        lock.rmdir()
        data = progress.read_document(self.file)
        progress.complete_steps(data, '2', [{'step': 1, 'note': '完成', 'at': '2020-01-01T10:01:00+08:00'}])
        with mock.patch.object(progress.os, 'replace', side_effect=OSError('写入失败')):
            with self.assertRaises(OSError):
                progress.write_document(self.file, data)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(list(self.root.glob('.task-progress-*')), [])

    def test_cleanup_requires_task_end_and_keeps_artifacts(self):
        self.assert_rejected_without_change('cleanup', '--task-finished')
        self.finish_all()
        self.assert_rejected_without_change('cleanup')
        artifact = self.project / 'spec.md'
        artifact.write_text('方案成果', encoding='utf-8')
        result = json.loads(self.run_cli('cleanup', '--task-finished').stdout)
        self.assertTrue(result['removed'])
        self.assertFalse(self.file.exists())
        self.assertEqual(artifact.read_text(encoding='utf-8'), '方案成果')
        self.assertFalse(self.file.with_name(self.file.name + '.lock').exists())

    def test_progress_survives_execution_worktree_removal(self):
        worktree = self.project / 'worktree'
        worktree.mkdir()
        self.complete('2', 1, '2020-01-01T10:01:00+08:00', '工作区：' + str(worktree))
        shutil.rmtree(worktree)
        data = self.show()
        self.assertEqual(data['project'], str(self.project.resolve()))
        self.assertEqual(len(data['stages'][0]['steps']), 1)

    def test_independent_skill_copies_match_and_work_without_repository(self):
        self.assertTrue(all(script.read_bytes() == SCRIPTS[0].read_bytes() for script in SCRIPTS))
        isolated = self.root / 'installed-script.py'
        shutil.copyfile(SCRIPTS[1], isolated)
        self.finish_all(script=isolated)
        self.assertEqual(self.show(script=isolated)['stages'][0]['status'], '已完成')

    def test_child_prompt_carries_identity_without_changing_progress(self):
        before = self.file.read_bytes()
        worktree = self.project / 'worktree'
        worktree.mkdir()
        notes = (f'进度文件：{self.file}\n任务标识：{self.task_id}\n原目标项目：{self.project}\n'
                 '进度由主对话维护；子 Agent 回传结果，不创建或更新进度文件。')
        result = subprocess.run([
            sys.executable, '-B', str(REPOSITORY / 'skills/solution-design/scripts/render-subagent-prompt.py'),
            '--worktree', str(worktree), '--goal', self.summary, '--context', '已确认范围：配置加载',
            '--sources', '[]', '--notes', notes,
        ], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(notes, result.stdout)
        self.assertEqual(self.file.read_bytes(), before)

    def test_new_conversation_prompt_carries_identity_without_changing_progress(self):
        self.file = self.root / 'discussion-progress.md'
        self.task_id = self.create('1')['task_id']
        before = self.file.read_bytes()
        notes = (f'进度文件：{self.file}\n任务标识：{self.task_id}\n原目标项目：{self.project}\n'
                 '进度由原对话维护；新对话回传结果，不创建或更新进度文件。')
        result = subprocess.run([
            sys.executable, '-B', str(REPOSITORY / 'skills/design-discussion/scripts/render-new-conversation-prompt.py'),
            '--project', str(self.project), '--goal', self.summary, '--context', '已确认范围：配置加载',
            '--sources', '[]', '--discussion-documents', '[]', '--origin-thread-id', 'original-thread',
            '--return-authorization', '用户已授权将讨论结果传回原对话', '--notes', notes,
        ], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(notes, result.stdout)
        self.assertEqual(self.file.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
