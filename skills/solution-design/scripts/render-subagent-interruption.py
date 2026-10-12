#!/usr/bin/env python3
# coding: utf-8
"""根据固定参数生成子 Agent 发给主 Agent 的意外中断消息。"""

import argparse
import json
import string
from pathlib import Path
from urllib.parse import quote, urlsplit


def parse_text(value):
    if not isinstance(value, str) or not value.strip():
        raise argparse.ArgumentTypeError("必须是非空文本")
    if any((ord(char) < 32 and char not in "\n\r\t") or ord(char) == 127 for char in value):
        raise argparse.ArgumentTypeError("文本不能包含控制字符")
    return value.strip()


def parse_location(value):
    location = parse_text(value)
    if any(ord(char) < 32 for char in location):
        raise argparse.ArgumentTypeError("产物位置必须是单行文本")
    if Path(location).is_absolute():
        return location
    try:
        url = urlsplit(location)
        hostname = url.hostname
        url.port
    except ValueError as error:
        raise argparse.ArgumentTypeError("产物链接无效") from error
    if url.scheme not in ("http", "https") or not hostname or any(char.isspace() for char in hostname):
        raise argparse.ArgumentTypeError("产物位置必须是绝对文件路径或 HTTP(S) 链接")
    return location


def parse_artifacts(value):
    try:
        artifacts = json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError("产物必须是有效的 JSON 数组") from error
    if not isinstance(artifacts, list):
        raise argparse.ArgumentTypeError("产物必须是数组，尚无产物时传入 []")
    for index, artifact in enumerate(artifacts, start=1):
        if not isinstance(artifact, dict) or set(artifact) != {"name", "location"}:
            raise argparse.ArgumentTypeError(f"第 {index} 项产物必须包含且仅包含 name 和 location")
        try:
            name = parse_text(artifact["name"])
            if any(ord(char) < 32 for char in name):
                raise argparse.ArgumentTypeError("产物名称必须是单行文本")
            artifact["name"] = name
            artifact["location"] = parse_location(artifact["location"])
        except argparse.ArgumentTypeError as error:
            raise argparse.ArgumentTypeError(f"第 {index} 项产物无效：{error}") from error
    return artifacts


def escape_text(value):
    return "".join("\\" + char if char in string.punctuation else char for char in value)


def format_text(value):
    return "\n".join(escape_text(line.strip()) for line in value.splitlines())


def artifact_link(artifact):
    location = artifact["location"]
    safe = "/:~@-._" if Path(location).is_absolute() else "/:?#@!$&'*,;=+%-._~"
    return f"[{escape_text(artifact['name'])}](<{quote(location, safe=safe)}>)"


def render_subagent_interruption(step, reason, completed_work, artifacts,
                                 remaining_work, recommendation):
    if artifacts:
        artifact_list = "\n".join(f"- {artifact_link(artifact)}" for artifact in artifacts)
    else:
        artifact_list = "尚未生成产物。"
    return f"""**状态：整体任务尚未完成，执行意外中断**

后续执行已暂停。本次报告需要你了解中断情况，决定本子 Agent 接下来如何处理，并将处理结论通知我。

**中断步骤**

{format_text(step)}

**中断原因**

{format_text(reason)}

**已完成内容**

{format_text(completed_work)}

**已有产物**

{artifact_list}

**未完成事项**

{format_text(remaining_work)}

**处理建议**

{format_text(recommendation)}

收到你的处理结论前，后续执行保持暂停。
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step", required=True, type=parse_text, help="发生中断的具体步骤")
    parser.add_argument(
        "--reason", required=True, type=parse_text,
        help="中断原因，无法确定的部分应说明不确定性",
    )
    parser.add_argument(
        "--completed-work", required=True, type=parse_text,
        help="已完成内容，尚无完成内容时明确说明",
    )
    parser.add_argument(
        "--artifacts", required=True, type=parse_artifacts,
        help='已有产物 JSON 数组，例如 [{"name":"Spec","location":"/absolute/path/spec.md"}]；尚无产物时传入 []',
    )
    parser.add_argument("--remaining-work", required=True, type=parse_text, help="尚未完成的事项")
    parser.add_argument("--recommendation", required=True, type=parse_text, help="针对中断情况的处理建议")
    args = parser.parse_args()
    print(render_subagent_interruption(
        args.step, args.reason, args.completed_work, args.artifacts,
        args.remaining_work, args.recommendation,
    ), end="")


if __name__ == "__main__":
    main()
