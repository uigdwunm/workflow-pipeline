#!/usr/bin/env python3
# coding: utf-8
"""核对统一进度，生成并保存 1拷问固定报告，登记收尾后返回完整 Markdown。"""

import argparse
import hashlib
import importlib.util
import json
import string
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit


STAGE_NAMES = {"2": "2方案", "3": "3实现"}
STAGE_SKILLS = {"2": "solution-design", "3": "guided-implementation"}
sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location("task_progress", Path(__file__).with_name("task-progress.py"))
progress = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(progress)


def parse_text(value):
    if not value.strip():
        raise argparse.ArgumentTypeError("必须是非空文本")
    if any((ord(char) < 32 and char not in "\n\r\t") or ord(char) == 127 for char in value):
        raise argparse.ArgumentTypeError("文本不能包含控制字符")
    return value.strip()


def parse_artifacts(value):
    try:
        artifacts = json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError("成果必须是有效的 JSON 数组") from error
    if not isinstance(artifacts, list):
        raise argparse.ArgumentTypeError("成果必须是数组，无文档产物时传入 []")

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


def render_result(summary, artifacts, started_at, recommended_stage,
                  recommendation, scope, next_skill_available, now):
    duration = int((now - started_at).total_seconds())
    stage_name = STAGE_NAMES[recommended_stage]
    handoff_stage = str(min(int(recommended_stage), int(scope["end_stage"])))
    handoff_name = STAGE_NAMES.get(handoff_stage)
    skill_name = STAGE_SKILLS.get(handoff_stage)
    lines = ["**1拷问已完成**", "", "**拷问摘要**  "]
    lines.extend(escape_text(line.strip()) for line in summary.strip().splitlines())
    lines.extend(["", "**成果**", ""])
    if artifacts:
        lines.extend(f"- {artifact_link(artifact)}" for artifact in artifacts)
    else:
        lines.append("本次没有文档产物。")
    lines.extend([
        "",
        f"**开始时间**：{started_at.isoformat(sep=' ', timespec='seconds')}  ",
        f"**总耗时**：{format_duration(duration)}",
    ])
    lines.extend(["", "**执行范围**  ", escape_text(progress.scope_description(scope)),
                  "", "---", "", "**下一步**", ""])
    recommendation_lines = [
        escape_text(line.strip()) for line in recommendation.strip().splitlines()
    ]
    lines.append(f"建议进入 **{stage_name}**，{recommendation_lines[0]}")
    lines.extend(recommendation_lines[1:])
    lines.append("")
    if scope["end_stage"] == "1":
        lines.append("本次按用户要求在 1拷问 结束。")
    elif next_skill_available is None:
        lines.append(
            f"**{handoff_name}** 对应 Skill 的可用情况尚未核实，交接暂缓。"
            "进入该流程前需重新核实。"
        )
    elif next_skill_available:
        lines.append(f"当前是连续模式，下一步将进入 {handoff_name} 流程")
    else:
        lines.append(
            f"当前是连续模式，但当前环境无法使用 `{skill_name}`。"
            f"请先检查该 Skill 的安装、启用状态及入口，再进入 {handoff_name} 流程。"
        )

    return "\n".join("> " + line if line else ">" for line in lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="同一进度文件的绝对路径")
    parser.add_argument("--task-id", required=True, help="创建时返回的固定任务标识")
    parser.add_argument("--project", required=True, help="原目标项目的绝对路径")
    parser.add_argument("--at", help="实际报告生成完成时间，须有时区；默认取当前时间")
    parser.add_argument(
        "--summary", required=True, type=parse_text,
        help="本次拷问摘要和已确认结论，支持多行文本",
    )
    parser.add_argument(
        "--artifacts", required=True, type=parse_artifacts,
        help='文档产物 JSON 数组，例如 [{"name":"讨论文档","location":"/absolute/path/discussion.md"}]；无文档产物时传入 []',
    )
    parser.add_argument(
        "--recommended-stage", required=True, choices=("2", "3"),
        help="根据已确认讨论结果建议进入的阶段：2方案或3实现",
    )
    parser.add_argument(
        "--recommendation", required=True, type=parse_text,
        help="建议进入该阶段的理由，支持多行文本",
    )
    parser.add_argument(
        "--next-skill-available", required=True, choices=("true", "false", "unknown"),
        help="执行范围内实际接续阶段的 Skill 可用情况：true 可用，false 不可用，unknown 尚未核实",
    )
    args = parser.parse_args()
    try:
        path = progress.absolute_path(args.file, "进度文档")
        project = progress.absolute_path(args.project, "目标项目")
        at = progress.event_time(args.at)
        inputs = {name: getattr(args, name) for name in (
            "summary", "artifacts", "recommended_stage", "recommendation", "next_skill_available",
        )}
        digest = hashlib.sha256(json.dumps(inputs, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        with progress.lock_document(path):
            data = progress.read_document(path)
            progress.match_task(data, args.task_id, project)
            phase = data["stages"][-1]
            if phase["stage"] != "1":
                raise progress.ProgressError("当前阶段不是 1拷问")
            if phase["finished_at"] is not None:
                if "report" not in phase or phase["status"] != "已完成":
                    raise progress.ProgressError("本轮已结束且没有固定报告记录，请核实原结果；不能覆盖重建")
                if phase["report"]["input_sha256"] != digest:
                    raise progress.ProgressError("本轮已有不同报告输入，不能覆盖原报告")
                print(phase["report"]["markdown"], end="", flush=True)
                return
            progress.confirmed_discussion(phase)
            if len(phase["steps"]) != 2:
                raise progress.ProgressError("生成完成报告前须完成确认讨论要点和执行拷问两步")
            markdown = render_result(
                args.summary, args.artifacts, progress.timestamp(phase["started_at"]),
                args.recommended_stage, args.recommendation, data["execution_scope"],
                {"true": True, "false": False, "unknown": None}[args.next_skill_available], progress.timestamp(at),
            )
            progress.finish_discussion(data, markdown, digest, at)
            # 输出失败时保留未结束的原文件；写入失败时调用方须按非零退出状态处理，保留输出待恢复。
            print(markdown, end="", flush=True)
            progress.write_document(path, data)
    except (progress.ProgressError, OSError, UnicodeError) as error:
        parser.exit(1, f"无法生成并登记 1拷问报告：{error}\n")


if __name__ == "__main__":
    main()
