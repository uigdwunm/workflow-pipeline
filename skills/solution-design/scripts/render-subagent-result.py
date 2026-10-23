#!/usr/bin/env python3
# coding: utf-8
"""根据固定参数生成子 Agent 发给主 Agent 的任务完成消息。"""

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


def parse_tickets(value):
    try:
        tickets = json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError("Tickets 必须是有效的 JSON 数组") from error
    if not isinstance(tickets, list):
        raise argparse.ArgumentTypeError("Tickets 必须是数组，未拆分时传入 []")
    for index, ticket in enumerate(tickets, start=1):
        if not isinstance(ticket, dict) or set(ticket) != {"name", "location"}:
            raise argparse.ArgumentTypeError(f"第 {index} 项 Ticket 必须包含且仅包含 name 和 location")
        try:
            name = parse_text(ticket["name"])
            if any(ord(char) < 32 for char in name):
                raise argparse.ArgumentTypeError("Ticket 名称必须是单行文本")
            ticket["name"] = name
            ticket["location"] = parse_location(ticket["location"])
        except argparse.ArgumentTypeError as error:
            raise argparse.ArgumentTypeError(f"第 {index} 项 Ticket 无效：{error}") from error
    return tickets


def escape_text(value):
    return "".join("\\" + char if char in string.punctuation else char for char in value)


def format_text(value):
    return "\n".join(escape_text(line.strip()) for line in value.splitlines())


def artifact_link(artifact):
    location = artifact["location"]
    safe = "/:~@-._" if Path(location).is_absolute() else "/:?#@!$&'*,;=+%-._~"
    return f"[{escape_text(artifact['name'])}](<{quote(location, safe=safe)}>)"


def render_subagent_result(summary, spec_location, tickets, no_tickets_reason):
    spec_link = artifact_link({"name": "Spec", "location": spec_location})
    if tickets:
        tickets_result = "\n".join(f"- {artifact_link(ticket)}" for ticket in tickets)
    else:
        tickets_result = f"未拆分 Tickets。原因：{format_text(no_tickets_reason)}"
    return f"""**状态：子 Agent 任务已完成**

请验收以下成果并接续主流程。

**方案摘要**

{format_text(summary)}

**Spec**

{spec_link}

**Tickets**

{tickets_result}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", required=True, type=parse_text, help="本次方案摘要")
    parser.add_argument(
        "--spec-location", required=True, type=parse_location,
        help="Spec 的绝对文件路径或 HTTP(S) 链接",
    )
    parser.add_argument(
        "--tickets", required=True, type=parse_tickets,
        help='Tickets JSON 数组，例如 [{"name":"Ticket 01","location":"/absolute/path/01.md"}]；未拆分时传入 []',
    )
    parser.add_argument(
        "--no-tickets-reason", type=parse_text,
        help="主 Agent 已确认的不拆分原因；Tickets 为空时必填，有 Tickets 时不传",
    )
    args = parser.parse_args()
    if not args.tickets and args.no_tickets_reason is None:
        parser.error("未拆分 Tickets 时必须传入 --no-tickets-reason")
    if args.tickets and args.no_tickets_reason is not None:
        parser.error("有 Tickets 时不能传入 --no-tickets-reason")
    print(render_subagent_result(
        args.summary, args.spec_location, args.tickets, args.no_tickets_reason,
    ), end="")


if __name__ == "__main__":
    main()
