#!/usr/bin/env python3
"""根据固定参数生成 2方案的 Markdown 结果报告。"""

import argparse
import json
import string
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit


def parse_started_at(value):
    try:
        started_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("开始时间必须是 ISO 8601 时间") from error
    if started_at.utcoffset() is None:
        raise argparse.ArgumentTypeError("开始时间必须包含时区，例如 +08:00 或 Z")
    return started_at


def parse_artifacts(value):
    try:
        artifacts = json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError("成果必须是有效的 JSON 数组") from error
    if not isinstance(artifacts, list) or not artifacts:
        raise argparse.ArgumentTypeError("成果必须是非空数组")

    for index, artifact in enumerate(artifacts, start=1):
        if not isinstance(artifact, dict) or set(artifact) != {"name", "location"}:
            raise argparse.ArgumentTypeError(
                f"第 {index} 项成果必须包含且仅包含 name 和 location"
            )
        for field in ("name", "location"):
            value = artifact[field]
            if not isinstance(value, str) or not value.strip():
                raise argparse.ArgumentTypeError(f"第 {index} 项成果的 {field} 必须是非空文本")
            if any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise argparse.ArgumentTypeError(f"第 {index} 项成果的 {field} 必须是单行文本")
            artifact[field] = value.strip()

        location = artifact["location"]
        if Path(location).is_absolute():
            continue
        try:
            url = urlsplit(location)
            hostname = url.hostname
            url.port
        except ValueError as error:
            raise argparse.ArgumentTypeError(f"第 {index} 项成果的链接无效") from error
        if url.scheme not in ("http", "https") or not hostname or any(char.isspace() for char in hostname):
            raise argparse.ArgumentTypeError(
                f"第 {index} 项成果的位置必须是绝对文件路径或 HTTP(S) 链接"
            )
    return artifacts


def escape_text(value):
    return "".join("\\" + char if char in string.punctuation else char for char in value)


def artifact_link(artifact):
    location = artifact["location"]
    safe = "/:~@-._" if Path(location).is_absolute() else "/:?#@!$&'*,;=+%-._~"
    return f"[{escape_text(artifact['name'])}](<{quote(location, safe=safe)}>)"


def format_duration(seconds):
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    units = ((days, "天"), (hours, "小时"), (minutes, "分钟"))
    return " ".join([f"{value} {unit}" for value, unit in units if value] + [f"{seconds} 秒"])


def render_result(summary, artifacts, started_at, continuous_mode,
                  implementation_skill_available, now):
    duration = int((now - started_at).total_seconds())
    lines = ["**2方案已完成**", "", "**方案摘要**  "]
    lines.extend(escape_text(line.strip()) for line in summary.strip().splitlines())
    lines.extend(["", "**成果**", ""])
    lines.extend(f"- {artifact_link(artifact)}" for artifact in artifacts)
    lines.extend([
        "",
        f"**开始时间**：{started_at.isoformat(sep=' ', timespec='seconds')}  ",
        f"**总耗时**：{format_duration(duration)}",
        "",
        "---",
        "",
    ])

    if continuous_mode:
        if implementation_skill_available:
            lines.append("当前是连续模式，下一步将进入 3实现 流程")
        else:
            lines.append(
                "当前是连续模式，但当前环境无法使用 `guided-implementation`。"
                "请先检查该 Skill 的安装、启用状态及入口，再进入 3实现 流程。"
            )
    elif implementation_skill_available:
        lines.extend([
            "是否进入 **3实现** 流程？",
            "",
            "是否开启**连续模式**？开启后，将自动执行 **3实现** 和 **4归档**。",
        ])
    else:
        lines.append(
            "当前环境无法使用 `guided-implementation`。请先检查该 Skill 的安装、启用状态及入口，再进入 3实现 流程。"
        )

    return "\n".join("> " + line if line else ">" for line in lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", required=True, help="本次方案摘要")
    parser.add_argument(
        "--artifacts", required=True, type=parse_artifacts,
        help='成果 JSON 数组，例如 [{"name":"Spec","location":"/absolute/path/spec.md"}]',
    )
    parser.add_argument(
        "--started-at", required=True, type=parse_started_at,
        help="进入 Skill 时的 ISO 8601 时间，必须包含时区",
    )
    parser.add_argument(
        "--continuous-mode", required=True, choices=("true", "false"),
        help="当前是否处于连续模式",
    )
    parser.add_argument(
        "--implementation-skill-available", required=True, choices=("true", "false"),
        help="当前可用 Skills 是否包含 guided-implementation",
    )
    args = parser.parse_args()
    if not args.summary.strip():
        parser.error("方案摘要不能为空")
    now = datetime.now(timezone.utc)
    if args.started_at > now:
        parser.error("开始时间不能晚于当前时间")
    print(render_result(
        args.summary, args.artifacts, args.started_at,
        args.continuous_mode == "true",
        args.implementation_skill_available == "true", now,
    ), end="")


if __name__ == "__main__":
    main()
