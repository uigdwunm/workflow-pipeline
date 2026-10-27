#!/usr/bin/env python3
# coding: utf-8
"""检查、打包并复核交给 codex-manager 的三个新版 Skill；不执行安装。"""

import argparse
import ast
import hashlib
import json
import posixpath
import re
import stat
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

try:
    import yaml
except ImportError:
    sys.exit("缺少 PyYAML；请使用能够运行 codex-manager 的 Python 环境。")


SKILLS = {
    "design-discussion": {
        "stage": "1",
        "required_skills": ["grill-with-docs", "grilling", "domain-modeling"],
        "handoff_skills": ["solution-design", "guided-implementation"],
        "dependency_files": ["SKILL.md", "references/clarification-flow.md"],
        "project_requirements": [],
    },
    "solution-design": {
        "stage": "2",
        "required_skills": ["to-spec", "to-tickets"],
        "handoff_skills": ["guided-implementation"],
        "project_requirements": ["issue tracker、发布位置和 triage 标签约定"],
    },
    "guided-implementation": {
        "stage": "3",
        "required_skills": ["code-review"],
        # 分支依赖按条件核实，不作为所有任务的共同前置条件。
        "conditional_skills": {
            "implement-spec": "已有 Spec 的实现路径",
            "implement": "没有 Spec 的小改动实现路径",
            "tdd": "Spec 路径，或小改动路径需要使用 TDD 时",
        },
        "handoff_skills": ["solution-design"],
        "dependency_files": [
            "SKILL.md",
            "references/spec-execution.md",
            "references/small-change-execution.md",
        ],
        "project_requirements": ["docs/agents/issue-tracker.md"],
    },
}
CHECKS = ["skill_metadata", "agents_metadata", "python_syntax", "local_references"]
GENERATED_NAMES = {".git", ".gitkeep", ".DS_Store", "__pycache__", ".pytest_cache"}
REPOSITORY = Path(__file__).resolve().parents[1]
MODEL_PREFERENCES_SOURCE = "preferences/subagent-models.md"
MODEL_PREFERENCES_TARGET = "references/subagent-model-preferences.md"
MODEL_PREFERENCES_CONSUMERS = {
    "solution-design": "references/subagent-execution.md",
    "guided-implementation": "references/agent-settings.md",
}


class DeploymentError(ValueError):
    pass


def read_file(path):
    if path.is_symlink() or not path.is_file():
        raise DeploymentError(f"必须是普通文件，不能是软链接：{path}")
    return path.read_bytes(), stat.S_IMODE(path.stat().st_mode)


def collect_files(directory, exclude_generated=True):
    if directory.is_symlink() or not directory.is_dir():
        raise DeploymentError(f"必须是实际目录，不能是软链接：{directory}")
    files = {}

    def visit(folder):
        for path in sorted(folder.iterdir()):
            if exclude_generated and (
                path.name in GENERATED_NAMES or path.suffix in {".pyc", ".pyo"}
            ):
                continue
            if any(ord(char) < 32 or ord(char) == 127 for char in path.name):
                raise DeploymentError(f"文件名不能包含控制字符：{path!s}")
            if path.is_symlink():
                raise DeploymentError(f"候选资源不能包含软链接：{path}")
            if path.is_dir():
                visit(path)
            else:
                files[path.relative_to(directory).as_posix()] = read_file(path)

    visit(directory)
    return files


def text_content(content, location):
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DeploymentError(f"文本文件必须使用 UTF-8：{location}") from error


def yaml_mapping(content, location):
    try:
        value = yaml.safe_load(content)
    except yaml.YAMLError as error:
        raise DeploymentError(f"YAML 无效：{location}：{error}") from error
    if not isinstance(value, dict):
        raise DeploymentError(f"YAML 必须是对象：{location}")
    try:
        converted = json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise DeploymentError(f"YAML 元数据必须可保存为 JSON：{location}") from error
    if converted != value:
        raise DeploymentError(f"YAML 元数据必须使用文本对象键：{location}")
    return value


def check_reference(target, origin, files, name):
    target = target.strip().strip("<>")
    try:
        parsed = urlsplit(target)
    except ValueError as error:
        raise DeploymentError(f"链接无效：{name}/{origin}：{target}") from error
    if parsed.scheme or parsed.netloc or not parsed.path or parsed.path.startswith("/"):
        return
    relative = posixpath.normpath(posixpath.join(posixpath.dirname(origin), unquote(parsed.path)))
    if relative.startswith("../") or relative == ".." or relative not in files:
        raise DeploymentError(f"包内引用缺失或越界：{name}/{origin} -> {target}")


