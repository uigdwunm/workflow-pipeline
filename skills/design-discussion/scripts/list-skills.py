#!/usr/bin/env python3
"""只读查询 Codex 当前项目的完整 Skills 登记信息。"""

import argparse
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import time

TIMEOUT_SECONDS = 15
MAX_OUTPUT_BYTES = 2 * 1024 * 1024


class RegistryError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details


def parse_cwd(value):
    path = Path(value)
    if not path.is_absolute() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise argparse.ArgumentTypeError("项目目录必须是绝对路径，且不能包含控制字符")
    try:
        path = path.resolve(strict=True)
    except (OSError, ValueError, RuntimeError) as error:
        raise argparse.ArgumentTypeError("项目目录不存在或无法读取") from error
    if not path.is_dir():
        raise argparse.ArgumentTypeError("项目路径必须指向已存在的目录")
    return str(path)


def normalize_registry(result, cwd):
    data = result.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise RegistryError("invalid_registry", "Skills 查询必须返回当前项目的一份完整登记信息")
    row = data[0]
    if row.get("cwd") != cwd or not isinstance(row.get("skills"), list) or not isinstance(row.get("errors"), list):
        raise RegistryError("invalid_registry", "Skills 查询返回的项目、Skills 或错误列表不符合约定")
    if row["errors"]:
        paths = []
        for error in row["errors"]:
            path = error.get("path") if isinstance(error, dict) else None
            if (isinstance(path, str) and path.isprintable()
                    and len(path.encode("utf-8")) <= 1024
                    and Path(path).is_absolute() and path not in paths):
                paths.append(path)
                if len(paths) == 8:
                    break
        raise RegistryError("registry_load_failed", "宿主报告 Skill 加载错误，无法确认完整登记信息",
                            error_count=len(row["errors"]), error_paths=paths)
    skills = []
    for skill in row["skills"]:
        if not isinstance(skill, dict):
            raise RegistryError("invalid_registry", "宿主返回了无效的 Skill 登记项")
        name, entry, enabled = skill.get("name"), skill.get("path"), skill.get("enabled")
        if (not isinstance(name, str) or not name.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in name)
                or not isinstance(entry, str) or not Path(entry).is_absolute()
                or any(ord(char) < 32 or ord(char) == 127 for char in entry)
                or type(enabled) is not bool):
            raise RegistryError("invalid_registry", "Skill 登记项必须包含名称、绝对入口路径及布尔启用状态")
        skills.append({"name": name, "entry": entry, "enabled": enabled})
    return {"ok": True, "cwd": cwd, "skills": skills}


def query_registry(cwd):
    binary = os.environ.get("CODEX_BIN") or shutil.which("codex")
    if not binary:
        raise RegistryError("registry_unavailable", "找不到 Codex 可执行文件，请检查 PATH 或 CODEX_BIN")
    process = None
    try:
        process = subprocess.Popen(
            [binary, "app-server", "--stdio"], cwd=cwd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0,
        )
        deadline = time.monotonic() + TIMEOUT_SECONDS
        pending = bytearray()
        total = 0
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)

            def send(message):
                process.stdin.write(json.dumps(message).encode("utf-8") + b"\n")
                process.stdin.flush()

            def receive(request_id, operation):
                nonlocal total
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RegistryError("registry_timeout", "Codex Skills 查询超时", operation=operation)
                    if b"\n" not in pending:
                        if not selector.select(remaining):
                            raise RegistryError("registry_timeout", "Codex Skills 查询超时", operation=operation)
                        chunk = os.read(process.stdout.fileno(), 65536)
                        if not chunk:
                            raise RegistryError("registry_unavailable", "Codex app-server 未返回查询结果便关闭了连接",
                                                operation=operation, exit_code=process.poll())
                        total += len(chunk)
                        if total > MAX_OUTPUT_BYTES:
                            raise RegistryError("registry_output_limit", "Codex Skills 查询响应超过大小限制",
                                                operation=operation)
                        pending.extend(chunk)
                        continue
                    line, _, rest = pending.partition(b"\n")
                    pending[:] = rest
                    try:
                        message = json.loads(line)
                    except (ValueError, UnicodeError) as error:
                        raise RegistryError("invalid_registry", "Codex app-server 返回了无效 JSON",
                                            operation=operation) from error
                    if not isinstance(message, dict):
                        raise RegistryError("invalid_registry", "Codex app-server 响应必须是对象", operation=operation)
                    if "id" not in message and isinstance(message.get("method"), str):
                        continue
                    if type(message.get("id")) is not int or message["id"] != request_id:
                        raise RegistryError("invalid_registry", "Codex app-server 返回了非预期响应",
                                            operation=operation)
                    if "error" in message:
                        error = message["error"]
                        code = error.get("code") if isinstance(error, dict) else None
                        details = {"rpc_code": code} if type(code) is int else {}
                        raise RegistryError("registry_rpc_error", "Codex app-server 拒绝了 Skills 查询",
                                            operation=operation, **details)
                    if not isinstance(message.get("result"), dict):
                        raise RegistryError("invalid_registry", "Codex app-server 未返回有效结果对象",
                                            operation=operation)
                    return message["result"]

            send({"id": 1, "method": "initialize", "params": {
                "clientInfo": {"name": "workflow_skill_lookup", "version": "1.0.0"},
            }})
            receive(1, "initialize")
            send({"method": "initialized"})
            send({"id": 2, "method": "skills/list", "params": {"cwds": [cwd], "forceReload": True}})
            result = receive(2, "skills/list")
    except OSError as error:
        raise RegistryError("registry_unavailable", "无法执行 Codex app-server Skills 查询",
                            errno=error.errno) from error
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            process.stdin.close()
            process.stdout.close()
    return normalize_registry(result, cwd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", required=True, type=parse_cwd, help="目标项目的实际绝对目录")
    args = parser.parse_args()
    try:
        result = query_registry(args.cwd)
    except RegistryError as error:
        print(json.dumps({"ok": False, "cwd": args.cwd, "error": {
            "code": error.code, "message": str(error), **error.details,
        }}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
