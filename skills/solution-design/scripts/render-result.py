#!/usr/bin/env python3
# coding: utf-8
"""根据固定参数生成 2方案的 Markdown 结果报告。"""

import argparse
import importlib.util
import json
import string
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit


sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location("task_progress", Path(__file__).with_name("task-progress.py"))
progress = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(progress)


def parse_artifacts(value):
    try:
        artifacts = json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError("成果必须是有效的 JSON 数组") from error
    if not isinstance(artifacts, list) or not artifacts:
        raise argparse.ArgumentTypeError("成果必须是非空数组")

    for index, artifact in enumerate(artifacts, start=1):
        if not isinstance(artifact, dict) or set(artifact) != {"name", "location"}:
            raise argparse.ArgumentTypeError(
                f"第 {index} 项成果必须包含且仅包含 name 和 location"
            )
        for field in ("name", "location"):
            value = artifact[field]
            if not isinstance(value, str) or not value.strip():
                raise argparse.ArgumentTypeError(f"第 {index} 项成果的 {field} 必须是非空文本")
            if any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise argparse.ArgumentTypeError(f"第 {index} 项成果的 {field} 必须是单行文本")
            artifact[field] = value.strip()

        location = artifact["location"]
        if Path(location).is_absolute():
            continue
        try:
            url = urlsplit(location)
            hostname = url.hostname
            url.port
        except ValueError as error:
            raise argparse.ArgumentTypeError(f"第 {index} 项成果的链接无效") from error
        if url.scheme not in ("http", "https") or not hostname or any(char.isspace() for char in hostname):
            raise argparse.ArgumentTypeError(
                f"第 {index} 项成果的位置必须是绝对文件路径或 HTTP(S) 链接"
            )
    return artifacts


def escape_text(value):
    return "".join("\\" + char if char in string.punctuation else char for char in value)


def artifact_link(artifact):
    location = artifact["location"]
    safe = "/:~@-._" if Path(location).is_absolute() else "/:?#@!$&'*,;=+%-._~"
    return f"[{escape_text(artifact['name'])}](<{quote(location, safe=safe)}>)"


def format_duration(seconds):
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    units = ((days, "天"), (hours, "小时"), (minutes, "分钟"))
    return " ".join([f"{value} {unit}" for value, unit in units if value] + [f"{seconds} 秒"])


def render_result(summary, artifacts, started_at, scope,
                  implementation_skill_available, now):
    duration = int((now - started_at).total_seconds())
    lines = ["**2方案已完成**", "", "**方案摘要**  "]
    lines.extend(escape_text(line.strip()) for line in summary.strip().splitlines())
    lines.extend(["", "**成果**", ""])
    lines.extend(f"- {artifact_link(artifact)}" for artifact in artifacts)
    lines.extend([
        "",
        f"**开始时间**：{started_at.isoformat(sep=' ', timespec='seconds')}  ",
        f"**总耗时**：{format_duration(duration)}",
        "",
        "**执行范围**  ",
        escape_text(progress.scope_description(scope)),
        "",
        "---",
        "",
        "**下一步**",
        "",
        "建议进入 **3实现**，按已确认的方案完成实现与验收。",
        "",
    ])

    if scope["end_stage"] == "2":
        lines.append("本次按用户要求在 2方案 结束。")
    elif implementation_skill_available is None:
        lines.append(
            "**3实现** 对应 Skill 的可用情况尚未核实，交接暂缓。"
            "进入该流程前需重新核实。"
        )
    elif implementation_skill_available:
        lines.append("当前是连续模式，下一步将进入 3实现 流程")
    else:
        lines.append(
            "当前是连续模式，但当前环境无法使用 `guided-implementation`。"
            "请先检查该 Skill 的安装、启用状态及入口，再进入 3实现 流程。"
        )

    return "\n".join("> " + line if line else ">" for line in lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="同一进度文件的绝对路径")
    parser.add_argument("--task-id", required=True, help="创建时返回的固定任务标识")
    parser.add_argument("--project", required=True, help="原目标项目的绝对路径")
    parser.add_argument("--at", help="实际报告生成完成时间，须有时区；默认取当前时间")
    parser.add_argument("--summary", required=True, help="本次方案摘要")
    parser.add_argument(
        "--artifacts", required=True, type=parse_artifacts,
        help='成果 JSON 数组，例如 [{"name":"Spec","location":"/absolute/path/spec.md"}]',
    )
    parser.add_argument(
        "--implementation-skill-available", required=True, choices=("true", "false", "unknown"),
        help="guided-implementation 的可用情况：true 已核实可用，false 已确认不可用，unknown 尚未核实",
    )
    args = parser.parse_args()
    if not args.summary.strip():
        parser.error("方案摘要不能为空")
    try:
        path = progress.absolute_path(args.file, "进度文档")
        project = progress.absolute_path(args.project, "目标项目")
        at = progress.event_time(args.at)
        data = progress.read_document(path)
        progress.match_task(data, args.task_id, project)
        phase = data["stages"][-1]
        if phase["stage"] != "2" or phase["status"] != "进行中" or len(phase["steps"]) != 3:
            raise progress.ProgressError("方案报告须在当前 2方案前三步完成后、收尾登记前生成")
        if data["execution_scope"]["end_stage"] == "1":
            raise progress.ProgressError("当前方案阶段超出用户限定的执行范围，须先核对实际状态")
        now = progress.timestamp(at)
        if now < progress.timestamp(phase["steps"][-1]["finished_at"]):
            raise progress.ProgressError("报告时间不能早于已完成步骤")
        print(render_result(
            args.summary, args.artifacts, progress.timestamp(phase["started_at"]),
            data["execution_scope"],
            {"true": True, "false": False, "unknown": None}[args.implementation_skill_available], now,
        ), end="")
    except (progress.ProgressError, OSError, UnicodeError) as error:
        parser.exit(1, f"无法生成 2方案报告：{error}\n")


if __name__ == "__main__":
    main()
