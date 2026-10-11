#!/usr/bin/env python3
"""根据固定任务参数生成子 Agent 首轮提示词。"""

import argparse
import json
from pathlib import Path
from string import Template
from urllib.parse import urlsplit

PROMPT_TEMPLATE = r"""你是负责本次“2方案”任务的子 Agent。使用 Matt 系列的 `to-spec` 和 `to-tickets` 生成成果，任务中的确认和决策由主 Agent 处理。

# 任务信息

**工作目录**
${worktree}

**方案目标**
${goal}

**已确认的背景、范围、约束和决策**
${context}

**参考资料**
${sources}

**补充说明**
${notes}

# 执行流程

## 1. 生成并确认 Spec

在指定 Worktree 中，根据任务信息调用名为 `to-spec` 的 Skill，按其原流程生成 Spec。

`to-spec` 执行过程中需要确认的事项，向主 Agent 说明任务尚未完成、需要确认的具体内容，收到明确回复后继续。

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

以主 Agent 已确认的 Spec 为依据，调用名为 `to-tickets` 的 Skill。

遵循其原有切分规则，优先采用较细粒度。每个 Ticket 应有明确、完整且可验证的交付结果，避免拆出无法独立验证的零碎工作。

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

发生导致任务无法继续的意外时，暂停后续执行，使用 Python 3 调用：
`${scripts_dir}/render-subagent-interruption.py`

| 参数 | 生成要求 |
| --- | --- |
| `--step` | 写明中断发生在哪个阶段、正在执行什么具体动作，使主 Agent 能定位执行停在何处。使用非空文本。 |
| `--reason` | 根据实际执行信息说明发生了什么、导致什么动作无法继续，并保留可定位的错误信息。已确认的原因与尚不确定的推测应区分说明。使用非空文本。 |
| `--completed-work` | 列出实际完成的工作，区分已生成、已确认和已发布的状态。尚无完成内容时明确说明。使用非空文本。 |
| `--artifacts` | 列出当前实际存在的产物，包括已生成的阶段产物。使用 JSON 数组，每项仅包含 `name` 和 `location`，分别填写产物名称及实际绝对文件路径或 HTTP(S) 链接，两者均为非空单行文本。尚无产物时传入空数组。 |
| `--remaining-work` | 根据任务目标和当前进展，列出尚未完成的具体事项，使主 Agent 能判断恢复后还需要做什么。使用非空文本。 |
| `--recommendation` | 根据中断信息提出可行的处理建议，说明继续执行需要补充的信息或恢复的条件。使用非空文本。 |

将脚本标准输出中的 Markdown 原样发送给主 Agent，保持后续执行暂停，等待其处理结论，再按要求继续、调整或结束任务。
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
        help="已确认的背景、范围、约束、决策和项目约定，完整保留子 Agent 执行所需信息，可用多行文本",
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
