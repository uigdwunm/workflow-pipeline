#!/usr/bin/env python3
# coding: utf-8
"""用一份临时 Markdown 文件记录阶段时间及已完成的大步骤。"""

import argparse
import json
import os
import re
import stat
import string
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit


STAGES = {"1": "1拷问", "2": "2方案", "3": "3实现", "4": "4归档"}
STEPS = {
    "1": ("确认讨论要点", "执行拷问", "验收与交接"),
    "2": ("确定工作区", "生成方案", "验收成果", "报告与交接"),
    "3": ("准备", "执行实现", "验收与整改", "报告与交接"),
    "4": ("接收成果", "分支检查与文档收尾", "合并交付", "更新项目状态", "清理与报告"),
}
EMPTY = "—"
STAGE_COLUMNS = ("阶段", "执行轮次", "状态", "开始时间", "结束时间", "终止原因")
STEP_COLUMNS = ("编号", "大步骤", "简要结果", "完成时间")
ARTIFACT_COLUMNS = ("产物", "实际位置")
CLOSURE_FIELDS = (("worktree", "集成 Worktree"), ("branch", "集成分支"),
                  ("target_branch", "目标分支"), ("implementation_commit", "待归档实现提交"))

class ProgressError(ValueError):
    pass


def text(value, label, optional=False):
    if not isinstance(value, str):
        raise ProgressError(f"{label}必须是文本")
    if any(ord(char) < 32 or ord(char) == 127 or char in "\x85\u2028\u2029" for char in value):
        raise ProgressError(f"{label}必须是单行文本，不能包含控制字符或换行符")
    value = value.strip()
    if not value and not optional:
        raise ProgressError(f"{label}不能为空")
    return value


def absolute_path(value, label):
    path = Path(text(str(value), label))
    if not path.is_absolute():
        raise ProgressError(f"{label}必须是绝对路径")
    return path


def artifacts(value):
    if not isinstance(value, list):
        raise ProgressError("产物必须是 JSON 数组")
    result = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"name", "location"}:
            raise ProgressError("每项产物必须仅包含 name 和 location")
        name, location = text(item["name"], "产物名称"), text(item["location"], "产物位置")
        if not Path(location).is_absolute():
            try:
                url = urlsplit(location)
                hostname = url.hostname
                url.port
            except ValueError as error:
                raise ProgressError("产物链接无效") from error
            if url.scheme not in ("http", "https") or not hostname or any(char.isspace() for char in hostname):
                raise ProgressError("产物位置必须是绝对路径或 HTTP(S) 链接")
        result.append({"name": name, "location": location})
    return result


