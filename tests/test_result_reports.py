# coding: utf-8
import json
import subprocess
import sys
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
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
    def run_report(self, profile, continuous_mode, available, artifacts=None):
        skill, available_argument, _, default_artifacts, extra_arguments = profile
        script = REPOSITORY / "skills" / skill / "scripts/render-result.py"
        arguments = [
            sys.executable, "-B", str(script),
            "--summary", SUMMARY,
            "--artifacts", default_artifacts if artifacts is None else artifacts,
            "--started-at", "2026-01-01T09:00:00-07:00",
            "--" + available_argument, available,
        ] + extra_arguments
        if continuous_mode is not None:
            arguments.extend(["--continuous-mode", continuous_mode])
        return subprocess.run(arguments, capture_output=True, text=True, encoding="utf-8")

    def test_continuous_handoff_proceeds_without_stage_questions(self):
        for profile in PROFILES:
            with self.subTest(skill=profile[0], stage=profile[2]):
                result = self.run_report(profile, "true", "true")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(SUMMARY, result.stdout)
                self.assertIn(f"下一步将进入 {profile[2]} 流程", result.stdout)
                self.assertNotIn("是否", result.stdout)

    def test_explicit_stage_scope_ends_without_handoff_for_any_availability(self):
        for profile in PROFILES:
            for available in ("true", "false", "unknown"):
                with self.subTest(skill=profile[0], stage=profile[2], available=available):
                    result = self.run_report(profile, "false", available)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(SUMMARY, result.stdout)
                    for artifact in json.loads(profile[3]):
                        self.assertIn(artifact["location"], result.stdout)
                    stage = "1拷问" if profile[0] == "design-discussion" else "2方案"
                    self.assertIn(f"本次按用户要求在 {stage} 结束。", result.stdout)
                    for text in ("是否", "下一步", "无法使用", "尚未核实", "交接暂缓"):
                        self.assertNotIn(text, result.stdout)

    def test_blocked_handoff_preserves_results_without_advancing_or_asking(self):
        for profile in PROFILES:
            for available in ("false", "unknown"):
                with self.subTest(skill=profile[0], stage=profile[2], available=available):
                    result = self.run_report(profile, "true", available)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(SUMMARY, result.stdout)
                    for artifact in json.loads(profile[3]):
                        self.assertIn(artifact["location"], result.stdout)
                    self.assertNotIn("是否", result.stdout)
                    self.assertNotIn("下一步将进入", result.stdout)
                    if available == "unknown":
                        self.assertIn("尚未核实", result.stdout)
                        self.assertNotIn("无法使用", result.stdout)
                    else:
                        self.assertIn("无法使用", result.stdout)
                        self.assertNotIn("尚未核实", result.stdout)

    def test_continuous_mode_must_be_supplied_explicitly(self):
        for profile in PROFILES:
            with self.subTest(skill=profile[0], stage=profile[2]):
                result = self.run_report(profile, None, "true")
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("--continuous-mode", result.stderr)

    def test_invalid_availability_does_not_emit_a_completion_report(self):
        for profile in PROFILES:
            with self.subTest(skill=profile[0], stage=profile[2]):
                result = self.run_report(profile, "true", "invalid")
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("--" + profile[1], result.stderr)

    def test_handoff_report_empty_artifacts_are_valid_only_for_design_discussion(self):
        for profile in PROFILES:
            with self.subTest(skill=profile[0], stage=profile[2]):
                result = self.run_report(profile, "false", "unknown", artifacts="[]")
                if profile[0] == "design-discussion":
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(SUMMARY, result.stdout)
                else:
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
