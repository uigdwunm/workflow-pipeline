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
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit


STAGES = {"1": "1拷问", "2": "2方案", "3": "3实现"}
STEPS = {
    "1": ("确认讨论要点", "执行拷问", "验收与交接"),
    "2": ("确定工作区", "生成方案", "验收成果", "报告与交接"),
    "3": (
        "准备",
        "执行实现",
        "实现验收与整改",
        "分支检查与文档收尾",
        "整合与交付",
        "更新项目状态",
        "清理资源",
        "最终报告",
    ),
}
EMPTY = "—"
STAGE_COLUMNS = ("阶段", "执行轮次", "状态", "开始时间", "结束时间", "终止原因")
STEP_COLUMNS = ("编号", "大步骤", "简要结果", "完成时间")
ARTIFACT_COLUMNS = ("产物", "实际位置")
CLOSURE_FIELDS = (("worktree", "集成 Worktree"), ("branch", "集成分支"),
                  ("target_branch", "目标分支"), ("implementation_commit", "已验收实现提交"))
DISCUSSION_FIELDS = (("goal", "目标"), ("decisions", "已有想法与决定"),
                     ("scope", "范围和约束"), ("open_questions", "未决问题"),
                     ("inferences", "模型推断（尚未确认）"))

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


def discussion_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ProgressError(f"{label}必须是非空文本")
    if any((ord(char) < 32 and char not in "\n\r\t") or ord(char) == 127
           or char in "\x85\u2028\u2029" for char in value):
        raise ProgressError(f"{label}不能包含控制字符或特殊换行符")
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def discussion_points(value):
    if not isinstance(value, dict) or set(value) != {key for key, _ in DISCUSSION_FIELDS}:
        raise ProgressError("讨论要点必须仅包含 goal、decisions、scope、open_questions、inferences")
    return {key: discussion_text(value[key], label) for key, label in DISCUSSION_FIELDS}


def execution_scope(value):
    if not isinstance(value, dict) or set(value) != {"end_stage", "user_instruction"}:
        raise ProgressError("执行范围须包含 end_stage 和 user_instruction")
    if not isinstance(value["end_stage"], str) or value["end_stage"] not in STAGES:
        raise ProgressError("执行结束阶段必须是 1、2 或 3")
    instruction = value["user_instruction"]
    if instruction is None:
        if value["end_stage"] != "3":
            raise ProgressError("提前结束须提供用户原话和可定位来源")
    else:
        if not isinstance(instruction, dict) or set(instruction) != {"text", "source"}:
            raise ProgressError("执行范围依据须包含用户原话 text 和来源 source")
        instruction = {"text": discussion_text(instruction["text"], "执行范围的用户原话"),
                       "source": text(instruction["source"], "执行范围的用户来源")}
    return {"end_stage": value["end_stage"], "user_instruction": instruction}


def make_execution_scope(end_stage="3", reply=None, source=None):
    instruction = None if reply is None and source is None else {"text": reply, "source": source}
    return execution_scope({"end_stage": end_stage, "user_instruction": instruction})


def set_scope(data, end_stage, reply, source):
    if reply is None or source is None:
        raise ProgressError("修改执行范围须提供用户原话和可定位来源")
    data["execution_scope"] = make_execution_scope(end_stage, reply, source)


