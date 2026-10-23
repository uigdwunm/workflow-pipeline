#!/usr/bin/env python3
# coding: utf-8
"""根据固定任务参数生成子 Agent 首轮提示词。"""

import argparse
import json
from pathlib import Path
from string import Template
from urllib.parse import urlsplit

PROMPT_TEMPLATE = r"""你负责根据本次已确认的需求制定技术方案和实施计划，生成 Spec 和必要的 Tickets。使用下文提供实际入口的 `to-spec` 和 `to-tickets` 执行，任务中的确认和决策由主 Agent 处理。

# 任务信息

**工作目录**
${worktree}

**方案目标**
${goal}

**任务上下文：已确认内容与执行信息**
${context}

**参考资料**
${sources}

**补充说明**
${notes}

# 执行流程

本次使用的 `to-spec` 和 `to-tickets` 实际入口由主 Agent 通过完整 Skills 登记查询核实，列在任务上下文的“执行信息”中。调用相应 Skill 前，从该入口读取完整 `SKILL.md`，按其原流程执行。入口无法读取、未提供或需要重新定位时，按本提示词“意外中断”的要求向主 Agent 报告，等待其针对原目标项目核实并传递入口。

执行本地文本写入前，读取 `${scripts_dir}/../SKILL.md` 中的“本地文本写入”章节，遵守其中要求。

## 1. 生成并确认 Spec

生成 Spec 前，读取 `${scripts_dir}/../SKILL.md` 中的“已确认设计要点”规则，根据任务信息和参考资料整理要点及需要澄清的内容。

在指定 Worktree 中，根据任务信息使用已核实入口的 `to-spec`，将设计要点及其记录、核对和变更确认要求作为输入，按其原流程生成 Spec。

读取 `${scripts_dir}/../SKILL.md` 中的“测试范围的承接与细化”，将该规则及任务信息中的测试范围结论、确认依据和未决事项一并传给 `to-spec`，复用适用的既有确认。仅将按该规则仍需补充或变更确认的事项交给主 Agent 处理。

以任务信息中用户已确认的目标、范围、约束和关键设计决策为本次方案的设计基线，遵循以下要求：

- 制定满足当前已确认需求的最小完整方案。新增复杂度应有明确的需求依据，避免为尚未确认的未来需求增加额外抽象、通用框架或扩展机制。
- 可以自行决定内部实现方式；但用户尚未确定、也未授权自行决定的模块职责、输入输出合同、校验和失败行为，不属于可自行补充的实现细节。实现依赖这些决定时，先说明具体缺口，不能以“配套调整”为由自行补全。
- 需要改变已确认内容、增加额外设计或机制，或者补充细节需要改动既有重要设计时，若用户尚未明确授权对应变化，暂停后续流程，按照本提示词“意外中断”的要求生成并发送中断报告。报告中说明触发原因、拟修改内容及影响，并明确该变更需要用户确认。等待主 Agent 反馈用户决定后再继续。

`to-spec` 执行过程中需要确认、且不涉及上述设计变更的事项，向主 Agent 说明任务尚未完成、需要确认的具体内容，收到明确回复后继续。

Spec 草案完成后、发布前，读取 `${scripts_dir}/../SKILL.md` 中的“验收标准”，检查其中针对 Spec 的要求。对照本次任务信息和参考资料，检查“已确认设计要点”清单是否完整、准确，并逐条检查方案是否遵守这些要点。检查不通过时，先修订。需要修改、撤销或偏离某条要点时，向主 Agent 说明对应编号、原约定、拟议变化、原因和影响，并暂停相关工作。收到主 Agent 反馈的用户同意变更的明确决定后，先更新 Spec 和已有的相关 Tickets，再继续。检查通过且所需确认均已完成后，按已读取的 `to-spec` 流程发布。

Spec 生成后，根据实际产物准备以下参数，使用 Python 3 调用：
`${scripts_dir}/render-spec-confirmation.py`

| 参数 | 生成要求 |
| --- | --- |
| `--summary` | 根据已生成的 Spec 概括解决的问题、采用的方案和重要范围边界，帮助主 Agent 快速了解当前方案。使用非空文本。 |
| `--artifacts` | 列出本次待确认的 Spec 及直接相关的方案产物。使用非空 JSON 数组，每项仅包含 `name` 和 `location`：`name` 是清楚标识产物的名称；`location` 是实际的绝对文件路径或 HTTP(S) 链接。两者均为非空单行文本。 |
| `--confirmation-items` | 从 Spec 中提取需要主 Agent 特别关注的设计取舍、范围边界或确认事项，明确说明每项要确认什么。没有额外事项时填“无额外确认事项”。使用非空文本，可分行描述。 |
| `--recommendation` | 对确认事项给出建议及理由。同时根据 Spec 的工作量、可分别交付和验收的部分、实施依赖，提出是否拆分 Tickets 的建议及依据，最终由主 Agent 决定。使用非空文本。 |

将脚本标准输出中的 Markdown 原样通过当前宿主的子 Agent 通信能力发送给主 Agent，保持任务可继续，等待其回复。

- 要求修改时，修订 Spec 后重新调用该脚本提交确认。
- Spec 确认通过且需要拆分 Tickets 时，进入下一步。
- Spec 确认通过且无需拆分时，记录主 Agent 确认的原因，进入完成处理。主 Agent 未说明原因时，请其补充。

## 2. 生成并确认 Tickets

以主 Agent 已确认的 Spec 为依据，使用已核实入口的 `to-tickets`。

遵循 `to-tickets` 的原有切分规则及其中的大范围重构例外。在这些规则允许的范围内，优先采用较细粒度。

读取 `${scripts_dir}/../SKILL.md` 中的“Tickets 依赖与并行机会”，将其作为拆分要求传给 `to-tickets`，生成并检查草案时执行。

每个 Ticket 记录适用的设计要点，或明确引用其编号和 Spec 位置，供实现时读取和核对。

检查 Tickets 草案时，核对每个 Ticket 的交付结果和验收条件与 Spec 要求的对应关系，确认其遵守相关设计要点，以及整组 Tickets 对本次必要交付内容的覆盖情况。

拆分时发现影响方案成立或 Tickets 切分的必要决策缺口，暂停 Tickets 拆分，向主 Agent 说明缺口及其影响，等待处理结论。涉及设计变更时，等待主 Agent 反馈用户决定。按处理结论和本提示词“生成并确认 Spec”中的规则修订原 Spec，更新原产物，并重新提交主 Agent 确认；确认通过后，重新读取更新后的 Spec，再继续调整相关 Tickets。已确认框架内的普通实现细节，按前述设计边界自行补充。

根据 Spec 提出必要的阻塞关系，以及合并或进一步拆分的建议，交主 Agent 判断，确认后再发布。

在原流程的草案确认环节，准备可读取的草案产物。草案只有对话内容时，先保存到指定 Worktree 中，供主 Agent 查看。

使用 Python 3 调用：
`${scripts_dir}/render-tickets-confirmation.py`

| 参数 | 生成要求 |
| --- | --- |
| `--spec-location` | 填入本次拆分所依据、已经由主 Agent 确认的 Spec 的实际绝对文件路径或 HTTP(S) 链接。 |
| `--summary` | 根据草案概括拆出了哪些交付项、各自交付什么，以及它们如何覆盖 Spec。使用非空文本，避免仅罗列标题。 |
| `--artifacts` | 列出待确认的 Tickets 草案文件或草案文档。使用非空 JSON 数组，每项仅包含 `name` 和 `location`，分别填写产物名称及实际绝对文件路径或 HTTP(S) 链接。两者均为非空单行文本。 |
| `--recommendation` | 说明当前粒度为什么合适、阻塞关系为什么必要，以及是否建议合并或进一步拆分。依据具体草案给出判断和理由。使用非空文本。 |
| `--confirmation-items` | 列出需要主 Agent 特别关注的草案项及具体判断事项。没有额外事项时填“无额外确认事项”。使用非空文本，可分行描述。 |

将脚本标准输出中的 Markdown 原样发送给主 Agent，暂停发布，等待其回复。

要求调整时，修改草案后重新调用该脚本提交确认。明确批准发布后，按 `to-tickets` 的原流程发布，记录实际产物位置，进入完成处理。

## 3. 返回完成结果

满足以下任一条件时，返回完成结果：

- Spec 已由主 Agent 确认，主 Agent 已决定无需拆分 Tickets，且已记录其确认的不拆分原因。
- Spec 已由主 Agent 确认，需要拆分的 Tickets 已由主 Agent 确认并发布完成。

使用 Python 3 调用：
`${scripts_dir}/render-subagent-result.py`

| 参数 | 生成要求 |
| --- | --- |
| `--summary` | 根据最终实际交付的方案概括解决的问题、采用的方案和交付范围。使用非空文本。 |
| `--spec-location` | 填入最终已确认 Spec 的实际绝对文件路径或 HTTP(S) 链接。 |
| `--tickets` | 列出本次实际发布完成的 Tickets。使用 JSON 数组，每项仅包含 `name` 和 `location`，分别填写 Ticket 名称及实际绝对文件路径或 HTTP(S) 链接，两者均为非空单行文本。未拆分时传入空数组。 |
| `--no-tickets-reason` | `--tickets` 为空数组时必填，使用主 Agent 已确认的不拆分理由；有 Tickets 时不传此参数。理由必须是非空文本。 |

将脚本标准输出中的 Markdown 原样作为最终任务结果返回，结束本次子 Agent 任务。

# 意外中断

发生执行异常时，先读取 `${scripts_dir}/../SKILL.md` 中的“意外中断”，按其中规则核实状态并判断能否自行恢复。授权范围内可恢复的常规问题由你修复、验证并继续；用户另有明确的停工或报告要求时，按用户要求执行。

在现有条件下无法恢复或需要主 Agent 处理决定时，暂停受影响工作，使用 Python 3 调用：
`${scripts_dir}/render-subagent-interruption.py`

| 参数 | 生成要求 |
| --- | --- |
| `--step` | 写明中断发生在哪个阶段、正在执行什么具体动作，使主 Agent 能定位执行停在何处。使用非空文本。 |
| `--reason` | 根据实际执行信息说明发生了什么、导致什么动作无法继续，并保留可定位的错误信息。已确认的原因与尚不确定的推测应区分说明。使用非空文本。 |
| `--completed-work` | 列出实际完成的工作，区分已生成、已确认和已发布的状态。尚无完成内容时明确说明。使用非空文本。 |
| `--artifacts` | 列出当前实际存在的产物，包括已生成的阶段产物。使用 JSON 数组，每项仅包含 `name` 和 `location`，分别填写产物名称及实际绝对文件路径或 HTTP(S) 链接，两者均为非空单行文本。尚无产物时传入空数组。 |
| `--remaining-work` | 根据任务目标和当前进展，列出尚未完成的具体事项，使主 Agent 能判断恢复后还需要做什么。使用非空文本。 |
| `--recommendation` | 根据中断信息提出可行的处理建议，说明继续执行需要补充的信息或恢复的条件。使用非空文本。 |

将脚本标准输出中的 Markdown 原样发送给主 Agent，保持受影响工作暂停，等待其处理结论；可以继续不依赖该决定的工作。

收到继续执行的处理结论后，从已核实的位置接续，仅继续尚未完成的工作。创建或发布仍有结果不明的操作时，先向主 Agent 说明已知状态及缺失的信息，保持相关操作暂停，核实前暂停重复执行。
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


def parse_location(value):
    location = parse_text(value)
    if any(ord(char) < 32 for char in location):
        raise argparse.ArgumentTypeError("资料位置必须是单行文本")
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


def parse_worktree(value):
    worktree = parse_text(value)
    if any(ord(char) < 32 for char in worktree):
        raise argparse.ArgumentTypeError("Worktree 路径必须是单行文本")
    path = Path(worktree)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("Worktree 必须使用绝对路径")
    if not path.is_dir():
        raise argparse.ArgumentTypeError("Worktree 路径必须指向已存在的目录")
    return worktree


def parse_sources(value):
    try:
        sources = json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError("参考资料必须是有效的 JSON 数组") from error
    if not isinstance(sources, list):
        raise argparse.ArgumentTypeError("参考资料必须是数组，无额外资料时传入 []")
    for index, source in enumerate(sources, start=1):
        if not isinstance(source, dict) or set(source) != {"name", "location"}:
            raise argparse.ArgumentTypeError(f"第 {index} 项资料必须包含且仅包含 name 和 location")
        try:
            name = parse_text(source["name"])
            if any(ord(char) < 32 for char in name):
                raise argparse.ArgumentTypeError("资料名称必须是单行文本")
            source["name"] = name
            source["location"] = parse_location(source["location"])
        except argparse.ArgumentTypeError as error:
            raise argparse.ArgumentTypeError(f"第 {index} 项资料无效：{error}") from error
    return sources


def render_subagent_prompt(scripts_dir, worktree, goal, context, sources, notes):
    source_list = "\n".join(f"- {source['name']}：{source['location']}" for source in sources)
    return Template(PROMPT_TEMPLATE).substitute(
        worktree=worktree,
        goal=goal,
        context=context,
        sources=source_list or "未指定额外参考资料。",
        notes=notes or "无补充说明。",
        scripts_dir=str(scripts_dir),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--worktree", required=True, type=parse_worktree,
        help="主 Agent 已确定的 Worktree 绝对路径，目录必须已存在",
    )
    parser.add_argument(
        "--goal", required=True, type=parse_text,
        help="用户希望解决的问题和实现的结果，根据已确认的任务目标整理",
    )
    parser.add_argument(
        "--context", required=True, type=parse_text,
        help="已确认的背景、范围、约束、决策、设计要点和项目约定，另列执行信息传递已核实的 Skill 入口；完整保留子 Agent 执行所需信息，可用多行文本",
    )
    parser.add_argument(
        "--sources", required=True, type=parse_sources,
        help="参考资料 JSON 数组，每项仅含 name 和 location；名称为非空单行文本，位置为绝对文件路径或 HTTP(S) 链接；无额外资料时传入 []",
    )
    parser.add_argument(
        "--notes", required=True, type=parse_optional_text,
        help="需要告知子 Agent、但不适合放入目标、背景或资料列表的补充说明；没有时传入空文本",
    )
    args = parser.parse_args()
    scripts_dir = Path(__file__).resolve().parent
    try:
        prompt = render_subagent_prompt(
            scripts_dir, args.worktree, args.goal, args.context, args.sources, args.notes,
        )
    except (KeyError, ValueError) as error:
        parser.error(f"首轮提示词正文中的占位符无效：{error}")
    print(prompt, end="")


if __name__ == "__main__":
    main()