def inspect_skill(name, files):
    if "SKILL.md" not in files:
        raise DeploymentError(f"缺少入口：{name}/SKILL.md")
    body = text_content(files["SKILL.md"][0], f"{name}/SKILL.md")
    frontmatter = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", body, re.DOTALL)
    if not frontmatter:
        raise DeploymentError(f"缺少有效 YAML frontmatter：{name}/SKILL.md")
    metadata = yaml_mapping(frontmatter.group(1), f"{name}/SKILL.md")
    if metadata.get("name") != name:
        raise DeploymentError(f"Skill 名称与目录不一致：{name}/SKILL.md")
    description = metadata.get("description")
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise DeploymentError(f"description 必须是 1 至 1024 字符的文本：{name}/SKILL.md")
    stage = metadata.get("metadata", {})
    if not isinstance(stage, dict) or stage.get("stage") != SKILLS[name]["stage"]:
        raise DeploymentError(f"metadata.stage 必须是字符串 {SKILLS[name]['stage']}：{name}/SKILL.md")
    dependency_files = SKILLS[name].get("dependency_files", ["SKILL.md"])
    for relative in dependency_files:
        if relative not in files:
            raise DeploymentError(f"缺少依赖声明文件：{name}/{relative}")
    instructions = "\n".join(
        text_content(files[relative][0], f"{name}/{relative}") for relative in dependency_files
    )
    conditional_skills = SKILLS[name].get("conditional_skills", {})
    dependencies = (
        SKILLS[name]["required_skills"] + list(conditional_skills) + SKILLS[name]["handoff_skills"]
    )
    for dependency in dependencies:
        if f"`{dependency}`" not in instructions:
            raise DeploymentError(f"部署清单与 Skill 正文不一致，未找到依赖或交接：{name} -> {dependency}")

    interface = {}
    allow_implicit = True
    if "agents/openai.yaml" in files:
        agents = yaml_mapping(
            text_content(files["agents/openai.yaml"][0], f"{name}/agents/openai.yaml"),
            f"{name}/agents/openai.yaml",
        )
        interface = agents.get("interface", {})
        policy = agents.get("policy", {})
        if not isinstance(interface, dict) or not isinstance(policy, dict):
            raise DeploymentError(f"interface 和 policy 必须是对象：{name}/agents/openai.yaml")
        allow_implicit = policy.get("allow_implicit_invocation", True)
        if type(allow_implicit) is not bool:
            raise DeploymentError(f"allow_implicit_invocation 必须是布尔值：{name}/agents/openai.yaml")
        for key in ("icon_small", "icon_large"):
            if key in interface:
                if not isinstance(interface[key], str):
                    raise DeploymentError(f"{key} 必须是资源路径：{name}/agents/openai.yaml")
                # Codex 图标路径相对于 Skill 根目录。
                check_reference(interface[key], "SKILL.md", files, name)

    inventory = []
    for relative, (content, mode) in sorted(files.items()):
        if relative.endswith(".py"):
            try:
                ast.parse(content, filename=f"{name}/{relative}")
            except (SyntaxError, ValueError) as error:
                raise DeploymentError(f"Python 语法无效：{name}/{relative}：{error}") from error
        if relative.endswith(".md"):
            markdown = text_content(content, f"{name}/{relative}")
            for target in re.findall(r"\[[^\]\n]*\]\(([^)\n]+)\)", markdown):
                check_reference(target, relative, files, name)
        inventory.append({
            "path": relative,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
            "mode": mode,
        })
    package_hash = hashlib.sha256(
        json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "name": name,
        "candidate_source": f"skills/{name}",
        "metadata": metadata,
        "interface": interface,
        "allow_implicit_invocation": allow_implicit,
        "required_skills": SKILLS[name]["required_skills"],
        **({"conditional_skills": conditional_skills} if conditional_skills else {}),
        "handoff_skills": SKILLS[name]["handoff_skills"],
        "project_requirements": SKILLS[name]["project_requirements"],
        "package_sha256": package_hash,
        "files": inventory,
    }


def git_source(repository):
    def run(arguments):
        result = subprocess.run(
            ["git", "-C", str(repository)] + arguments,
            capture_output=True, text=True, check=False,
        )
        if result.returncode:
            raise DeploymentError(f"无法取得 Git 来源：{repository}：{result.stderr.strip()}")
        return result.stdout.strip()

    return {
        "repository": str(repository),
        "git_commit": run(["rev-parse", "HEAD"]),
        "worktree_dirty": bool(run(["status", "--porcelain", "--untracked-files=normal"])),
    }