def scope_description(scope):
    end = STAGES[scope["end_stage"]]
    instruction = scope["user_instruction"]
    if instruction is None:
        return "默认连续执行至 3实现及收尾。"
    quote = instruction["text"].replace("\n", " ")
    return f"按用户要求执行至 {end}；依据：“{quote}”（来源：{instruction['source']}）。"


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
    if phase.get("discussions") and phase["discussions"][-1]["confirmation"] is None:
        return {"id": "1.1", "name": STEPS["1"][0]}
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
        f"- 当前大步骤：{escape(step)}", "", "## 执行范围", "", "```json",
        json.dumps(data["execution_scope"], ensure_ascii=False, indent=2), "```",
        "", "## 阶段时间", "",
        row(STAGE_COLUMNS), row(["---"] * len(STAGE_COLUMNS)),
    ]
    for phase in data["stages"]:
        lines.append(row([
            STAGES[phase["stage"]], str(phase["run"]), phase["status"],
            phase["started_at"], phase["finished_at"] or EMPTY, escape(phase["note"]),
        ]))
    for phase in data["stages"]:
        if "discussions" in phase:
            lines.extend(["", f"## 1拷问确认记录（第 {phase['run']} 次）", "", "```json",
                          json.dumps(phase["discussions"], ensure_ascii=False, indent=2), "```"])
        if "report" in phase:
            lines.extend(["", f"## 1拷问结果报告（第 {phase['run']} 次）", "", "```json",
                          json.dumps(phase["report"], ensure_ascii=False, indent=2), "```"])
        if "closure" in phase:
            lines.extend(["", f"## 3实现归档输入（第 {phase['run']} 次）", ""])
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
    if execution_scope(data["execution_scope"]) != data["execution_scope"]:
        raise ProgressError("执行范围格式无效")
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
            raise ProgressError("阶段必须是 1、2 或 3")
        if "closure" in phase:
            if stage != "3" or closure_input(phase["closure"]) != phase["closure"]:
                raise ProgressError("归档输入只能记录在 3实现，且须符合固定格式")
            if len(phase["steps"]) < 3:
                raise ProgressError("3实现须完成前三个大步骤后才能保存归档输入")
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
        validate_discussions(phase)
        if "report" in phase:
            report = phase["report"]
            if (stage != "1" or phase["status"] != "已完成" or not isinstance(report, dict)
                    or set(report) != {"markdown", "input_sha256"}):
                raise ProgressError("固定报告只能保存在已完成的 1拷问，且须包含 markdown、input_sha256")
            discussion_text(report["markdown"], "固定报告")
            if re.fullmatch(r"[0-9a-f]{64}", text(report["input_sha256"], "报告输入校验")) is None:
                raise ProgressError("报告输入校验无效")
        previous_end = end


def validate_discussions(phase):
    if "discussions" not in phase:
        return  # 保留旧进度的可读性；进入拷问时仍须取得确认记录。
    records = phase["discussions"]
    if phase["stage"] != "1" or not isinstance(records, list) or not records:
        raise ProgressError("讨论确认记录只能是 1拷问的非空列表")
    previous_time = timestamp(phase["started_at"])
    identifiers = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != {"id", "points", "prepared_at", "confirmation"}:
            raise ProgressError("讨论确认记录字段无效")
        identifier = text(record["id"], "确认编号")
        if str(uuid.UUID(identifier)) != identifier or identifier in identifiers:
            raise ProgressError("确认编号无效或重复")
        identifiers.add(identifier)
        if discussion_points(record["points"]) != record["points"]:
            raise ProgressError("讨论要点格式无效")
        prepared = timestamp(record["prepared_at"])
        if prepared < previous_time:
            raise ProgressError("准备时间早于阶段开始或上一版确认记录")
        previous_time = prepared
        confirmation = record["confirmation"]
        if confirmation is not None:
            if not isinstance(confirmation, dict) or set(confirmation) != {"reply", "source", "confirmed_at"}:
                raise ProgressError("用户确认须包含 reply、source、confirmed_at")
            if discussion_text(confirmation["reply"], "用户答复") != confirmation["reply"]:
                raise ProgressError("用户答复格式无效")
            text(confirmation["source"], "确认依据")
            previous_time = timestamp(confirmation["confirmed_at"])
            if previous_time < prepared:
                raise ProgressError("确认时间早于要点准备时间")
            if not phase["steps"]:
                raise ProgressError("已有确认记录但缺少第 1 步完成记录")
        if phase["finished_at"] and previous_time > timestamp(phase["finished_at"]):
            raise ProgressError("讨论确认记录晚于阶段结束")
    if len(phase["steps"]) >= 2:
        record = confirmed_discussion(phase)
        if timestamp(record["confirmation"]["confirmed_at"]) > timestamp(phase["steps"][1]["finished_at"]):
            raise ProgressError("执行拷问的完成时间早于讨论要点确认")


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
    if position >= len(lines) or lines[position] != "## 执行范围":
        raise ProgressError("进度文档缺少执行范围记录，请沿用创建本任务的旧版本收尾")
    expect("## 执行范围")
    expect("")
    expect("```json")
    start = position
    while position < len(lines) and lines[position] != "```":
        position += 1
    data["execution_scope"] = json.loads("\n".join(lines[start:position]))
    expect("```")
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
        if position + 1 < len(lines) and lines[position + 1] == f"## 1拷问确认记录（第 {phase['run']} 次）":
            expect("")
            expect(f"## 1拷问确认记录（第 {phase['run']} 次）")
            expect("")
            expect("```json")
            start = position
            while position < len(lines) and lines[position] != "```":
                position += 1
            phase["discussions"] = json.loads("\n".join(lines[start:position]))
            expect("```")
        if position + 1 < len(lines) and lines[position + 1] == f"## 1拷问结果报告（第 {phase['run']} 次）":
            expect("")
            expect(f"## 1拷问结果报告（第 {phase['run']} 次）")
            expect("")
            expect("```json")
            start = position
            while position < len(lines) and lines[position] != "```":
                position += 1
            phase["report"] = json.loads("\n".join(lines[start:position]))
            expect("```")
        if position + 1 < len(lines) and lines[position + 1] == f"## 3实现归档输入（第 {phase['run']} 次）":
            expect("")
            expect(f"## 3实现归档输入（第 {phase['run']} 次）")
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


