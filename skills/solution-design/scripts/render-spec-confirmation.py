#!/usr/bin/env python3
# coding: utf-8
"""根据固定参数生成子 Agent 发给主 Agent 的 Spec 确认消息。"""

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
        raise argparse.ArgumentTypeError("Spec 产物必须是非空数组")
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


def render_spec_confirmation(summary, artifacts, confirmation_items, recommendation):
    artifact_list = "\n".join(f"- {artifact_link(artifact)}" for artifact in artifacts)
    return f"""**状态：整体任务尚未完成，等待 Spec 确认**

请检查以下 Spec 是否符合已明确的目标、范围、约束和决策，提出修改意见或确认通过，并决定是否需要拆分 Tickets。

判断是否拆分 Tickets 时，根据已生成的 Spec，结合工作量、是否包含需要分别交付和验收的部分，以及实施依赖关系进行判断。

通常需要拆分 Tickets。只有非常简单、能够通过一次短小且连贯的实现完成的小任务，才可以省略。

**内容摘要**

{format_text(summary)}

**Spec 产物**

{artifact_list}

**需要关注的确认事项**

{format_text(confirmation_items)}

**我的建议及理由**

{format_text(recommendation)}

请通过当前环境的通信工具回复本子 Agent：

- 需要修改时，说明具体修改要求。我会修订后重新提交确认。
- 确认通过时，明确说明 Spec 已确认以及是否需要拆分 Tickets；无需拆分时说明原因，并通知我继续。

收到明确确认前，我暂停后续执行。
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", required=True, type=parse_text, help="Spec 内容摘要")
    parser.add_argument(
        "--artifacts", required=True, type=parse_artifacts,
        help='非空产物 JSON 数组，例如 [{"name":"Spec","location":"/absolute/path/spec.md"}]',
    )
    parser.add_argument(
        "--confirmation-items", required=True, type=parse_text,
        help="需要主 Agent 关注的确认事项；没有额外事项时明确说明，可用多行文本",
    )
    parser.add_argument(
        "--recommendation", required=True, type=parse_text,
        help="建议及理由，可包含是否拆分 Tickets 的建议，最终由主 Agent 判断",
    )
    args = parser.parse_args()
    print(render_spec_confirmation(
        args.summary, args.artifacts, args.confirmation_items, args.recommendation,
    ), end="")


if __name__ == "__main__":
    main()
