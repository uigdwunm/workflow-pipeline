# coding: utf-8
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPTS = [REPOSITORY / 'skills' / name / 'scripts/task-progress.py'
           for name in ('design-discussion', 'solution-design', 'guided-implementation')]
SPEC = importlib.util.spec_from_file_location('discussion_progress', SCRIPTS[0])
progress = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(progress)
START = '2020-01-01T10:00:00+08:00'
PREPARED = '2020-01-01T10:01:00+08:00'
CONFIRMED = '2020-01-01T10:02:00+08:00'
POINTS = {
    'goal': '整理首页 JS 功能目录，并合理拆小大文件',
    'decisions': '按完整状态责任拆分；保持现有业务行为',
    'scope': '只讨论 home JS，模板和样式排除；等用户说动手再修改',
    'open_questions': '首页外直接消费者是否纳入必要调整？\n如何归属跨功能协调？',
    'inferences': '执行展示可能适合独立管理，尚待讨论',
}


class DiscussionConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.file = self.root / 'progress.md'
        result = subprocess.run([
            sys.executable, '-B', str(SCRIPTS[0]), 'create', '--output', str(self.file),
            '--project', str(self.project), '--summary', '首页结构查漏', '--stage', '1', '--at', START,
        ], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.task_id = json.loads(result.stdout)['task_id']

    def invoke(self, command, *arguments, script=None, success=True):
        before = self.file.read_bytes()
        result = subprocess.run([
            sys.executable, '-B', str(script or SCRIPTS[0]), command,
            '--file', str(self.file), '--task-id', self.task_id, '--project', str(self.project),
            *arguments,
        ], capture_output=True, text=True, encoding='utf-8')
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')
            self.assertEqual(self.file.read_bytes(), before)
        self.assertFalse(self.file.with_name(self.file.name + '.lock').exists())
        return result

    def prepare(self, points=None, at=PREPARED, **kwargs):
        result = self.invoke('prepare-discussion', '--points', json.dumps(POINTS if points is None else points),
                             '--at', at, **kwargs)
        return json.loads(result.stdout) if result.returncode == 0 else None

    def confirm(self, identifier, reply='确认以上要点', source='当前对话中确认要点后的用户答复',
                at=CONFIRMED, **kwargs):
        return self.invoke('confirm-discussion', '--confirmation-id', identifier,
                           '--reply', reply, '--source', source, '--at', at, **kwargs)

    def show(self, **kwargs):
        return json.loads(self.invoke('show', **kwargs).stdout)

    def complete(self, entries, **kwargs):
        return self.invoke('complete', '--stage', '1', '--steps', json.dumps(entries), **kwargs)

    def finish(self, at=CONFIRMED):
        result = subprocess.run([
            sys.executable, '-B', str(SCRIPTS[0].with_name('render-result.py')),
            '--file', str(self.file), '--task-id', self.task_id, '--project', str(self.project),
            '--summary', '讨论结论已确认', '--artifacts', '[]', '--recommended-stage', '2',
            '--recommendation', '需要制定方案', '--continuous-mode', 'false',
            '--next-skill-available', 'unknown', '--at', at,
        ], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_preparation_preserves_points_and_requires_confirmation_before_entry(self):
        self.invoke('check-discussion', success=False)
        prepared = self.prepare()
        self.assertEqual(prepared['next_step']['id'], '1.1')
        for value in POINTS.values():
            for line in value.splitlines():
                self.assertIn(line, prepared['confirmation_prompt'])
        self.assertNotIn(prepared['confirmation_id'], prepared['confirmation_prompt'])
        phase = self.show()['stages'][0]
        self.assertEqual(phase['steps'], [])
        self.assertEqual(phase['discussions'][0]['points'], POINTS)
        self.assertIsNone(phase['discussions'][0]['confirmation'])
        self.invoke('check-discussion', success=False)
        self.assertEqual([path.name for path in self.root.iterdir() if path.is_file()], ['progress.md'])

    def test_plain_completion_and_batch_cannot_register_confirmation(self):
        for prepared in (False, True):
            if prepared:
                self.prepare()
            for entries in ([{'step': 1, 'note': '用户已确认'}],
                            [{'step': 1, 'note': '用户已确认'}, {'step': 2, 'note': '已拷问'}],
                            [{'step': 2, 'note': '已拷问'}]):
                with self.subTest(prepared=prepared, entries=entries):
                    self.complete(entries, success=False)

    def test_explicit_confirmation_unlocks_entry_and_keeps_reply_evidence(self):
        prepared = self.prepare()
        reply = '确认。\n执行展示的拆分方式仍留到拷问讨论。'
        confirmed = json.loads(self.confirm(prepared['confirmation_id'], reply=reply).stdout)
        self.assertEqual(confirmed['last_completed_step'], '1.1')
        self.assertEqual(confirmed['next_step']['id'], '1.2')
        before = self.file.read_bytes()
        result = json.loads(self.invoke('check-discussion').stdout)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(result['points'], POINTS)
        self.assertEqual(result['confirmation_id'], prepared['confirmation_id'])
        self.assertEqual(result['confirmation']['reply'], reply)
        self.assertEqual(result['confirmation']['confirmed_at'], CONFIRMED)
        self.complete([{'step': 2, 'note': '拷问已完成', 'at': CONFIRMED}])
        self.finish()
        self.invoke('enter', '--stage', '2', '--at', CONFIRMED, script=SCRIPTS[1])
        self.assertEqual(self.show(script=SCRIPTS[2])['stages'][0]['discussions'][0]['confirmation']['reply'], reply)

    def test_retries_preserve_identifier_and_original_confirmation_time(self):
        prepared = self.prepare()
        before = self.file.read_bytes()
        retry = self.prepare(at=CONFIRMED)
        self.assertEqual(retry['confirmation_id'], prepared['confirmation_id'])
        self.assertEqual(self.file.read_bytes(), before)
        self.confirm(prepared['confirmation_id'])
        before = self.file.read_bytes()
        self.confirm(prepared['confirmation_id'], at='2020-01-01T10:03:00+08:00')
        self.prepare(at='2020-01-01T10:03:00+08:00')
        self.assertEqual(self.file.read_bytes(), before)
        self.confirm(prepared['confirmation_id'], reply='不同的答复', success=False)

    def test_revised_points_reject_old_identifier_and_revoke_previous_entry(self):
        first = self.prepare()
        self.confirm(first['confirmation_id'])
        revised = dict(POINTS, scope='纳入必要的首页外共享代码调整，仍只讨论')
        second = self.prepare(revised, at='2020-01-01T10:03:00+08:00')
        self.assertNotEqual(first['confirmation_id'], second['confirmation_id'])
        self.assertEqual(second['next_step']['id'], '1.1')
        self.confirm(first['confirmation_id'], at='2020-01-01T10:04:00+08:00', success=False)
        self.invoke('check-discussion', success=False)
        self.complete([{'step': 2, 'note': '已拷问'}], success=False)
        self.confirm(second['confirmation_id'], at='2020-01-01T10:04:00+08:00')
        checked = json.loads(self.invoke('check-discussion').stdout)
        self.assertEqual(checked['points'], revised)
        self.assertEqual(len(self.show()['stages'][0]['discussions']), 2)

    def test_invalid_inputs_and_backdated_confirmation_leave_original_file(self):
        for points in ({}, [], dict(POINTS, goal=''), dict(POINTS, scope=None),
                       dict(POINTS, extra='额外项'), dict(POINTS, goal='控制\x1b字符')):
            self.invoke('prepare-discussion', '--points', json.dumps(points), success=False)
        self.invoke('prepare-discussion', '--points', '{invalid', success=False)
        prepared = self.prepare()
        for kwargs in ({'reply': ''}, {'source': ''}, {'source': '跨\n行'},
                       {'reply': '控制\x1b字符'}, {'at': START}, {'at': '2999-01-01T00:00:00Z'}):
            self.confirm(prepared['confirmation_id'], success=False, **kwargs)
        self.confirm('not-a-confirmation-id', success=False)
        self.prepare(dict(POINTS, goal='改过的目标'), at=START, success=False)

    def test_active_round_is_required_and_old_round_cannot_be_reused(self):
        first = self.prepare()
        self.confirm(first['confirmation_id'])
        self.complete([{'step': 2, 'note': '已拷问', 'at': CONFIRMED}])
        self.finish()
        self.invoke('check-discussion', success=False)
        self.prepare(success=False)
        self.invoke('enter', '--stage', '1', '--new-run', '--at', '2020-01-01T10:03:00+08:00')
        self.invoke('check-discussion', success=False)
        second = self.prepare(at='2020-01-01T10:04:00+08:00')
        self.confirm(first['confirmation_id'], at='2020-01-01T10:05:00+08:00', success=False)
        self.confirm(second['confirmation_id'], at='2020-01-01T10:05:00+08:00')
        self.assertEqual(self.show()['stages'][0]['discussions'][0]['id'], first['confirmation_id'])

    def test_legacy_progress_is_readable_but_a_note_does_not_unlock_entry(self):
        data = progress.read_document(self.file)
        data['stages'][0]['steps'] = [{
            'id': '1.1', 'name': '确认讨论要点', 'note': '旧版本声称已确认', 'finished_at': PREPARED,
        }]
        self.file.write_text(progress.render(data), encoding='utf-8')
        self.show()
        self.invoke('check-discussion', success=False)
        self.complete([{'step': 2, 'note': '已拷问'}], success=False)
        prepared = self.prepare()
        self.confirm(prepared['confirmation_id'])
        self.invoke('check-discussion')
        self.assertEqual(self.show()['stages'][0]['steps'][0], data['stages'][0]['steps'][0])

    def test_confirmation_cannot_change_after_interview_completion_or_in_other_stage(self):
        prepared = self.prepare()
        self.confirm(prepared['confirmation_id'])
        self.complete([{'step': 2, 'note': '已拷问', 'at': CONFIRMED}])
        self.prepare(dict(POINTS, goal='新的目标'), at='2020-01-01T10:03:00+08:00', success=False)
        self.finish()
        self.invoke('enter', '--stage', '2', '--at', CONFIRMED)
        self.invoke('check-discussion', success=False)
        self.prepare(success=False)
        self.confirm(prepared['confirmation_id'], success=False)

    def test_invalid_in_memory_update_is_atomic_and_copies_read_same_record(self):
        first = self.prepare()
        self.confirm(first['confirmation_id'])
        for script in SCRIPTS:
            checked = json.loads(self.invoke('check-discussion', script=script).stdout)
            self.assertEqual(checked['points'], POINTS)
        data = progress.read_document(self.file)
        before = deepcopy(data)
        with self.assertRaises(progress.ProgressError):
            progress.complete_steps(data, '1', [{'step': 2, 'note': '已拷问', 'at': START}])
        self.assertEqual(data, before)

    def test_multiline_markup_roundtrips_without_changing_progress_structure(self):
        points = dict(POINTS, goal='分析 `input.js` | 执行模块\n```json\n{"goal": "示例"}\n```')
        prepared = self.prepare(points)
        reply = '确认范围。\n```\n保留尚未确认的推断。'
        self.confirm(prepared['confirmation_id'], reply=reply, source='当前对话 [答复] | 后续消息')
        checked = json.loads(self.invoke('check-discussion').stdout)
        self.assertEqual(checked['points'], points)
        self.assertEqual(checked['confirmation']['reply'], reply)
        for script in SCRIPTS:
            phase = self.show(script=script)['stages'][0]
            self.assertEqual(len(phase['steps']), 1)
            self.assertEqual(phase['discussions'][0]['points'], points)

    def test_corrupt_confirmation_records_are_rejected_without_overwriting_file(self):
        prepared = self.prepare()
        self.confirm(prepared['confirmation_id'])
        original = self.file.read_text(encoding='utf-8')
        records = self.show()['stages'][0]['discussions']
        encoded = json.dumps(records, ensure_ascii=False, indent=2)
        invalid = [[], [dict(records[0], id='bad-id')], records + records,
                   [dict(records[0], points={})],
                   [dict(records[0], confirmation={'reply': '确认'})],
                   [dict(records[0], confirmation=dict(records[0]['confirmation'], confirmed_at=START))]]
        for value in invalid:
            with self.subTest(value=value):
                self.file.write_text(original.replace(encoded, json.dumps(value, ensure_ascii=False, indent=2)),
                                     encoding='utf-8')
                self.invoke('show', success=False)
        self.file.write_text(original, encoding='utf-8')
        self.invoke('check-discussion')


if __name__ == '__main__':
    unittest.main()