def active_discussion(data):
    phase = data["stages"][-1]
    if phase["stage"] != "1" or phase["finished_at"] is not None:
        raise ProgressError("讨论确认命令仅适用于当前尚未结束的 1拷问")
    return phase


def confirmed_discussion(phase):
    records = phase.get("discussions", [])
    if not records or records[-1]["confirmation"] is None:
        raise ProgressError("本轮讨论要点尚未取得用户确认；先准备、展示并等待答复，再调用 confirm-discussion")
    return records[-1]


def prepare_discussion(data, points, at):
    candidate = deepcopy(data)
    phase = active_discussion(candidate)
    points = discussion_points(points)
    records = phase.setdefault("discussions", [])
    if records and records[-1]["points"] == points:
        return
    if len(phase["steps"]) >= 2:
        raise ProgressError("执行拷问已登记完成，不能改写本轮的前置确认")
    records.append({"id": str(uuid.uuid4()), "points": points, "prepared_at": at, "confirmation": None})
    validate(candidate)
    data.update(candidate)


def confirm_discussion(data, identifier, reply, source, at):
    candidate = deepcopy(data)
    phase = active_discussion(candidate)
    records = phase.get("discussions", [])
    if not records or records[-1]["id"] != identifier:
        raise ProgressError("确认编号不属于本轮最新讨论要点，请展示并确认当前版本")
    record = records[-1]
    reply, source = discussion_text(reply, "用户答复"), text(source, "确认依据")
    if record["confirmation"] is not None:
        if (record["confirmation"]["reply"], record["confirmation"]["source"]) != (reply, source):
            raise ProgressError("该版本已有不同确认答复或依据，不能覆盖原记录")
        return
    record["confirmation"] = {"reply": reply, "source": source, "confirmed_at": at}
    if not phase["steps"]:
        phase["steps"].append({"id": "1.1", "name": STEPS["1"][0],
                               "note": "用户已确认讨论要点，确认编号 " + identifier, "finished_at": at})
    validate(candidate)
    data.update(candidate)


def discussion_prompt(record, scope):
    lines = ["请确认本轮讨论要点：", ""]
    for key, label in DISCUSSION_FIELDS:
        lines.extend([f"**{label}**", *(escape(line) for line in record["points"][key].splitlines()), ""])
    lines.extend(["**执行范围（沿用任务记录）**", escape(scope_description(scope)), "",
                  "以上理解是否准确？请确认或指出需要调整的内容，确认后再开始拷问。"])
    return "\n".join(lines)


def finish_discussion(data, markdown, input_sha256, at):
    candidate = deepcopy(data)
    phase = active_discussion(candidate)
    confirmed_discussion(phase)
    if len(phase["steps"]) != 2:
        raise ProgressError("生成完成报告前须完成确认讨论要点和执行拷问两步")
    phase["report"] = {"markdown": markdown, "input_sha256": input_sha256}
    phase["steps"].append({"id": "1.3", "name": STEPS["1"][2],
                           "note": "成果验收通过，固定报告已生成并保存", "finished_at": at})
    phase["status"], phase["finished_at"] = "已完成", at
    validate(candidate)
    data.update(candidate)


def enter_stage(data, stage, at, new_run=False, closure=None):
    if int(stage) > int(data["execution_scope"]["end_stage"]):
        raise ProgressError("目标阶段超出用户限定的执行范围，须先核对新指令并用 set-scope 更新")
    if closure is not None:
        if stage != "3":
            raise ProgressError("归档输入只适用于 3实现")
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
    if phase["stage"] != "3" or len(phase["steps"]) != 3 or phase["finished_at"] is not None:
        raise ProgressError("须在 3实现完成第 3 步后、第 4 步完成前首次保存归档输入，且阶段尚未结束")
    phase["closure"] = closure


