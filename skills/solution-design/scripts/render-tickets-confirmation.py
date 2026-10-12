#!/usr/bin/env python3
# coding: utf-8
"""根据固定参数生成子 Agent 发给主 Agent 的 Tickets 草案确认消息。"""

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
    if not isinstance(artifacts, list) or not artifacts:
        raise argparse.ArgumentTypeError("Tickets 草案产物必须是非空数组")
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


def render_tickets_confirmation(spec_location, summary, artifacts, recommendation,
                                confirmation_items):
    spec_link = artifact_link({"name": "Spec", "location": spec_location})
    artifact_list = "\n".join(f"- {artifact_link(artifact)}" for artifact in artifacts)
    return f"""**状态：整体任务尚未完成，等待 Tickets 草案确认**

已根据确认后的 Spec 生成 Tickets 草案，尚未发布。

本次报告需要你判断草案的粒度、阻塞关系，以及是否需要合并或进一步拆分，提出修改意见或批准发布。

**依据的 Spec**

{spec_link}

**拆分摘要**

{format_text(summary)}

**Tickets 草案**

{artifact_list}

**我的判断及理由**

{format_text(recommendation)}

**需要特别关注的事项**

{format_text(confirmation_items)}

请通过当前环境的通信工具回复本子 Agent：

- 需要调整时，说明具体修改要求。我会调整后重新提交确认。
- 确认通过时，明确通知我按当前草案发布。

收到明确确认前，我暂停发布。发布完成后，我会返回任务完成报告。
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--spec-location", required=True, type=parse_location,
        help="已确认 Spec 的绝对文件路径或 HTTP(S) 链接",
    )
    parser.add_argument("--summary", required=True, type=parse_text, help="Tickets 拆分摘要")
    parser.add_argument(
        "--artifacts", required=True, type=parse_artifacts,
        help='非空草案 JSON 数组，例如 [{"name":"Ticket 01","location":"/absolute/path/01.md"}]',
    )
    parser.add_argument(
        "--recommendation", required=True, type=parse_text,
        help="对粒度、阻塞关系和合并拆分的判断及理由",
    )
    parser.add_argument(
        "--confirmation-items", required=True, type=parse_text,
        help="需要主 Agent 特别关注的事项；没有额外事项时明确说明，可用多行文本",
    )
    args = parser.parse_args()
    print(render_tickets_confirmation(
        args.spec_location, args.summary, args.artifacts, args.recommendation,
        args.confirmation_items,
    ), end="")


if __name__ == "__main__":
    main()
