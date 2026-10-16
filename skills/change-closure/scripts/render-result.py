#!/usr/bin/env python3
# coding: utf-8
"""读取统一进度生成固定归档报告；--complete 同时记录最后一步和结束时间。"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from urllib.parse import quote


sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location("task_progress", Path(__file__).with_name("task-progress.py"))
progress = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(progress)

RESULT_LABELS = ("成果核对", "分支与文档", "交付与验证", "项目状态", "资源处理")


def parse_artifacts(value):
    try:
        return progress.artifacts(json.loads(value))
    except (json.JSONDecodeError, progress.ProgressError) as error:
        raise argparse.ArgumentTypeError(f"成果参数无效：{error}") from error


def artifact_link(item):
    location = item["location"]
    safe = "/:~@-._" if Path(location).is_absolute() else "/:?#@!$&'*,;=+%-._~"
    return f"[{progress.escape(item['name'])}](<{quote(location, safe=safe)}>)"


def format_duration(seconds):
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    units = ((days, "天"), (hours, "小时"), (minutes, "分钟"))
    return " ".join([f"{value} {unit}" for value, unit in units if value] + [f"{seconds} 秒"])


def report_durations(data, at):
    durations = progress.summarize(data)["stage_durations"]
    for item in durations:
        active = next((phase for phase in data["stages"]
                       if phase["stage"] == item["stage"] and phase["finished_at"] is None), None)
        item["ongoing"] = active is not None
        if active is not None:
            elapsed = int((progress.timestamp(at) - progress.timestamp(active["started_at"])).total_seconds())
            last_time = active["steps"][-1]["finished_at"] if active["steps"] else active["started_at"]
            if progress.timestamp(at) < progress.timestamp(last_time):
                raise progress.ProgressError("报告时间早于当前已记录的进度")
            item["duration_seconds"] = item["known_duration_seconds"] + elapsed
    return durations


def render_result(data, summary, artifacts, pending, resources, at):
    phase = data["stages"][-1]
    state = {"进行中": "未完成", "已完成": "已完成", "已终止": "已终止"}[phase["status"]]
    closure = phase.get("closure", {})
    lines = [
        "**本次任务**", "", progress.escape(summary), "",
        f"**4归档{state}**", "",
        f"**目标项目**：{artifact_link({'name': data['project'], 'location': data['project']})}",
        f"**目标分支**：{progress.escape(closure.get('target_branch', '尚未核实'))}", "",
        "**归档结果**", "",
    ]
    for index, label in enumerate(RESULT_LABELS):
        note = phase["steps"][index]["note"] if index < len(phase["steps"]) else "尚无完成记录"
        if index == 4 and resources is not None:
            note = resources
        lines.append(f"- **{label}**：{progress.escape(note)}")
    lines.extend(["", "**成果位置**", ""])
    lines.extend(f"- {artifact_link(item)}" for item in artifacts)
    if not artifacts:
        lines.append("- 无额外文件或链接")
    lines.extend(["", "**未完成事项**", "", progress.escape(pending), "", "**阶段用时**", ""])
    durations = report_durations(data, at)
    for item in durations:
        suffix = "（截至本次报告，阶段尚未结束）" if item["ongoing"] else ""
        lines.append(f"- **{item['name']}**：{format_duration(item['duration_seconds'])}{suffix}")
    total = sum(item["duration_seconds"] for item in durations)
    time_label = "归档结束时间" if phase["finished_at"] else "统计时刻"
    ending = phase["finished_at"] or at
    lines.extend([
        "", f"**流程累计用时**：{format_duration(total)}",
        "按各阶段起止时间累计，包含阶段内等待，不包含阶段间等待。", "",
        f"**归档开始时间**：{progress.timestamp(phase['started_at']).isoformat(sep=' ', timespec='seconds')}",
        f"**{time_label}**：{progress.timestamp(ending).isoformat(sep=' ', timespec='seconds')}",
    ])
    return "\n".join("> " + line if line else ">" for line in lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="统一进度文件的绝对路径")
    parser.add_argument("--task-id", required=True, help="创建时返回的固定任务标识")
    parser.add_argument("--project", required=True, help="原目标项目的绝对路径")
    parser.add_argument("--summary", required=True, help="整个任务的目标与最终确认范围，使用非空单行文本")
    parser.add_argument("--artifacts", required=True, type=parse_artifacts, help="当前实际交付位置的 JSON 数组，允许 []")
    parser.add_argument("--complete", action="store_true", help="归档前四步和资源处理均已完成，生成报告并结束阶段")
    parser.add_argument("--resources", help="一句实际资源处理结果，包括保留或不由本 Skill 管理的资源")
    parser.add_argument("--pending", required=True, help="显式填写未完成和待决事项及接续条件；核实均无后填“无”")
    parser.add_argument("--at", help="实际报告时间，含时区的 ISO 8601 时间；默认取当前时间")
    args = parser.parse_args()
    try:
        path = progress.absolute_path(args.file, "进度文档")
        project = progress.absolute_path(args.project, "目标项目")
        summary = progress.text(args.summary, "任务描述")
        pending = progress.text(args.pending, "未完成事项")
        resources = progress.text(args.resources, "资源处理结果") if args.resources is not None else None
        at = progress.event_time(args.at)
        with progress.lock_document(path):
            data = progress.read_document(path)
            progress.match_task(data, args.task_id, project)
            phase = data["stages"][-1]
            if phase["stage"] != "4":
                raise progress.ProgressError("当前阶段不是 4归档")
            if (phase["status"] == "已完成" and resources is not None
                    and resources != phase["steps"][4]["note"]):
                raise progress.ProgressError("归档已结束，须沿用已记录的资源处理结果")
            original = progress.render(data)
            if args.complete:
                if "closure" not in phase or len(phase["steps"]) < 4:
                    raise progress.ProgressError("归档输入及前四个大步骤尚未完整记录")
                if resources is None:
                    raise progress.ProgressError("结束归档须提供实际资源处理结果")
                if pending != "无":
                    raise progress.ProgressError("存在未完成事项，不能生成已完成报告")
                progress.complete_step(data, "4", 5, resources, at)
                progress.validate(data)
            if phase["status"] != "已完成" and pending == "无":
                raise progress.ProgressError("未结束归档须说明未完成事项及接续条件")
            if phase["status"] == "已完成" and pending != "无":
                raise progress.ProgressError("已结束归档的记录与未完成事项不一致，请先核实实际状态")
            result = render_result(data, summary, args.artifacts, pending, resources, at)
            if progress.render(data) != original:
                progress.write_document(path, data)
        print(result, end="")
    except (progress.ProgressError, OSError, UnicodeError) as error:
        parser.exit(1, f"无法生成归档报告：{error}\n")


if __name__ == "__main__":
    main()