def closure_input(value):
    fields = {key for key, _ in CLOSURE_FIELDS} | {"artifacts"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ProgressError("归档输入必须仅包含 worktree、branch、target_branch、implementation_commit、artifacts")
    result = {key: text(value[key], label) for key, label in CLOSURE_FIELDS}
    result["worktree"] = str(absolute_path(result["worktree"], "集成 Worktree"))
    if re.fullmatch(r"[0-9a-fA-F]{7,64}", result["implementation_commit"]) is None:
        raise ProgressError("实现提交必须是 7 至 64 位十六进制提交号")
    result["artifacts"] = artifacts(value["artifacts"])
    if not result["artifacts"]:
        raise ProgressError("归档输入须包含实际已有的产物")
    return result


def timestamp(value):
    try:
        moment = datetime.fromisoformat(text(value, "时间").replace("Z", "+00:00"))
    except ValueError as error:
        raise ProgressError("时间必须是包含时区的 ISO 8601 时间") from error
    if moment.utcoffset() is None:
        raise ProgressError("时间必须包含时区")
    return moment


def event_time(value=None):
    now = datetime.now(timezone.utc)
    moment = timestamp(value) if value is not None else now
    if moment > now:
        raise ProgressError("记录时间不能晚于当前时间")
    return moment.isoformat(timespec="seconds")


def escape(value):
    return "".join("\\" + char if char in string.punctuation else char for char in str(value))


def unescape(value):
    return re.sub(r"\\([" + re.escape(string.punctuation) + r"])", r"\1", value)


def link(path, label):
    return f"[{escape(label)}](<{quote(str(path), safe='/:~@-._')}>)"


def read_link(value):
    match = re.fullmatch(r"\[((?:\\.|[^\]\\])*)\]\(<([^<>]*)>\)", value)
    if match is None:
        raise ProgressError("目标项目链接格式无效")
    path = str(absolute_path(unquote(match[2]), "链接路径"))
    return {"path": path, "section": unescape(match[1])}


def row(values):
    return "| " + " | ".join(values) + " |"


def cells(line):
    if not line.startswith("| ") or not line.endswith(" |"):
        raise ProgressError("进度表格格式无效")
    result, current = [], []
    index = 1
    while index < len(line):
        char = line[index]
        if char == "\\" and index + 1 < len(line):
            current.extend(line[index:index + 2])
            index += 2
            continue
        if char == "|":
            result.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    return result


def next_step(phase):
    if phase["finished_at"] is not None:
        return None
    index = len(phase["steps"])
    return {"id": f"{phase['stage']}.{index + 1}", "name": STEPS[phase["stage"]][index]}


def current(data):
    phase = data["stages"][-1]
    label = f"{STAGES[phase['stage']]}（第 {phase['run']} 次）"
    upcoming = next_step(phase)
    step = f"{upcoming['id']} {upcoming['name']}" if upcoming else f"阶段{phase['status']}"
    return label, step


def render(data):
    stage, step = current(data)
    lines = [
        "# 任务进度", "", "## 任务信息", "",
        f"- 任务标识：{escape(data['task_id'])}",
        f"- 本次任务：{escape(data['summary'])}",
        f"- 目标项目：{link(data['project'], data['project'])}",
        f"- 当前阶段：{escape(stage)}",
        f"- 当前大步骤：{escape(step)}", "", "## 阶段时间", "",
        row(STAGE_COLUMNS), row(["---"] * len(STAGE_COLUMNS)),
    ]
    for phase in data["stages"]:
        lines.append(row([
            STAGES[phase["stage"]], str(phase["run"]), phase["status"],
            phase["started_at"], phase["finished_at"] or EMPTY, escape(phase["note"]),
        ]))
    for phase in data["stages"]:
        if "closure" in phase:
            lines.extend(["", f"## 4归档输入（第 {phase['run']} 次）", ""])
            lines.extend(f"- {label}：{escape(phase['closure'][key])}" for key, label in CLOSURE_FIELDS)
            lines.extend(["", row(ARTIFACT_COLUMNS), row(["---"] * len(ARTIFACT_COLUMNS))])
            lines.extend(row([escape(item["name"]), escape(item["location"])])
                         for item in phase["closure"]["artifacts"])
        lines.extend([
            "", f"## {STAGES[phase['stage']]}已完成步骤（第 {phase['run']} 次）", "",
            row(STEP_COLUMNS), row(["---"] * len(STEP_COLUMNS)),
        ])
        lines.extend(row([
            escape(item["id"]), escape(item["name"]), escape(item["note"]), item["finished_at"],
        ]) for item in phase["steps"])
    return "\n".join(lines) + "\n"


def validate(data):
    if str(uuid.UUID(data["task_id"])) != data["task_id"]:
        raise ProgressError("任务标识无效")
    text(data["summary"], "任务简介")
    absolute_path(data["project"], "目标项目")
    if not data["stages"]:
        raise ProgressError("进度文档缺少阶段")
    runs, previous_end = {}, None
    for position, phase in enumerate(data["stages"]):
        stage = phase["stage"]
        if stage not in STAGES:
            raise ProgressError("阶段必须是 1、2、3 或 4")
        if "closure" in phase:
            if stage != "4" or closure_input(phase["closure"]) != phase["closure"]:
                raise ProgressError("归档输入只能记录在 4归档，且须符合固定格式")
        runs[stage] = runs.get(stage, 0) + 1
        if phase["run"] != runs[stage]:
            raise ProgressError("阶段执行轮次不连续")
        start = timestamp(phase["started_at"])
        if previous_end is not None and start < previous_end:
            raise ProgressError("不同阶段的执行时间不能重叠")
        if len(phase["steps"]) > len(STEPS[stage]):
            raise ProgressError("记录超出本阶段的大步骤数量")
        last_time = start
        for index, item in enumerate(phase["steps"], 1):
            if item["id"] != f"{stage}.{index}" or item["name"] != STEPS[stage][index - 1]:
                raise ProgressError("大步骤编号、名称或顺序无效")
            text(item["note"], "简要结果")
            finish = timestamp(item["finished_at"])
            if finish < last_time:
                raise ProgressError(f"步骤 {item['id']} 的完成时间早于上一记录")
            last_time = finish
        end = timestamp(phase["finished_at"]) if phase["finished_at"] else None
        text(phase["note"], "终止原因", optional=True)
        if end is None:
            if (phase["status"] != "进行中" or phase["note"] or position != len(data["stages"]) - 1
                    or len(phase["steps"]) == len(STEPS[stage])):
                raise ProgressError("未结束阶段的状态或位置无效")
        elif end < last_time:
            raise ProgressError("阶段结束时间早于已完成步骤")
        elif phase["status"] == "已完成":
            if len(phase["steps"]) != len(STEPS[stage]) or phase["note"] or end != last_time:
                raise ProgressError("已完成阶段须在最后一个大步骤完成时结束")
        elif phase["status"] != "已终止" or not phase["note"]:
            raise ProgressError("终止阶段必须保留原因")
        previous_end = end


def parse_document(content):
    lines, position = content.splitlines(), 0

    def expect(value):
        nonlocal position
        if position >= len(lines) or lines[position] != value:
            raise ProgressError(f"进度文档第 {position + 1} 行格式无效，应为 {value!r}")
        position += 1

    def field(label):
        nonlocal position
        prefix = f"- {label}："
        if position >= len(lines) or not lines[position].startswith(prefix):
            raise ProgressError(f"进度文档缺少{label}")
        value = lines[position][len(prefix):]
        position += 1
        return value

    def table(columns):
        nonlocal position
        expect(row(columns))
        expect(row(["---"] * len(columns)))
        result = []
        while position < len(lines) and lines[position].startswith("|"):
            values = cells(lines[position])
            if len(values) != len(columns):
                raise ProgressError(f"进度文档第 {position + 1} 行列数无效")
            result.append(values)
            position += 1
        return result

    expect("# 任务进度")
    expect("")
    expect("## 任务信息")
    expect("")
    data = {"task_id": unescape(field("任务标识")), "summary": unescape(field("本次任务")),
            "project": read_link(field("目标项目"))["path"], "stages": []}
    field("当前阶段")
    field("当前大步骤")
    expect("")
    expect("## 阶段时间")
    expect("")
    for name, run, state, start, finish, note in table(STAGE_COLUMNS):
        if name not in STAGES.values() or not run.isdecimal():
            raise ProgressError("阶段名称或执行轮次无效")
        data["stages"].append({
            "stage": name[0], "run": int(run), "status": state, "started_at": start,
            "finished_at": None if finish == EMPTY else finish, "note": unescape(note), "steps": [],
        })
    for phase in data["stages"]:
        if position + 1 < len(lines) and lines[position + 1] == f"## 4归档输入（第 {phase['run']} 次）":
            expect("")
            expect(f"## 4归档输入（第 {phase['run']} 次）")
            expect("")
            phase["closure"] = {key: unescape(field(label)) for key, label in CLOSURE_FIELDS}
            expect("")
            phase["closure"]["artifacts"] = [
                {"name": unescape(name), "location": unescape(location)}
                for name, location in table(ARTIFACT_COLUMNS)
            ]
        expect("")
        expect(f"## {STAGES[phase['stage']]}已完成步骤（第 {phase['run']} 次）")
        expect("")
        phase["steps"] = [{"id": unescape(identifier), "name": unescape(name),
                           "note": unescape(note), "finished_at": finish}
                          for identifier, name, note, finish in table(STEP_COLUMNS)]
    if position != len(lines):
        raise ProgressError("进度文档包含不属于固定格式的内容")
    validate(data)
    if render(data) != content:
        raise ProgressError("进度文档与固定格式不一致，请核对实际记录，不能覆盖重建")
    return data


def read_document(path):
    if path.is_symlink() or not path.is_file():
        raise ProgressError(f"进度文件不存在或不是普通文件：{path}")
    try:
        return parse_document(path.read_text(encoding="utf-8"))
    except (ValueError, KeyError, TypeError, IndexError) as error:
        raise ProgressError(f"无法读取进度文档 {path}：{error}") from error


def match_task(data, task_id, project):
    if data["task_id"] != task_id:
        raise ProgressError("进度文档的任务标识与本次任务不一致")
    if Path(data["project"]).resolve() != project.resolve():
        raise ProgressError("进度文档的目标项目与本次任务不一致")


@contextmanager
def lock_document(path):
    lock = path.with_name(path.name + ".lock")
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError as error:
        raise ProgressError(f"进度文档正在更新或上次更新中断，请核实实际状态：{lock}") from error
    try:
        yield
    finally:
        lock.rmdir()


def write_document(path, data, create=False):
    validate(data)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent,
                                         prefix=".task-progress-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(render(data))
            stream.flush()
            os.fsync(stream.fileno())
        if create:
            os.link(temporary, path)
        else:
            temporary.chmod(stat.S_IMODE(path.stat().st_mode))
            os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def latest_stage(data, stage):
    for phase in reversed(data["stages"]):
        if phase["stage"] == stage:
            return phase
    raise ProgressError(f"本任务尚未进入 {STAGES[stage]}")


def enter_stage(data, stage, at, new_run=False, closure=None):
    if closure is not None:
        if stage != "4":
            raise ProgressError("归档输入只适用于 4归档")
        closure = closure_input(closure)
    existing = next((phase for phase in reversed(data["stages"]) if phase["stage"] == stage), None)
    if existing is not None and not new_run:
        attach_closure(existing, closure)
        return
    if any(phase["finished_at"] is None for phase in data["stages"]):
        raise ProgressError("当前阶段尚未结束，不能进入另一阶段")
    data["stages"].append({"stage": stage, "run": existing["run"] + 1 if existing else 1,
                           "status": "进行中", "started_at": at, "finished_at": None,
                           "note": "", "steps": []})
    attach_closure(data["stages"][-1], closure)


def attach_closure(phase, closure):
    if closure is None:
        return
    if "closure" in phase:
        if phase["closure"] != closure:
            raise ProgressError("本轮归档已有不同输入，不能覆盖原记录")
        return
    if phase["steps"] or phase["finished_at"] is not None:
        raise ProgressError("须在第一个大步骤完成前保存归档输入")
    phase["closure"] = closure


def create_document(path, project, summary, stage, at, closure=None):
    summary = text(summary, "任务简介")
    if not project.is_dir():
        raise ProgressError(f"目标项目目录不存在：{project}")
    with lock_document(path):
        if path.exists() or path.is_symlink():
            data = read_document(path)
            if (Path(data["project"]).resolve() != project.resolve() or data["summary"] != summary
                    or data["stages"][-1]["stage"] != stage):
                raise ProgressError("已有进度文档的任务或阶段不一致，原文件已保留")
            original = render(data)
            enter_stage(data, stage, at, closure=closure)
            if render(data) != original:
                write_document(path, data)
            return data
        data = {"task_id": str(uuid.uuid4()), "summary": summary, "project": str(project.resolve()), "stages": []}
        enter_stage(data, stage, at, closure=closure)
        write_document(path, data, create=True)
        return data


def complete_step(data, stage, number, note, at):
    phase = latest_stage(data, stage)
    note = text(note, "简要结果")
    if not 1 <= number <= len(STEPS[stage]):
        raise ProgressError("只能记录本阶段已有的大步骤")
    if number <= len(phase["steps"]):
        if phase["steps"][number - 1]["note"] != note:
            raise ProgressError("该大步骤已记录不同结果，不能覆盖原记录")
        return
    if phase["finished_at"] is not None:
        raise ProgressError("该阶段已结束；重新执行用 enter --new-run 保留旧记录")
    if number != len(phase["steps"]) + 1:
        raise ProgressError("请按实际顺序记录大步骤，不能跳过未完成的步骤")
    phase["steps"].append({"id": f"{stage}.{number}", "name": STEPS[stage][number - 1],
                           "note": note, "finished_at": at})
    if number == len(STEPS[stage]):
        phase["status"], phase["finished_at"] = "已完成", at


def stop_stage(data, stage, note, at):
    phase = latest_stage(data, stage)
    note = text(note, "终止原因")
    if phase["finished_at"] is not None:
        if phase["status"] != "已终止" or phase["note"] != note:
            raise ProgressError("该阶段已结束，不能改写结束状态或原因")
        return
    phase["status"], phase["finished_at"], phase["note"] = "已终止", at, note


def summarize(data):
    durations = []
    for stage in sorted({phase["stage"] for phase in data["stages"]}):
        phases = [phase for phase in data["stages"] if phase["stage"] == stage]
        known = sum(int((timestamp(phase["finished_at"]) - timestamp(phase["started_at"])).total_seconds())
                    for phase in phases if phase["finished_at"] is not None)
        ended = all(phase["finished_at"] is not None for phase in phases)
        durations.append({"stage": stage, "name": STAGES[stage], "runs": len(phases),
                          "duration_seconds": known if ended else None, "known_duration_seconds": known})
    stage, step = current(data)
    total = sum(item["known_duration_seconds"] for item in durations)
    return {**data, "current_stage": stage, "current_step": step, "stage_durations": durations,
            "known_duration_seconds": total,
            "total_duration_seconds": total if all(item["duration_seconds"] is not None for item in durations) else None}


def compact_result(path, data):
    phase = data["stages"][-1]
    return {"file": str(path), "task_id": data["task_id"], "project": data["project"],
            "stage": phase["stage"], "run": phase["run"], "status": phase["status"],
            "started_at": phase["started_at"], "finished_at": phase["finished_at"],
            "last_completed_step": phase["steps"][-1]["id"] if phase["steps"] else None,
            "next_step": next_step(phase)}


def json_object(value):
    try:
        return json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError("归档输入必须是有效 JSON") from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command, description in (
        ("create", "首次创建文档并进入阶段"), ("enter", "接续或进入下一阶段"),
        ("complete", "大步骤完成时记录一句结果和时间；最后一步自动结束阶段"),
        ("stop", "明确取消或切换流程时终止阶段，保留已完成步骤"),
        ("show", "恢复任务或生成报告时读取完整记录及阶段耗时"),
        ("cleanup", "整个任务已结束且报告已保留后删除临时进度文档"),
    ):
        subparser = commands.add_parser(command, help=description)
        subparser.add_argument("--project", required=True, help="原目标项目的绝对路径")
        if command == "create":
            subparser.add_argument("--output", required=True, help="进度文档绝对路径，父目录须已存在")
            subparser.add_argument("--summary", required=True, help="原始任务的问题和目标，使用简短单行文本")
        else:
            subparser.add_argument("--file", required=True, help="同一进度文档的绝对路径")
            subparser.add_argument("--task-id", required=True, help="创建时返回的固定任务标识")
        if command in {"create", "enter", "complete", "stop"}:
            subparser.add_argument("--stage", required=True, choices=tuple(STAGES))
            subparser.add_argument("--at", help="实际事件的 ISO 8601 时间，须有时区；默认取当前时间")
        if command == "enter":
            subparser.add_argument("--new-run", action="store_true", help="明确重新执行已结束阶段时追加一轮")
        if command in {"create", "enter"}:
            subparser.add_argument("--closure", type=json_object, help="4归档的输入 JSON，保存在同一文件中")
        if command == "complete":
            subparser.add_argument("--step", required=True, type=int, help="本阶段大步骤序号，例如 1")
        if command in {"complete", "stop"}:
            subparser.add_argument("--note", required=True, help="一句结果或终止原因")
        if command == "cleanup":
            subparser.add_argument("--task-finished", action="store_true", required=True)
    args = parser.parse_args()
    try:
        project = absolute_path(args.project, "目标项目")
        path = absolute_path(args.output if args.command == "create" else args.file, "进度文档")
        if args.command == "create":
            data = create_document(path, project, args.summary, args.stage, event_time(args.at), args.closure)
        else:
            with lock_document(path):
                data = read_document(path)
                match_task(data, args.task_id, project)
                original = render(data)
                if args.command == "enter":
                    enter_stage(data, args.stage, event_time(args.at), args.new_run, args.closure)
                elif args.command == "complete":
                    complete_step(data, args.stage, args.step, args.note, event_time(args.at))
                elif args.command == "stop":
                    stop_stage(data, args.stage, args.note, event_time(args.at))
                elif args.command == "cleanup":
                    if any(phase["finished_at"] is None for phase in data["stages"]):
                        raise ProgressError("还有未结束的阶段，必须保留进度文档")
                    path.unlink()
                if args.command not in {"show", "cleanup"}:
                    validate(data)
                    if render(data) != original:
                        write_document(path, data)
        result = {"file": str(path), **summarize(data)} if args.command == "show" else compact_result(path, data)
        if args.command == "cleanup":
            result["removed"] = True
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ProgressError, OSError, UnicodeError) as error:
        parser.exit(1, f"无法处理任务进度：{error}\n")


if __name__ == "__main__":
    main()
