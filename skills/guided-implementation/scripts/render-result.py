#!/usr/bin/env python3
# coding: utf-8
"""读取统一进度生成任务结果报告，不修改进度；第 8 步由调用方登记。"""

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
    units = ((days, "天"), (hours, "小时"), (minutes, "分钟"), (seconds, "秒"))
    return " ".join(f"{value} {unit}" for value, unit in units if value) or "0 秒"


def report_durations(data, at, complete):
    durations = progress.summarize(data)["stage_durations"]
    for item in durations:
        active = next((phase for phase in data["stages"]
                       if phase["stage"] == item["stage"] and phase["finished_at"] is None), None)
        item["ongoing"] = active is not None and not complete
        if active is not None:
            elapsed = int((progress.timestamp(at) - progress.timestamp(active["started_at"])).total_seconds())
            last_time = active["steps"][-1]["finished_at"] if active["steps"] else active["started_at"]
            if progress.timestamp(at) < progress.timestamp(last_time):
                raise progress.ProgressError("报告时间早于当前已记录的进度")
            item["duration_seconds"] = item["known_duration_seconds"] + elapsed
    return durations


def render_result(data, summary, artifacts, pending, delivery_method, at, complete,
                  implementation_result, validation_result, closure_result):
    phase = data["stages"][-1]
    state = "已完成" if complete else {"进行中": "未完成", "已终止": "已终止"}[phase["status"]]
    closure = phase.get("closure", {})
    lines = [
        "**本次任务**", "", progress.escape(summary), "",
        f"**3实现{state}**", "",
        "**完成内容**", "", progress.escape(implementation_result), "",
        "**验证结果**", "", progress.escape(validation_result), "",
        "**交付与收尾**", "",
        f"- **目标项目**：{artifact_link({'name': data['project'], 'location': data['project']})}",
        f"- **交付方式**：{progress.escape(delivery_method or '尚未核实')}",
        f"- **目标分支**：{progress.escape(closure.get('target_branch', '尚未核实'))}",
        f"- **已验收实现提交**：{progress.escape(closure.get('implementation_commit', '尚未核实'))}",
        f"- **收尾结果**：{progress.escape(closure_result)}",
    ]
    lines.extend(["", "**成果位置**", ""])
    lines.extend(f"- {artifact_link(item)}" for item in artifacts)
    if not artifacts:
        lines.append("- 无额外文件或链接")
    lines.extend(["", "**未完成事项**", "", progress.escape(pending), "", "**阶段用时**", ""])
    lines.extend(["| 阶段 | 用时 |", "| --- | --- |"])
    durations = report_durations(data, at, complete)
    for item in durations:
        suffix = "（截至本次报告，阶段尚未结束）" if item["ongoing"] else ""
        duration = format_duration(item["duration_seconds"])
        if item["runs"] > 1:
            duration = f"累计 {duration}，共 {item['runs']} 轮"
            if any(run["stage"] == item["stage"] and run["status"] == "已终止"
                   for run in data["stages"]):
                duration += "（含已终止轮次）"
        lines.append(f"| {item['name']} | {duration}{suffix} |")
    total = sum(item["duration_seconds"] for item in durations)
    lines.extend([
        f"| **累计** | **{format_duration(total)}** |", "",
        "按各阶段起止时间累计，包含阶段内等待，不包含阶段间等待。只展示实际进入过的阶段。",
    ])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="统一进度文件的绝对路径")
    parser.add_argument("--task-id", required=True, help="创建时返回的固定任务标识")
    parser.add_argument("--project", required=True, help="原目标项目的绝对路径")
    parser.add_argument("--summary", required=True, help="整个任务的目标与最终确认范围，使用非空单行文本")
    parser.add_argument("--implementation-result", required=True, help="实际改动与最终行为，非空单行文本")
    parser.add_argument("--validation-result", required=True, help="测试、审查及验证限制，非空单行文本")
    parser.add_argument("--closure-result", required=True, help="文档、项目状态及资源处理结果，非空单行文本")
    parser.add_argument("--artifacts", required=True, type=parse_artifacts, help="当前实际交付位置的 JSON 数组，允许 []")
    parser.add_argument("--complete", action="store_true", help="前七步及归档输入已完整记录，生成完成报告；不登记进度")
    parser.add_argument("--delivery-method", help="已核实的实际交付方式，完成报告必须提供")
    parser.add_argument("--pending", required=True, help="显式填写未完成和待决事项及接续条件；核实均无后填“无”")
    parser.add_argument("--at", help="报告采用的含时区 ISO 时间；默认取当前时间，第 8 步登记时沿用")
    args = parser.parse_args()
    try:
        path = progress.absolute_path(args.file, "进度文档")
        project = progress.absolute_path(args.project, "目标项目")
        summary = progress.text(args.summary, "任务描述")
        implementation_result = progress.text(args.implementation_result, "完成内容")
        validation_result = progress.text(args.validation_result, "验证结果")
        closure_result = progress.text(args.closure_result, "收尾结果")
        pending = progress.text(args.pending, "未完成事项")
        delivery_method = progress.text(args.delivery_method, "交付方式") if args.delivery_method is not None else None
        at = progress.event_time(args.at)
        with progress.lock_document(path):
            data = progress.read_document(path)
            progress.match_task(data, args.task_id, project)
        phase = data["stages"][-1]
        if phase["stage"] != "3":
            raise progress.ProgressError("当前阶段不是 3实现")
        complete = args.complete or phase["status"] == "已完成"
        if complete:
            if phase["status"] == "已终止":
                raise progress.ProgressError("本轮已终止，不能生成已完成报告")
            if "closure" not in phase or len(phase["steps"]) < 7:
                raise progress.ProgressError("归档输入及前七个大步骤尚未完整记录")
            if delivery_method is None:
                raise progress.ProgressError("完成报告须提供已核实的交付方式")
            if pending != "无":
                raise progress.ProgressError("存在未完成事项，不能生成已完成报告")
        elif pending == "无":
            raise progress.ProgressError("未完成或已终止的任务须说明未完成事项及接续条件")
        if phase["finished_at"] and progress.timestamp(at) < progress.timestamp(phase["finished_at"]):
            raise progress.ProgressError("报告时间早于阶段结束时间")
        result = render_result(data, summary, args.artifacts, pending, delivery_method, at, complete,
                               implementation_result, validation_result, closure_result)
        print(result, end="")
    except (progress.ProgressError, OSError, UnicodeError) as error:
        parser.exit(1, f"无法生成任务结果报告：{error}\n")


if __name__ == "__main__":
    main()
