#!/usr/bin/env python3
# coding: utf-8
"""根据固定任务参数生成新对话的完整首轮提示词。"""

import argparse
import json
from pathlib import Path
from string import Template
from urllib.parse import urlsplit


PROMPT_TEMPLATE = r"""你要帮助用户澄清下面的想法或设计。请在当前对话中直接与用户讨论。

这次讨论由另一条对话转交而来。讨论完成后，需要把结果发回下方指定的“结果接收对话”，由它继续处理后续工作。

从下方上下文提供的实际入口读取 `grill-with-docs` 的完整 SKILL.md，按其原流程执行。

执行本地文本写入前，读取 `${skill_path}` 中的“本地文本写入”章节，遵守其中要求。

# 讨论任务

**项目目录**
${project}

**讨论目标**
${goal}

**必要背景、已有决定和待讨论事项**
${context}

**参考资料**
${sources}

# 需要继续维护的讨论记录

${discussion_documents}

这里列出的文档，是用户在本次讨论转交之前已经持续维护的讨论记录，不要求使用特定文件名或格式。

列出了文档时，在讨论过程中继续更新，沿用原有结构，并区分已确认内容与待讨论内容。

没有列出文档时，本次不额外创建这类讨论记录。指定 Skill 自身要求的其他文档工作仍照常执行。

# 补充要求

${notes}

# 完成后回传结果

**结果接收对话标识**
${origin_thread_id}

**结果回传的用户授权说明及确认出处**
${return_authorization}

指定 Skill 要求的讨论、用户确认和适用的文档工作完成后，整理：

- 讨论已完成的状态。
- 最终结论，包括已确认的目标、范围、约束和关键决定。
- 实际维护或生成的文档及其位置；没有文档产物时如实说明。
- 用户确认双方理解一致的情况及可核实的依据。

核实上述授权覆盖本次回传后，通过宿主的对话通信能力，将结果发送到指定的结果接收对话，请其继续处理。

授权缺少或无法核实时，先向用户确认具体接收对象和回传内容。确认发送成功后，结束本次执行。

# 无法继续时

暂停受影响的工作，说明中断原因、已完成内容、已有文档位置、未完成事项和继续所需的信息。

已有授权覆盖中断报告的回传时，将报告发送到结果接收对话，等待处理结论。无法回传时，在当前对话向用户说明情况并保留已有结果。

收到继续或修订的处理结论后，核对实际进展，仅继续尚未完成的工作。
"""


def parse_optional_text(value):
    if not isinstance(value, str):
        raise argparse.ArgumentTypeError("必须是文本")
    if any((ord(char) < 32 and char not in "\n\r\t") or ord(char) == 127 for char in value):
        raise argparse.ArgumentTypeError("文本不能包含控制字符")
    return value.strip()


def parse_text(value):
    value = parse_optional_text(value)
    if not value:
        raise argparse.ArgumentTypeError("必须是非空文本")
    return value


def parse_single_line(value):
    if not isinstance(value, str):
        raise argparse.ArgumentTypeError("必须是文本")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise argparse.ArgumentTypeError("必须是单行文本")
    return parse_text(value)


def parse_project(value):
    project = parse_single_line(value)
    path = Path(project)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("项目目录必须使用绝对路径")
    try:
        is_directory = path.is_dir()
    except OSError as error:
        raise argparse.ArgumentTypeError(f"无法核对项目目录：{error}") from error
    if not is_directory:
        raise argparse.ArgumentTypeError("项目目录必须指向已存在的目录")
    return project


def parse_location(value):
    location = parse_single_line(value)
    if Path(location).is_absolute():
        return location
    try:
        url = urlsplit(location)
        hostname = url.hostname
        url.port
    except ValueError as error:
        raise argparse.ArgumentTypeError("资料链接无效") from error
    if url.scheme not in ("http", "https") or not hostname or any(char.isspace() for char in hostname):
        raise argparse.ArgumentTypeError("资料位置必须是绝对文件路径或 HTTP(S) 链接")
    return location


def parse_reference_list(value):
    try:
        references = json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError("资料列表必须是有效的 JSON 数组") from error
    if not isinstance(references, list):
        raise argparse.ArgumentTypeError("资料列表必须是数组，没有时传入 []")
    for index, reference in enumerate(references, start=1):
        if not isinstance(reference, dict) or set(reference) != {"name", "location"}:
            raise argparse.ArgumentTypeError(f"第 {index} 项必须包含且仅包含 name 和 location")
        try:
            reference["name"] = parse_single_line(reference["name"])
            reference["location"] = parse_location(reference["location"])
        except argparse.ArgumentTypeError as error:
            raise argparse.ArgumentTypeError(f"第 {index} 项资料无效：{error}") from error
    return references


def render_new_conversation_prompt(project, goal, context, sources,
                                   discussion_documents, origin_thread_id,
                                   return_authorization, notes):
    source_list = "\n".join(f"- {item['name']}：{item['location']}" for item in sources)
    document_list = "\n".join(
        f"- {item['name']}：{item['location']}" for item in discussion_documents
    )
    return Template(PROMPT_TEMPLATE).substitute(
        skill_path=str(Path(__file__).resolve().parents[1] / "SKILL.md"),
        project=project,
        goal=goal,
        context=context,
        sources=source_list or "未指定额外参考资料。",
        discussion_documents=document_list or "本次讨论转交前没有持续维护的讨论记录。",
        origin_thread_id=origin_thread_id,
        return_authorization=return_authorization,
        notes=notes or "无补充说明。",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project", required=True, type=parse_project,
        help="目标项目的实际绝对目录，目录必须已存在",
    )
    parser.add_argument(
        "--goal", required=True, type=parse_text,
        help="本次希望讨论和澄清的问题，以及预期结果",
    )
    parser.add_argument(
        "--context", required=True, type=parse_text,
        help="新对话所需的背景、术语说明、范围、约束、已有决定、待讨论事项和相关用户要求；包含已核实的 grill-with-docs 实际入口，可分行描述",
    )
    parser.add_argument(
        "--sources", required=True, type=parse_reference_list,
        help="参考资料 JSON 数组，每项仅含 name 和 location；名称为非空单行文本，位置为绝对文件路径或 HTTP(S) 链接；没有时传入 []",
    )
    parser.add_argument(
        "--discussion-documents", required=True, type=parse_reference_list,
        help="转交前已持续维护的讨论文档 JSON 数组，格式与参考资料相同；没有时传入 []",
    )
    parser.add_argument(
        "--origin-thread-id", required=True, type=parse_single_line,
        help="已经可靠取得并核实的原对话标识，用于接收完成或中断结果",
    )
    parser.add_argument(
        "--return-authorization", required=True, type=parse_text,
        help="结果和中断报告回传的已有用户授权范围及可核实确认出处，可分行描述",
    )
    parser.add_argument(
        "--notes", required=True, type=parse_optional_text,
        help="需要告知新对话的其他补充说明；没有时传入空文本",
    )
    args = parser.parse_args()
    try:
        prompt = render_new_conversation_prompt(
            args.project, args.goal, args.context, args.sources,
            args.discussion_documents, args.origin_thread_id,
            args.return_authorization, args.notes,
        )
    except (KeyError, ValueError) as error:
        parser.error(f"首轮提示词模板中的占位符无效：{error}")
    print(prompt, end="")


if __name__ == "__main__":
    main()