def create_document(path, project, summary, stage, at, closure=None, scope=None):
    summary = text(summary, "任务简介")
    if not project.is_dir():
        raise ProgressError(f"目标项目目录不存在：{project}")
    with lock_document(path):
        if path.exists() or path.is_symlink():
            data = read_document(path)
            if (Path(data["project"]).resolve() != project.resolve() or data["summary"] != summary
                    or data["stages"][-1]["stage"] != stage):
                raise ProgressError("已有进度文档的任务或阶段不一致，原文件已保留")
            if scope is not None and execution_scope(scope) != data["execution_scope"]:
                raise ProgressError("已有任务的执行范围不同，须按用户新指令用 set-scope 更新")
            original = render(data)
            enter_stage(data, stage, at, closure=closure)
            if render(data) != original:
                write_document(path, data)
            return data
        data = {"task_id": str(uuid.uuid4()), "summary": summary, "project": str(project.resolve()),
                "execution_scope": execution_scope(scope) if scope is not None else make_execution_scope(),
                "stages": []}
        enter_stage(data, stage, at, closure=closure)
        write_document(path, data, create=True)
        return data


def complete_steps(data, stage, entries):
    if not isinstance(entries, list) or not entries:
        raise ProgressError("步骤记录必须是非空 JSON 列表")
    candidate = deepcopy(data)
    phase = latest_stage(candidate, stage)
    default_time = event_time()
    previous_number = None
    for entry in entries:
        if (not isinstance(entry, dict) or not {"step", "note"} <= entry.keys()
                or entry.keys() - {"step", "note", "at"}):
            raise ProgressError("每项步骤记录须包含 step、note，仅可额外提供 at")
        number = entry["step"]
        if type(number) is not int or not 1 <= number <= len(STEPS[stage]):
            raise ProgressError("step 必须是本阶段已有大步骤的整数序号")
        if stage == "1":
            if number == 1:
                raise ProgressError("确认讨论要点须使用 confirm-discussion，不能通过 complete 登记")
            if number == 3:
                raise ProgressError("1拷问须通过 render-result.py 生成固定报告并收尾，不能通过 complete 结束")
            confirmed_discussion(phase)
        if previous_number is not None and number != previous_number + 1:
            raise ProgressError("列表中的步骤须连续递增，不能重复、乱序或跳步")
        previous_number = number
        note = text(entry["note"], "简要结果")
        at = event_time(text(entry["at"], "时间")) if "at" in entry else default_time
        if number <= len(phase["steps"]):
            if phase["steps"][number - 1]["note"] != note:
                raise ProgressError("该大步骤已记录不同结果，不能覆盖原记录")
            continue
        if phase["finished_at"] is not None:
            raise ProgressError("该阶段已结束；重新执行用 enter --new-run 保留旧记录")
        if number != len(phase["steps"]) + 1:
            raise ProgressError("请按实际顺序记录大步骤，不能跳过未完成的步骤")
        phase["steps"].append({"id": f"{stage}.{number}", "name": STEPS[stage][number - 1],
                               "note": note, "finished_at": at})
        if number == len(STEPS[stage]):
            phase["status"], phase["finished_at"] = "已完成", at
    validate(candidate)
    data.update(candidate)


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
            "next_step": next_step(phase), "execution_scope": data["execution_scope"]}


