# coding: utf-8
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('report_test_progress', REPOSITORY / 'skills/design-discussion/scripts/task-progress.py')
progress = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(progress)
SUMMARY = "已确认的本次成果"
PROFILES = (
    ("design-discussion", "next-skill-available", "2方案", "[]",
     ["--recommended-stage", "2", "--recommendation", "需要制定方案"]),
    ("design-discussion", "next-skill-available", "3实现", "[]",
     ["--recommended-stage", "3", "--recommendation", "已确认的小改动"]),
    ("solution-design", "implementation-skill-available", "3实现",
     json.dumps([{"name": "Spec", "location": "/project/spec.md"}]), []),
)


class ResultReportTests(unittest.TestCase):
    def run_report(self, profile, continuous, available, artifacts=None, extra=None, paused_stage=None):
        skill, available_argument, _, default_artifacts, extra_arguments = profile
        script = REPOSITORY / "skills" / skill / "scripts/render-result.py"
        arguments = [
            sys.executable, "-B", str(script),
            "--summary", SUMMARY,
            "--artifacts", default_artifacts if artifacts is None else artifacts,
            "--" + available_argument, available,
        ] + extra_arguments
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            path = project / 'progress.md'
            at = '2026-01-01T09:00:00-07:00'
            stage = '1' if skill == 'design-discussion' else '2'
            state = None if continuous is None else progress.make_continuation(continuous, '关闭连续模式', '用户模式要求')
            data = progress.create_document(path, project, SUMMARY, stage, at, continuation=state)
            if paused_stage is not None:
                progress.pause_stages(data, paused_stage, '暂停下一阶段', '用户暂停消息')
            if stage == '1':
                points = {'goal': SUMMARY, 'decisions': '无', 'scope': '本任务', 'open_questions': '无', 'inferences': '无'}
                progress.prepare_discussion(data, points, at)
                progress.confirm_discussion(data, data['stages'][0]['discussions'][0]['id'], '确认要点', '本轮用户答复', at)
                progress.complete_steps(data, '1', [{'step': 2, 'note': '拷问已完成', 'at': at}])
            else:
                progress.complete_steps(data, '2', [{'step': step, 'note': '方案已验收', 'at': at} for step in (1, 2, 3)])
            progress.write_document(path, data)
            arguments += ['--file', str(path), '--task-id', data['task_id'], '--project', str(project)]
            return subprocess.run(arguments + (extra or []), capture_output=True, text=True, encoding="utf-8")

    def assert_recommendation(self, profile, report):
        self.assertIn("**下一步**", report)
        self.assertIn(f"建议进入 **{profile[2]}**", report)
        extra_arguments = profile[4]
        if "--recommendation" in extra_arguments:
            reason = extra_arguments[extra_arguments.index("--recommendation") + 1]
            self.assertIn(reason, report)

    def test_continuous_handoff_proceeds_without_stage_questions(self):
        for profile in PROFILES:
            with self.subTest(skill=profile[0], stage=profile[2]):
                result = self.run_report(profile, None, "true")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(SUMMARY, result.stdout)
                self.assert_recommendation(profile, result.stdout)
                self.assertIn(f"下一步将进入 {profile[2]} 流程", result.stdout)
                self.assertIn('连续模式已开启', result.stdout)
                self.assertNotIn("是否", result.stdout)

    def test_manual_mode_preserves_recommendation_and_waits_for_user(self):
        for profile in PROFILES:
            for available in ("true", "false", "unknown"):
                with self.subTest(skill=profile[0], stage=profile[2], available=available):
                    result = self.run_report(profile, False, available)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(SUMMARY, result.stdout)
                    self.assert_recommendation(profile, result.stdout)
                    for artifact in json.loads(profile[3]):
                        self.assertIn(artifact["location"], result.stdout)
                    self.assertIn('等待用户指定下一步', result.stdout)
                    self.assertIn('关闭连续模式', result.stdout)
                    self.assertIn('用户模式要求', result.stdout)
                    for text in ("是否", "下一步将进入", "无法使用", "尚未核实", "交接暂缓"):
                        self.assertNotIn(text, result.stdout)

    def test_blocked_handoff_preserves_results_without_advancing_or_asking(self):
        for profile in PROFILES:
            for available in ("false", "unknown"):
                with self.subTest(skill=profile[0], stage=profile[2], available=available):
                    result = self.run_report(profile, None, available)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(SUMMARY, result.stdout)
                    self.assert_recommendation(profile, result.stdout)
                    for artifact in json.loads(profile[3]):
                        self.assertIn(artifact["location"], result.stdout)
                    self.assertNotIn("是否", result.stdout)
                    self.assertNotIn("下一步将进入", result.stdout)
                    self.assertIn('连续模式已开启', result.stdout)
                    self.assertNotIn('按用户要求在', result.stdout)
                    if available == "unknown":
                        self.assertIn("尚未核实", result.stdout)
                        self.assertNotIn("无法使用", result.stdout)
                    else:
                        self.assertIn("无法使用", result.stdout)
                        self.assertNotIn("尚未核实", result.stdout)

    def test_retired_report_mode_switch_preserves_recorded_continuation(self):
        for profile in PROFILES:
            with self.subTest(skill=profile[0], stage=profile[2]):
                result = self.run_report(profile, None, "true", extra=['--continuous-mode', 'false'])
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("--continuous-mode", result.stderr)

    def test_invalid_availability_does_not_emit_a_completion_report(self):
        for profile in PROFILES:
            with self.subTest(skill=profile[0], stage=profile[2]):
                result = self.run_report(profile, None, "invalid")
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("--" + profile[1], result.stderr)

    def test_handoff_report_empty_artifacts_are_valid_only_for_design_discussion(self):
        for profile in PROFILES:
            with self.subTest(skill=profile[0], stage=profile[2]):
                result = self.run_report(profile, None, "unknown", artifacts="[]")
                if profile[0] == "design-discussion":
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(SUMMARY, result.stdout)
                else:
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, "")

    def test_explicit_pause_preserves_mode_and_recommendation(self):
        for profile in PROFILES:
            for available in ('true', 'false', 'unknown'):
                stage = '2' if profile[2] == '2方案' else '3'
                result = self.run_report(profile, None, available, paused_stage=stage)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assert_recommendation(profile, result.stdout)
                self.assertIn('连续模式已开启', result.stdout)
                self.assertIn(f'{profile[2]} 已按用户要求暂停', result.stdout)
                self.assertIn('用户暂停消息', result.stdout)
                for wording in ('下一步将进入', '无法使用', '尚未核实'):
                    self.assertNotIn(wording, result.stdout)

    def test_plan_report_checks_task_identity_and_uses_recorded_start(self):
        profile = PROFILES[2]
        rejected = self.run_report(profile, None, 'true', extra=['--task-id', 'other-task'])
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(rejected.stdout, '')
        self.assertIn('任务标识', rejected.stderr)
        accepted = self.run_report(profile, None, 'true', extra=['--at', '2026-01-01T09:05:00-07:00'])
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertIn('2026-01-01 09:00:00-07:00', accepted.stdout)
        self.assertIn('**总耗时**：5 分钟 0 秒', accepted.stdout)
        rejected = self.run_report(profile, None, 'true', extra=['--at', '2026-01-01T08:59:00-07:00'])
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(rejected.stdout, '')


if __name__ == "__main__":
    unittest.main()
