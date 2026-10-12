import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY / "scripts" / "prepare-deployment.py"
SPEC = importlib.util.spec_from_file_location("prepare_deployment", SCRIPT)
deployment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deployment)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        shutil.copyfile(REPOSITORY / "LICENSE", self.repository / "LICENSE")
        for name in deployment.SKILLS:
            shutil.copytree(REPOSITORY / "skills" / name, self.repository / "skills" / name)
        for arguments in (
            ["init", "--quiet"], ["add", "."],
            ["-c", "user.name=Deployment Test", "-c", "user.email=deployment@example.invalid",
             "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
             "commit", "--quiet", "-m", "Fixture"],
        ):
            subprocess.run(["git", "-C", str(self.repository)] + arguments,
                           check=True, capture_output=True)
        self.output = self.root / "candidate"

    def write(self, skill, relative, content):
        path = self.repository / "skills" / skill / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_build_preserves_exact_payload_and_omits_fifth_skill_and_caches(self):
        self.write("problem-framing", "SKILL.md", "old fifth skill")
        self.write("design-discussion", "scripts/__pycache__/cached.pyc", "cache")
        executable = self.repository / "skills/design-discussion/scripts/render-result.py"
        executable.chmod(0o755)
        manifest = deployment.build(self.repository, self.output)
        self.assertEqual(set(p.name for p in (self.output / "skills").iterdir()), set(deployment.SKILLS))
        self.assertTrue(manifest["source"]["worktree_dirty"])
        self.assertFalse(list(self.output.rglob(".gitkeep")))
        self.assertFalse(list(self.output.rglob("*.pyc")))
        for skill in manifest["skills"]:
            source = self.output / skill["candidate_source"]
            self.assertEqual((source / "LICENSE").read_bytes(), (REPOSITORY / "LICENSE").read_bytes())
            for file in skill["files"]:
                payload = (source / file["path"]).read_bytes()
                self.assertEqual(hashlib.sha256(payload).hexdigest(), file["sha256"])
                self.assertEqual(len(payload), file["size"])
            original = self.repository / "skills" / skill["name"]
            for path in original.rglob("*"):
                if path.is_file() and path.name != ".gitkeep" and "__pycache__" not in path.parts:
                    self.assertEqual(path.read_bytes(), (source / path.relative_to(original)).read_bytes())
        copied = self.output / "skills/design-discussion/scripts/render-result.py"
        self.assertEqual(copied.stat().st_mode & 0o777, 0o755)
        self.assertEqual(deployment.verify(self.output), manifest)

    def test_verify_works_without_source_checkout(self):
        deployment.build(self.repository, self.output)
        shutil.rmtree(self.repository)
        manifest = deployment.verify(self.output)
        self.assertEqual([skill["name"] for skill in manifest["skills"]], list(deployment.SKILLS))

    def test_verify_rejects_changed_missing_added_files_and_permissions(self):
        cases = ["changed", "missing", "added", "mode", "fifth"]
        for case in cases:
            with self.subTest(case=case):
                output = self.root / case
                deployment.build(self.repository, output)
                path = output / "skills/change-closure/SKILL.md"
                if case == "changed":
                    path.write_text(path.read_text() + "\nchanged\n")
                elif case == "missing":
                    path.unlink()
                elif case == "added":
                    (output / "skills/change-closure/extra.txt").write_text("extra")
                elif case == "mode":
                    path.chmod(0o600)
                else:
                    (output / "skills/problem-framing").mkdir()
                with self.assertRaises(deployment.DeploymentError):
                    deployment.verify(output)

    def test_invalid_source_fails_before_creating_output(self):
        skill = "design-discussion"
        path = self.repository / "skills" / skill / "SKILL.md"
        original = path.read_text()
        cases = {
            "yaml": "---\nname: [\n---\n",
            "name": original.replace("name: design-discussion", "name: another-skill"),
            "stage": original.replace('stage: "1"', 'stage: "0"'),
            "non-json-yaml": original.replace("license: MIT", "license: 2026-10-12"),
            "missing-dependency": original.replace("`grill-with-docs`", "`other-skill`"),
            "reference": original + "\n[missing](references/missing.md)\n",
            "escaping": original + "\n[escape](../../LICENSE)\n",
        }
        for case, content in cases.items():
            with self.subTest(case=case):
                path.write_text(content)
                with self.assertRaises(deployment.DeploymentError):
                    deployment.build(self.repository, self.output)
                self.assertFalse(self.output.exists())
        path.write_text(original)
        self.write(skill, "scripts/invalid.py", "def broken(:\n")
        with self.assertRaisesRegex(deployment.DeploymentError, "Python 语法"):
            deployment.build(self.repository, self.output)
        self.assertFalse(self.output.exists())

    def test_rejects_resource_symlinks(self):
        external = self.root / "external.txt"
        external.write_text("outside")
        (self.repository / "skills/change-closure/linked.txt").symlink_to(external)
        with self.assertRaisesRegex(deployment.DeploymentError, "软链接"):
            deployment.build(self.repository, self.output)
        self.assertFalse(self.output.exists())

    def test_build_preserves_existing_output_and_rejects_source_directory(self):
        self.output.mkdir()
        sentinel = self.output / "keep.txt"
        sentinel.write_text("keep")
        with self.assertRaisesRegex(deployment.DeploymentError, "已存在"):
            deployment.build(self.repository, self.output)
        self.assertEqual(sentinel.read_text(), "keep")
        with self.assertRaisesRegex(deployment.DeploymentError, "skills 目录"):
            deployment.build(self.repository, self.repository / "skills/candidate")
        self.assertFalse((self.repository / "skills/candidate").exists())

    def test_manifest_path_tampering_is_rejected(self):
        deployment.build(self.repository, self.output)
        path = self.output / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["skills"][0]["candidate_source"] = "../../outside"
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(deployment.DeploymentError, "不一致"):
            deployment.verify(self.output)

    def test_cli_check_build_and_verify_use_json_and_keep_source_unchanged(self):
        copied_script = self.repository / "scripts/prepare-deployment.py"
        copied_script.parent.mkdir()
        shutil.copyfile(SCRIPT, copied_script)
        before = (self.repository / "skills/design-discussion/SKILL.md").read_bytes()
        for arguments in (["check"], ["build", "--output", str(self.output)],
                          ["verify", "--package", str(self.output)]):
            result = subprocess.run([os.sys.executable, "-B", str(copied_script)] + arguments,
                                    cwd=str(self.root), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["workflow_execution"], "not_run")
            self.assertEqual(len(report["skills"]), 4)
        self.assertEqual(before, (self.repository / "skills/design-discussion/SKILL.md").read_bytes())
        failed = subprocess.run([os.sys.executable, "-B", str(copied_script), "build",
                                 "--output", str(self.output)], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 1)
        self.assertIn("已存在", failed.stderr)


if __name__ == "__main__":
    unittest.main()