def prepare(repository):
    license_file = read_file(repository / "LICENSE")
    preference_path = repository / MODEL_PREFERENCES_SOURCE
    if preference_path.parent.is_symlink():
        raise DeploymentError(f"偏好来源目录不能是软链接：{preference_path.parent}")
    preference_file = read_file(preference_path)
    snapshots = {}
    skills = []
    for name in SKILLS:
        files = collect_files(repository / "skills" / name)
        if name in MODEL_PREFERENCES_CONSUMERS:
            if MODEL_PREFERENCES_TARGET in files:
                raise DeploymentError(f"偏好副本仅在打包时生成，请修改共享来源：{name}/{MODEL_PREFERENCES_TARGET}")
            origin = MODEL_PREFERENCES_CONSUMERS[name]
            if origin not in files:
                raise DeploymentError(f"缺少偏好入口：{name}/{origin}")
            source_link = f"../../../{MODEL_PREFERENCES_SOURCE}".encode("utf-8")
            content, mode = files[origin]
            if source_link not in content:
                raise DeploymentError(f"缺少共享偏好引用：{name}/{origin}")
            files[origin] = (
                content.replace(source_link, posixpath.basename(MODEL_PREFERENCES_TARGET).encode("utf-8")),
                mode,
            )
            files[MODEL_PREFERENCES_TARGET] = preference_file
        files.setdefault("LICENSE", license_file)
        skills.append(inspect_skill(name, files))
        snapshots[name] = files
    manifest = {
        "schema_version": 1,
        "source": git_source(repository),
        "skills": skills,
        "excluded_skills": ["problem-framing", "change-closure"],
        "validation": {"status": "passed", "checks": CHECKS, "workflow_execution": "not_run"},
    }
    return manifest, snapshots


def verify(package):
    if package.is_symlink() or not package.is_dir():
        raise DeploymentError(f"候选包必须是实际目录：{package}")
    if {path.name for path in package.iterdir()} != {"manifest.json", "skills"}:
        raise DeploymentError(f"候选包根目录只能包含 manifest.json 和 skills：{package}")
    content, _ = read_file(package / "manifest.json")
    try:
        manifest = json.loads(content)
    except (ValueError, UnicodeDecodeError) as error:
        raise DeploymentError(f"候选清单不是有效 JSON：{package}/manifest.json") from error
    if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        raise DeploymentError("候选清单 schema_version 必须是 1")
    skills_root = package / "skills"
    if skills_root.is_symlink() or not skills_root.is_dir() or {p.name for p in skills_root.iterdir()} != set(SKILLS):
        raise DeploymentError("候选包必须且只能包含三个已选择的 Skill")
    actual = [inspect_skill(name, collect_files(skills_root / name, exclude_generated=False)) for name in SKILLS]
    if manifest.get("skills") != actual:
        raise DeploymentError("候选包内容、文件权限或元数据与 manifest.json 不一致")
    if manifest.get("excluded_skills") != ["problem-framing", "change-closure"] or manifest.get("validation") != {
        "status": "passed", "checks": CHECKS, "workflow_execution": "not_run",
    }:
        raise DeploymentError("候选清单的选择范围或验证声明不一致")
    source = manifest.get("source")
    if not isinstance(source, dict) or not isinstance(source.get("repository"), str) or not re.fullmatch(
        r"[0-9a-f]{40}|[0-9a-f]{64}", str(source.get("git_commit", ""))
    ) or type(source.get("worktree_dirty")) is not bool:
        raise DeploymentError("候选清单缺少有效的 Git 来源信息")
    return manifest


def build(repository, output):
    if not output.is_absolute():
        raise DeploymentError("--output 必须是绝对路径")
    if output.exists() or output.is_symlink():
        raise DeploymentError(f"输出位置已存在，原内容已保留：{output}")
    if not output.parent.is_dir():
        raise DeploymentError(f"输出父目录必须已存在：{output.parent}")
    source_root = (repository / "skills").resolve()
    if output.resolve() == source_root or source_root in output.resolve().parents:
        raise DeploymentError("候选包不能生成在仓库 skills 目录内")
    manifest, snapshots = prepare(repository)
    output.mkdir(mode=0o700)
    try:
        for name, files in snapshots.items():
            for relative, (content, mode) in files.items():
                path = output / "skills" / name / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as handle:
                    handle.write(content)
                path.chmod(mode)
        with (output / "manifest.json").open("x", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        return verify(output)
    except (OSError, DeploymentError) as error:
        raise DeploymentError(f"生成失败，候选目录保留供检查，请勿安装：{output}：{error}") from error


def report(manifest, package=None):
    result = {
        "status": "passed",
        "source": manifest["source"],
        "skills": [
            {"name": skill["name"], "stage": skill["metadata"]["metadata"]["stage"],
             "package_sha256": skill["package_sha256"], "file_count": len(skill["files"])}
            for skill in manifest["skills"]
        ],
        "checks": CHECKS,
        "workflow_execution": "not_run",
    }
    if package is not None:
        result["package"] = str(package)
        result["manifest"] = str(package / "manifest.json")
        result["checks"] = CHECKS + ["bundle_integrity"]
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="只读检查当前仓库的三个 Skill")
    build_parser = commands.add_parser("build", help="生成新候选目录，不覆盖已有文件")
    build_parser.add_argument("--output", required=True, type=Path)
    verify_parser = commands.add_parser("verify", help="独立复核候选包及清单，不执行 Skill")
    verify_parser.add_argument("--package", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "check":
            manifest, _ = prepare(REPOSITORY)
            report(manifest)
        elif args.command == "build":
            report(build(REPOSITORY, args.output), args.output)
        else:
            report(verify(args.package), args.package)
    except (DeploymentError, OSError) as error:
        print(f"部署准备失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
