#!/usr/bin/env python3
"""根据固定步骤和任务参数生成 4归档执行清单，保留已有文件。"""

import argparse
import json
import re
import string
from pathlib import Path
from urllib.parse import quote, urlsplit


STEPS = (
    "接收实现成果",
    "检查归档前提",
    "检查 main 的变化",
    "完成文档收尾",
    "核对最终待合并成果",
    "合并并完成代码交付",
    "完成项目状态收尾",
    "处理 Worktree",
    "清理本次任务分支",
    "整体复检并报告结果",
    "归档对话",
)


def parse_text(value):
    if not isinstance(value, str) or not value.strip():
        raise argparse.ArgumentTypeError("必须是非空文本")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise argparse.ArgumentTypeError("必须是单行文本，不能包含控制字符")
    return value.strip()


def parse_path(value):
    path = Path(parse_text(value))
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("必须是绝对文件路径")
    return path


def parse_location(value):
    location = parse_text(value)
    if Path(location).is_absolute():
        return location
    try:
        url = urlsplit(location)
        hostname = url.hostname
        url.port
    except ValueError as error:
        raise argparse.ArgumentTypeError("产物链接无效") from error
    if url.scheme not in ("http", "https") or not hostname or any(
        char.isspace() for char in hostname
    ):
        raise argparse.ArgumentTypeError("必须是绝对文件路径或 HTTP(S) 链接")
    return location


def parse_commit(value):
    commit = parse_text(value)
    if re.fullmatch(r"[0-9a-fA-F]{7,64}", commit) is None:
        raise argparse.ArgumentTypeError("实现提交必须是 7 至 64 位十六进制提交号")
    return commit


def parse_artifacts(value):
    try:
        artifacts = json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError("产物必须是有效的 JSON 数组") from error
    if not isinstance(artifacts, list) or not artifacts:
        raise argparse.ArgumentTypeError("产物必须是非空数组")
    for index, artifact in enumerate(artifacts, start=1):
        if not isinstance(artifact, dict) or set(artifact) != {"name", "location"}:
            raise argparse.ArgumentTypeError(
                f"第 {index} 项产物必须包含且仅包含 name 和 location"
            )
        try:
            artifact["name"] = parse_text(artifact["name"])
            artifact["location"] = parse_location(artifact["location"])
        except argparse.ArgumentTypeError as error:
            raise argparse.ArgumentTypeError(f"第 {index} 项产物无效：{error}") from error
    return artifacts


def escape_text(value):
    return "".join("\\" + char if char in string.punctuation else char for char in str(value))


def location_link(location):
    location = str(location)
    safe = "/:~@-._" if Path(location).is_absolute() else "/:?#@!$&'*,;=+%-._~"
    target = quote(location, safe=safe)
    return f"[{escape_text(location)}](<{target}>)"


def render_checklist(project, worktree, branch, target_branch, implementation_commit,
                     artifacts, execution_path):
    lines = [
        "# 4归档执行清单",
        "",
        "本清单用于辅助本次归档。按下表顺序执行，并记录各步骤的状态和必要的简短信息。",
        "",
        "## 任务信息",
        "",
        f"- 目标项目：{location_link(project)}",
        f"- 集成 Worktree：{location_link(worktree)}",
        f"- 集成分支：{escape_text(branch)}",
        f"- 目标分支：{escape_text(target_branch)}",
        f"- 待归档实现提交：`{implementation_commit}`",
        "",
        "以上信息在接收实现成果时核对。",
        "",
        "## 已有结果产物",
        "",
        "| 产物 | 文件位置或链接 |",
        "|---|---|",
    ]
    lines.extend(
        f"| {escape_text(artifact['name'])} | {location_link(artifact['location'])} |"
        for artifact in artifacts
    )
    lines.extend([
        "",
        "## 执行与记录规则",
        "",
        "1. 按执行表格的顺序推进。执行每一步前，读取该行指定的执行说明文件及章节。",
        "2. 开始处理时，将状态更新为“进行中”；核实该步骤达到执行说明中的完成条件后，更新为“已完成”，再进入下一步。",
        "3. 符合执行说明中的跳过条件时，更新为“跳过”，记录原因，再进入下一步。",
        "4. 无法继续或操作结果尚不明确时，更新为“阻塞”，记录问题及已知结果，暂停依赖该步骤的后续操作。结果未知时，先核实原操作，再决定如何接续。",
        "5. 简短记录填写有助于核对和接续的信息，例如提交号、关键结果、跳过原因或阻塞事项。",
        "6. 中断后接续时，核对清单与实际状态，再继续未完成的工作。原完成依据失效时，将受影响步骤恢复为“待执行”。",
        "",
        "状态使用：**待执行、进行中、已完成、跳过、阻塞**。",
        "",
        "## 执行表格",
        "",
        "| 序号 | 步骤 | 状态 | 执行说明 | 简短记录 |",
        "|---|---|---|---|---|",
    ])
    for number, step in enumerate(STEPS, start=1):
        reference = f"{location_link(execution_path)}（第 {number} 节：{step}）"
        lines.append(f"| {number} | {step} | 待执行 | {reference} | |")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=parse_path, help="目标项目的绝对路径")
    parser.add_argument("--worktree", required=True, type=parse_path, help="集成 Worktree 的绝对路径")
    parser.add_argument("--branch", required=True, type=parse_text, help="集成分支")
    parser.add_argument("--target-branch", default="main", type=parse_text, help="目标分支，默认 main")
    parser.add_argument(
        "--implementation-commit", required=True, type=parse_commit, help="待归档实现提交号",
    )
    parser.add_argument(
        "--artifacts", required=True, type=parse_artifacts,
        help='实际已有产物的非空 JSON 数组，例如 [{"name":"Spec","location":"/absolute/path/spec.md"}]',
    )
    parser.add_argument(
        "--output", required=True, type=parse_path,
        help="与 Spec 一起保存的清单绝对路径；所在目录须已存在，已有文件会保留",
    )
    args = parser.parse_args()
    execution_path = Path(__file__).resolve().parents[1] / "references" / "execution.md"
    if not execution_path.is_file():
        parser.error(f"执行说明文件不存在：{execution_path}")
    content = render_checklist(
        args.project, args.worktree, args.branch, args.target_branch,
        args.implementation_commit, args.artifacts, execution_path,
    )
    try:
        with args.output.open("x", encoding="utf-8") as output:
            output.write(content)
    except FileExistsError:
        parser.error(f"清单已存在，原文件已保留，请读取后接续：{args.output}")
    except OSError as error:
        parser.error(f"无法生成清单 {args.output}：{error}")
    print(f"已生成执行清单：{args.output}")


if __name__ == "__main__":
    main()