def json_value(value):
    try:
        return json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError("参数必须是有效 JSON") from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command, description in (
        ("create", "首次创建文档并进入阶段"), ("enter", "接续或进入下一阶段"),
        ("complete", "按列表登记一个或多个已完成步骤；最后一步自动结束阶段"),
        ("stop", "明确取消或切换流程时终止阶段，保留已完成步骤"),
        ("show", "恢复任务或生成报告时读取完整记录及阶段耗时"),
        ("set-scope", "按用户新指令更新任务执行范围，保留阶段记录"),
        ("cleanup", "整个任务已结束且报告已保留后删除临时进度文档"),
        ("prepare-discussion", "保存本轮讨论要点并生成待用户确认的内容"),
        ("confirm-discussion", "登记当前版本的实际用户答复及依据，完成确认讨论要点"),
        ("check-discussion", "进入拷问前核对本轮确认并返回已确认要点，只读"),
    ):
        subparser = commands.add_parser(command, help=description, allow_abbrev=command != "complete")
        subparser.add_argument("--project", required=True, help="原目标项目的绝对路径")
        if command == "create":
            subparser.add_argument("--output", required=True, help="进度文档绝对路径，父目录须已存在")
            subparser.add_argument("--summary", required=True, help="原始任务的问题和目标，使用简短单行文本")
        if command in {"create", "set-scope"}:
            subparser.add_argument("--end-stage", choices=tuple(STAGES), required=command == "set-scope",
                                   help="任务执行终点；新任务默认 3，提前结束须提供用户原话和来源")
            subparser.add_argument("--scope-reply", required=command == "set-scope", help="用户明确限定或修改执行范围的原话")
            subparser.add_argument("--scope-source", required=command == "set-scope", help="该用户指令的可定位来源")
        if command != "create":
            subparser.add_argument("--file", required=True, help="同一进度文档的绝对路径")
            subparser.add_argument("--task-id", required=True, help="创建时返回的固定任务标识")
        if command in {"create", "enter", "complete", "stop"}:
            subparser.add_argument("--stage", required=True, choices=tuple(STAGES))
        if command in {"create", "enter", "stop", "prepare-discussion", "confirm-discussion"}:
            subparser.add_argument("--at", help="实际事件的 ISO 8601 时间，须有时区；默认取当前时间")
        if command == "enter":
            subparser.add_argument("--new-run", action="store_true", help="明确重新执行已结束阶段时追加一轮")
        if command in {"create", "enter"}:
            subparser.add_argument("--closure", type=json_value,
                                   help="3实现的归档输入 JSON；第 3 步完成后、第 4 步完成前首次保存到同一文件")
        if command == "complete":
            subparser.add_argument("--steps", required=True, type=json_value,
                                   help="非空 JSON 列表，每项含整数 step、单行 note 及可选的含时区 at")
        if command == "stop":
            subparser.add_argument("--note", required=True, help="一句结果或终止原因")
        if command == "cleanup":
            subparser.add_argument("--task-finished", action="store_true", required=True)
        if command == "prepare-discussion":
            subparser.add_argument("--points", required=True, type=json_value, help="五项讨论要点的 JSON 对象")
        if command == "confirm-discussion":
            subparser.add_argument("--confirmation-id", required=True, help="prepare-discussion 返回的当前确认编号")
            subparser.add_argument("--reply", required=True, help="用户针对本版要点的实际确认答复")
            subparser.add_argument("--source", required=True, help="该用户答复的消息引用或可定位的对话依据")
    args = parser.parse_args()
    try:
        project = absolute_path(args.project, "目标项目")
        path = absolute_path(args.output if args.command == "create" else args.file, "进度文档")
        if args.command == "create":
            scope = None
            if args.end_stage is not None or args.scope_reply is not None or args.scope_source is not None:
                scope = make_execution_scope(args.end_stage or "3", args.scope_reply, args.scope_source)
            data = create_document(path, project, args.summary, args.stage, event_time(args.at), args.closure, scope)
        else:
            with lock_document(path):
                data = read_document(path)
                match_task(data, args.task_id, project)
                original = render(data)
                if args.command == "enter":
                    enter_stage(data, args.stage, event_time(args.at), args.new_run, args.closure)
                elif args.command == "set-scope":
                    set_scope(data, args.end_stage, args.scope_reply, args.scope_source)
                elif args.command == "complete":
                    complete_steps(data, args.stage, args.steps)
                elif args.command == "prepare-discussion":
                    prepare_discussion(data, args.points, event_time(args.at))
                elif args.command == "confirm-discussion":
                    confirm_discussion(data, args.confirmation_id, args.reply, args.source, event_time(args.at))
                elif args.command == "check-discussion":
                    confirmed_discussion(active_discussion(data))
                elif args.command == "stop":
                    stop_stage(data, args.stage, args.note, event_time(args.at))
                elif args.command == "cleanup":
                    if any(phase["finished_at"] is None for phase in data["stages"]):
                        raise ProgressError("还有未结束的阶段，必须保留进度文档")
                    path.unlink()
                if args.command not in {"show", "cleanup", "check-discussion"}:
                    validate(data)
                    if render(data) != original:
                        write_document(path, data)
        result = {"file": str(path), **summarize(data)} if args.command == "show" else compact_result(path, data)
        if args.command == "cleanup":
            result["removed"] = True
        if args.command in {"prepare-discussion", "confirm-discussion", "check-discussion"}:
            record = data["stages"][-1]["discussions"][-1]
            result["confirmation_id"] = record["id"]
            if args.command == "prepare-discussion":
                result["confirmed"] = record["confirmation"] is not None
                result["confirmation_prompt"] = discussion_prompt(record, data["execution_scope"])
            elif args.command == "check-discussion":
                result.update(points=record["points"], confirmation=record["confirmation"])
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ProgressError, OSError, UnicodeError) as error:
        parser.exit(1, f"无法处理任务进度：{error}\n")


if __name__ == "__main__":
    main()
