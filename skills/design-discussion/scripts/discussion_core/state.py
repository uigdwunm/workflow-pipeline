"""Shared ledger, identity, filesystem, and transaction primitives."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import uuid
from typing import Any

from .request import RequestContext


IDENTITY_RE = re.compile(r"^(project|tree|topic)-[0-9a-f]{32}$")
ROOT_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
LEDGER_SECTION_NAMES = (
    "Current Topics",
    "Pending Items",
    "Phase Results",
    "Checkpoints",
    "Phase Runs",
    "Pending Document Writes",
    "Impacts",
    "Relations and Coverage",
    "Dependencies and Active Implementations",
    "Conversation Bindings",
    "Recent Events",
)


class ProtocolError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        cause: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.cause = cause
        self.context = context or {}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _expect_keys(source: dict[str, Any], expected: set[str], label: str) -> None:
    observed = set(source)
    if observed != expected:
        missing = sorted(expected - observed)
        unexpected = sorted(observed - expected)
        raise ProtocolError(
            "invalid_request",
            f"{label} fields do not match the bootstrap schema; "
            f"missing={missing!r}; unexpected={unexpected!r}",
        )


def _expect_string(value: Any, label: str, *, max_bytes: int = 512) -> str:
    if not isinstance(value, str) or not value or "\n" in value or "\r" in value:
        raise ProtocolError("invalid_request", f"{label} must be a non-empty single line")
    if len(value.encode("utf-8")) > max_bytes:
        raise ProtocolError("invalid_request", f"{label} exceeds {max_bytes} UTF-8 bytes")
    return value


def _validate_project_path(value: Any) -> Path:
    raw = _expect_string(value, "project_path", max_bytes=4096)
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise ProtocolError("invalid_project_path", "project_path must be absolute")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ProtocolError(
            "invalid_project_path",
            "project_path must resolve to an existing directory",
            cause=str(error),
        ) from error
    if not resolved.is_dir():
        raise ProtocolError("invalid_project_path", "project_path must resolve to a directory")
    if candidate != resolved:
        raise ProtocolError(
            "invalid_project_path",
            "project_path must be canonical and contain no symlink or relative components",
        )
    return resolved


def _validate_uuid4(value: Any, label: str) -> str:
    text = _expect_string(value, label, max_bytes=64)
    try:
        parsed = uuid.UUID(text)
    except ValueError as error:
        raise ProtocolError("invalid_request", f"{label} must be a UUIDv4") from error
    if parsed.version != 4 or str(parsed) != text:
        raise ProtocolError("invalid_request", f"{label} must be a canonical UUIDv4")
    return text


def _new_identity(kind: str) -> str:
    return f"{kind}-{uuid.uuid4().hex}"


def _git_common_dir(project: Path) -> Path | None:
    environment = dict(os.environ)
    environment["LC_ALL"] = "C"
    try:
        membership = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=project,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
    except OSError as error:
        raise ProtocolError(
            "git_probe_failed", "Git worktree membership could not be probed"
        ) from error
    if membership.returncode != 0:
        if "not a git repository" in membership.stderr.casefold():
            return None
        raise ProtocolError(
            "git_probe_failed", "Git worktree membership probe failed closed"
        )
    membership_value = membership.stdout.strip().casefold()
    if membership_value == "false":
        return None
    if membership_value != "true":
        raise ProtocolError(
            "git_probe_failed", "Git worktree membership probe returned an invalid result"
        )
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=project,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
    except OSError as error:
        raise ProtocolError(
            "git_probe_failed", "Git common directory could not be probed"
        ) from error
    if completed.returncode != 0:
        raise ProtocolError("git_probe_failed", "Git common directory probe failed closed")
    common_dir_text = completed.stdout.strip()
    path = Path(common_dir_text)
    if not common_dir_text or not path.is_absolute():
        raise ProtocolError(
            "git_identity_invalid", "Git common directory is empty or not absolute"
        )
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ProtocolError(
            "git_identity_invalid",
            "Git reported a common directory that cannot be resolved",
            cause=str(error),
        ) from error
    if not resolved.is_dir():
        raise ProtocolError("git_identity_invalid", "Git common directory is not a directory")
    return resolved


def _coordination_root(project: Path) -> tuple[Path, str]:
    common_dir = _git_common_dir(project)
    if common_dir is not None:
        return common_dir / "cc-switch" / "design-discussion" / "v1", "git"
    return project / ".codex" / "design-discussion" / "v1", "non-git"


def _project_lock_name(project: Path) -> str:
    return hashlib.sha256(str(project).encode("utf-8")).hexdigest() + ".lock"


def _yaml_scalar(value: str | int | bool | None) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _yaml_record_block(records: list[dict[str, Any]]) -> str:
    lines = ["```yaml", "records:"]
    if not records:
        lines.append("  []")
    for record in records:
        first = True
        for key, value in record.items():
            prefix = "  - " if first else "    "
            lines.append(f"{prefix}{key}: {_yaml_scalar(value)}")
            first = False
    lines.append("```")
    return "\n".join(lines)


def _parse_frontmatter(data: bytes, label: str) -> dict[str, str]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProtocolError("state_corrupt", f"{label} is not UTF-8", cause=str(error)) from error
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ProtocolError("state_corrupt", f"{label} has invalid frontmatter")
    frontmatter = text[4:].split("\n---\n", 1)[0]
    result: dict[str, str] = {}
    for line in frontmatter.splitlines():
        if ": " not in line:
            raise ProtocolError("state_corrupt", f"{label} frontmatter is malformed")
        key, value = line.split(": ", 1)
        if not key or key in result:
            raise ProtocolError("state_corrupt", f"{label} frontmatter keys are invalid")
        result[key] = value.strip('"')
    return result


def _require_regular_nosymlink(path: Path, label: str) -> bytes:
    cursor = path
    while cursor != cursor.parent:
        try:
            status = os.lstat(cursor)
        except OSError as error:
            raise ProtocolError(
                "state_corrupt", f"cannot inspect {label}: {cursor}", cause=str(error)
            ) from error
        if stat.S_ISLNK(status.st_mode):
            raise ProtocolError("state_corrupt", f"{label} contains a symbolic-link component")
        cursor = cursor.parent
    try:
        status = os.lstat(path)
    except OSError as error:
        raise ProtocolError("state_corrupt", f"cannot inspect {label}", cause=str(error)) from error
    if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
        raise ProtocolError("state_corrupt", f"{label} must be a single-link regular file")
    try:
        return path.read_bytes()
    except OSError as error:
        raise ProtocolError("state_corrupt", f"cannot read {label}", cause=str(error)) from error


def _verify_ledger_digest(data: bytes) -> tuple[dict[str, str], str]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProtocolError("state_corrupt", "ledger is not UTF-8", cause=str(error)) from error
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ProtocolError("state_corrupt", "ledger has invalid frontmatter")
    frontmatter_text, body = text[4:].split("\n---\n", 1)
    frontmatter = _parse_frontmatter(data, "ledger")
    digest = frontmatter.get("content_digest")
    if digest is None or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ProtocolError("state_corrupt", "ledger content_digest is invalid")
    lines_without_digest = [
        line for line in frontmatter_text.splitlines() if not line.startswith("content_digest: ")
    ]
    expected = hashlib.sha256(
        (("\n".join(lines_without_digest) + "\n") + body).encode("utf-8")
    ).hexdigest()
    if digest != expected:
        raise ProtocolError("state_corrupt", "ledger content_digest does not match its bytes")
    return frontmatter, text


def _ledger_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    for index, name in enumerate(LEDGER_SECTION_NAMES):
        marker = f"## {name}\n\n"
        if text.count(marker) != 1:
            raise ProtocolError("state_corrupt", f"ledger section {name!r} is missing or duplicated")
        start = text.index(marker) + len(marker)
        if index + 1 < len(LEDGER_SECTION_NAMES):
            next_marker = f"\n## {LEDGER_SECTION_NAMES[index + 1]}\n\n"
            try:
                end = text.index(next_marker, start)
            except ValueError as error:
                raise ProtocolError(
                    "state_corrupt", f"ledger section order is invalid after {name!r}"
                ) from error
        else:
            end = len(text)
        section = text[start:end]
        if not section.startswith("```yaml\nrecords:") or not section.rstrip().endswith("```"):
            raise ProtocolError("state_corrupt", f"ledger section {name!r} has invalid record framing")
        sections[name] = section
    return sections


def _parse_yaml_scalar(value: str, label: str) -> str | int | bool | None:
    if value == "null":
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    if re.fullmatch(r"0|[1-9][0-9]*", value):
        return int(value)
    if value.startswith('"'):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as error:
            raise ProtocolError("state_corrupt", f"{label} has an invalid quoted scalar") from error
        if not isinstance(decoded, str):
            raise ProtocolError("state_corrupt", f"{label} must decode to a string")
        return decoded
    raise ProtocolError("state_corrupt", f"{label} uses an unsupported YAML scalar")


def _parse_record_section(section: str, label: str) -> list[dict[str, Any]]:
    lines = section.strip().splitlines()
    if len(lines) < 3 or lines[:2] != ["```yaml", "records:"] or lines[-1] != "```":
        raise ProtocolError("state_corrupt", f"{label} has invalid YAML framing")
    content = lines[2:-1]
    if content == ["  []"]:
        return []
    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in content:
        if line.startswith("  - "):
            current = {}
            records.append(current)
            field_line = line[4:]
        elif line.startswith("    ") and current is not None:
            field_line = line[4:]
        else:
            raise ProtocolError("state_corrupt", f"{label} contains invalid indentation")
        if ": " not in field_line:
            raise ProtocolError("state_corrupt", f"{label} contains a malformed field")
        key, raw_value = field_line.split(": ", 1)
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key) or key in current:
            raise ProtocolError("state_corrupt", f"{label} contains an invalid or duplicate key")
        current[key] = _parse_yaml_scalar(raw_value, f"{label}.{key}")
    return records


def _require_exact_record(
    sections: dict[str, str], section_name: str, expected: dict[str, Any]
) -> None:
    records = _parse_record_section(sections[section_name], section_name)
    if records != [expected]:
        raise ProtocolError("state_corrupt", f"ledger {section_name!r} record is invalid")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_replace(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _flock_with_timeout(stream: Any, timeout_seconds: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError as error:
            if time.monotonic() >= deadline:
                raise ProtocolError(
                    "coordination_busy",
                    "discussion ledger lock did not become available within five seconds",
                    retryable=True,
                ) from error
            time.sleep(0.05)


def _render_records_ledger(
    frontmatter: dict[str, str], records: dict[str, list[dict[str, Any]]]
) -> bytes:
    body_lines = ["# Design Discussion Ledger", ""]
    for name in LEDGER_SECTION_NAMES:
        body_lines.extend([f"## {name}", "", _yaml_record_block(records[name]), ""])
    body = "\n".join(body_lines)
    schema_version = frontmatter["schema_version"]
    frontmatter_lines = [
        f"schema_version: {schema_version}",
        f"project_id: {frontmatter['project_id']}",
        f"tree_id: {frontmatter['tree_id']}",
    ]
    if schema_version == "2":
        _validate_creation_receipt(frontmatter)
        frontmatter_lines.extend(
            [
                "creation_idempotency_key: "
                f"{json.dumps(frontmatter['creation_idempotency_key'])}",
                "creation_fingerprint: "
                f"{json.dumps(frontmatter['creation_fingerprint'])}",
            ]
        )
    elif schema_version == "1":
        if (
            "creation_idempotency_key" in frontmatter
            or "creation_fingerprint" in frontmatter
        ):
            raise ProtocolError(
                "state_corrupt", "v1 ledger has unsupported creation receipt"
            )
    else:
        raise ProtocolError("state_corrupt", "ledger schema_version is unsupported")
    frontmatter_lines.extend(
        [
            f"ledger_revision: {frontmatter['ledger_revision']}",
            f"event_count: {frontmatter['event_count']}",
            "project_manifest_path: "
            f"{json.dumps(frontmatter['project_manifest_path'])}",
        ]
    )
    frontmatter_without_digest = "\n".join(frontmatter_lines) + "\n"
    digest = _sha256((frontmatter_without_digest + body).encode("utf-8"))
    return (
        "---\n"
        + frontmatter_without_digest
        + f"content_digest: {digest}\n"
        + "---\n"
        + body
    ).encode("utf-8")


def _validate_creation_receipt(frontmatter: dict[str, str]) -> tuple[str, str]:
    key = frontmatter.get("creation_idempotency_key")
    fingerprint = frontmatter.get("creation_fingerprint")
    if not isinstance(key, str) or not isinstance(fingerprint, str):
        raise ProtocolError("state_corrupt", "ledger creation receipt is incomplete")
    try:
        parsed = uuid.UUID(key)
    except ValueError as error:
        raise ProtocolError(
            "state_corrupt", "ledger creation idempotency key is invalid"
        ) from error
    if parsed.version != 4 or str(parsed) != key:
        raise ProtocolError("state_corrupt", "ledger creation idempotency key is invalid")
    if not SHA256_RE.fullmatch(fingerprint):
        raise ProtocolError("state_corrupt", "ledger creation fingerprint is invalid")
    return key, fingerprint


def _hydrate_legacy_creation_receipt(
    frontmatter: dict[str, str], records: dict[str, list[dict[str, Any]]]
) -> None:
    schema_version = frontmatter.get("schema_version")
    has_key = "creation_idempotency_key" in frontmatter
    has_fingerprint = "creation_fingerprint" in frontmatter
    if schema_version == "2":
        _validate_creation_receipt(frontmatter)
        return
    if schema_version != "1":
        raise ProtocolError("state_corrupt", "ledger schema_version is unsupported")
    if has_key or has_fingerprint:
        raise ProtocolError("state_corrupt", "v1 ledger has unsupported creation receipt")
    genesis_events = [
        event
        for event in records["Recent Events"]
        if event.get("event_type") == "root-topic-bootstrapped"
    ]
    if not genesis_events:
        return
    if len(genesis_events) != 1:
        raise ProtocolError("state_corrupt", "ledger creation event is duplicated")
    event = genesis_events[0]
    topic_id = event.get("topic_id")
    matching_topics = [
        topic for topic in records["Current Topics"] if topic.get("topic_id") == topic_id
    ]
    if len(matching_topics) != 1:
        raise ProtocolError(
            "state_corrupt", "ledger creation event does not identify one topic"
        )
    receipt = {
        "creation_idempotency_key": event.get("idempotency_key"),
        "creation_fingerprint": event.get("request_fingerprint"),
    }
    key, fingerprint = _validate_creation_receipt(receipt)
    frontmatter.update(
        {
            "schema_version": "2",
            "creation_idempotency_key": key,
            "creation_fingerprint": fingerprint,
        }
    )


def _is_exact_creation_replay(
    frontmatter: dict[str, str], *, idempotency_key: str, request_fingerprint: str
) -> bool:
    if frontmatter.get("schema_version") == "1":
        return False
    stored_key, stored_fingerprint = _validate_creation_receipt(frontmatter)
    if stored_key != idempotency_key:
        return False
    if stored_fingerprint != request_fingerprint:
        raise ProtocolError(
            "idempotency_conflict",
            "ledger creation idempotency key was reused with different parameters",
        )
    return True


def _load_records(ledger_path: Path) -> tuple[dict[str, str], dict[str, list[dict[str, Any]]]]:
    frontmatter, text = _verify_ledger_digest(
        _require_regular_nosymlink(ledger_path, "ledger")
    )
    sections = _ledger_sections(text)
    records = {
        name: _parse_record_section(sections[name], name) for name in LEDGER_SECTION_NAMES
    }
    _hydrate_legacy_creation_receipt(frontmatter, records)
    return frontmatter, records


def _evolution_paths(
    request: dict[str, Any], *, query: bool = False, allow_tree_topic: bool = False
) -> tuple[Path, Path, Path, Path, str]:
    required = {
        "protocol_version",
        "operation",
        "project_path",
        "project_id",
        "tree_id",
        "actor_topic_id",
        "actor_conversation_ref",
    }
    if not query:
        required |= {
            "expected_ledger_revision",
            "expected_topic_revision",
            "idempotency_key",
        }
    if not required.issubset(request):
        raise ProtocolError(
            "invalid_request",
            f"request is missing fields: {sorted(required - set(request))!r}",
        )
    if isinstance(request, RequestContext):
        project = request.project_path
        project_id = request.project_id
        tree_id = request.tree_id
        topic_id = request.actor_topic_id
        owner_ref = request.actor_conversation_ref
        if None in {project, project_id, tree_id, topic_id, owner_ref}:
            raise ProtocolError("invalid_request", "request common envelope is incomplete")
    else:
        project = _validate_project_path(request["project_path"])
        project_id = _expect_string(request["project_id"], "project_id")
        tree_id = _expect_string(request["tree_id"], "tree_id")
        topic_id = _expect_string(request["actor_topic_id"], "actor_topic_id")
        owner_ref = _expect_string(
            request["actor_conversation_ref"], "actor_conversation_ref"
        )
    for label, value, kind in (
        ("project_id", project_id, "project"),
        ("tree_id", tree_id, "tree"),
        ("actor_topic_id", topic_id, "topic"),
    ):
        if not value.startswith(f"{kind}-") or not IDENTITY_RE.fullmatch(value):
            raise ProtocolError("discussion_identity_conflict", f"{label} is invalid")
    manifest_path = project / "docs" / "discussions" / ".codex-project.md"
    manifest = _parse_frontmatter(
        _require_regular_nosymlink(manifest_path, "project identity manifest"),
        "project identity manifest",
    )
    observed = (manifest.get("project_id"), manifest.get("tree_id"))
    if observed != (project_id, tree_id) or (
        not allow_tree_topic and manifest.get("topic_id") != topic_id
    ):
        raise ProtocolError(
            "discussion_identity_conflict",
            "request identity does not match the project identity manifest",
        )
    coordination_root, _ = _coordination_root(project)
    ledger_path = coordination_root / "projects" / project_id / "trees" / tree_id / "ledger.md"
    topic_path = project / "docs" / "discussions" / manifest["root_slug"] / "topic.md"
    lock_path = coordination_root / "locks" / _project_lock_name(project)
    return project, ledger_path, topic_path, lock_path, owner_ref


def _record_by_id(
    records: list[dict[str, Any]], field: str, value: str, label: str
) -> dict[str, Any]:
    matches = [record for record in records if record.get(field) == value]
    if len(matches) != 1:
        raise ProtocolError("record_not_found", f"{label} does not identify one record")
    return matches[0]


def _verify_topic_owner(
    records: dict[str, list[dict[str, Any]]],
    topic_id: str,
    owner_ref: str,
    *,
    allow_active_grilling: bool = False,
) -> None:
    active = [
        record
        for record in records["Conversation Bindings"]
        if record.get("topic_id") == topic_id and record.get("binding_state") == "active"
    ]
    if len(active) == 1 and active[0].get("conversation_ref") == owner_ref:
        return
    if not allow_active_grilling:
        raise ProtocolError(
            "document_ownership_conflict",
            "the caller is not the active document owner for this topic",
        )
    active_grilling_carriers = []
    for record in records["Phase Runs"]:
        if record.get("run_kind") != "phase-run" or record.get("state") != "active":
            continue
        data = _json_field(record, "data_json", "phase run")
        if (
            data.get("wrapper_integration") is True
            and data.get("carrier_kind") == "dedicated-grilling"
            and data.get("source_topic_id") == topic_id
        ):
            active_grilling_carriers.extend(
                attempt.get("carrier_ref")
                for attempt in data.get("attempts", [])
                if attempt.get("state") == "active" and attempt.get("claimed") is True
            )
    if active_grilling_carriers == [owner_ref]:
        return
    raise ProtocolError(
        "document_ownership_conflict",
        "the caller is not the active document owner for this topic",
    )


def _active_pending_write(records: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    active = [
        record
        for record in records["Pending Document Writes"]
        if record.get("state") != "completed"
    ]
    if len(active) > 1:
        raise ProtocolError("state_corrupt", "more than one document write is active")
    return active[0] if active else None


def _json_field(record: dict[str, Any], field: str, label: str) -> Any:
    raw = record.get(field)
    if not isinstance(raw, str):
        raise ProtocolError("state_corrupt", f"{label}.{field} is missing")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise ProtocolError("state_corrupt", f"{label}.{field} is invalid JSON") from error


def _topic_snapshot(records: dict[str, list[dict[str, Any]]], topic_id: str) -> dict[str, Any]:
    pending_items = [
        record for record in records["Pending Items"] if record.get("topic_id") == topic_id
    ]
    decisions = [
        _json_field(record, "data_json", "decision")
        for record in pending_items
        if record.get("item_kind") == "decision"
    ]
    questions = [
        _json_field(record, "data_json", "question")
        for record in pending_items
        if record.get("item_kind") == "question"
    ]
    ideas = [
        _json_field(record, "data_json", "idea")
        for record in pending_items
        if record.get("item_kind") == "idea"
    ]
    impacts = [
        _json_field(record, "data_json", "impact")
        for record in records["Impacts"]
        if record.get("topic_id") == topic_id
    ]
    return {
        "decisions": sorted(decisions, key=lambda item: item["decision_id"]),
        "questions": sorted(questions, key=lambda item: item["question_id"]),
        "ideas": sorted(ideas, key=lambda item: item["idea_id"]),
        "impacts": sorted(impacts, key=lambda item: item["impact_id"]),
    }


def _mutation_fingerprint(request: dict[str, Any]) -> str:
    return _sha256(
        _canonical_json(
            {key: value for key, value in request.items() if key != "idempotency_key"}
        ).encode("utf-8")
    )


def _idempotent_result(
    records: dict[str, list[dict[str, Any]]], request: dict[str, Any]
) -> dict[str, Any] | None:
    key = request["idempotency_key"]
    matches = [event for event in records["Recent Events"] if event.get("idempotency_key") == key]
    if not matches:
        return None
    event = matches[-1]
    if event.get("request_fingerprint") != _mutation_fingerprint(request):
        raise ProtocolError("idempotency_conflict", "idempotency key was reused for another update")
    result = _json_field(event, "result_json", "event")
    result["idempotent_replay"] = True
    return result


def _append_event(
    records: dict[str, list[dict[str, Any]]],
    request: dict[str, Any],
    *,
    revision: int,
    event_type: str,
    result: dict[str, Any],
) -> None:
    records["Recent Events"].append(
        {
            "event_id": f"event-{revision:08d}",
            "event_type": event_type,
            "ledger_revision": revision,
            "topic_id": request["actor_topic_id"],
            "idempotency_key": request["idempotency_key"],
            "request_fingerprint": _mutation_fingerprint(request),
            "result_json": _canonical_json(result),
        }
    )
    records["Recent Events"] = records["Recent Events"][-200:]


def _write_ledger_transaction(
    ledger_path: Path,
    frontmatter: dict[str, str],
    records: dict[str, list[dict[str, Any]]],
    request: dict[str, Any],
    *,
    ledger_revision: int,
    event_type: str,
    result: dict[str, Any],
) -> None:
    _append_event(
        records,
        request,
        revision=ledger_revision,
        event_type=event_type,
        result=result,
    )
    frontmatter["ledger_revision"] = str(ledger_revision)
    frontmatter["event_count"] = str(int(frontmatter["event_count"]) + 1)
    _atomic_replace(ledger_path, _render_records_ledger(frontmatter, records))


def _validate_revisions(
    request: dict[str, Any], frontmatter: dict[str, str], topic_record: dict[str, Any]
) -> tuple[int, int]:
    ledger_revision = int(frontmatter["ledger_revision"])
    topic_revision = int(topic_record["record_revision"])
    if request["expected_ledger_revision"] != ledger_revision:
        raise ProtocolError(
            "ledger_revision_conflict",
            "expected ledger revision is stale",
            context={"ledger_revision": ledger_revision, "record_revision": topic_revision},
        )
    if request["expected_topic_revision"] != topic_revision:
        raise ProtocolError(
            "record_revision_conflict",
            "expected topic record revision is stale",
            context={"ledger_revision": ledger_revision, "record_revision": topic_revision},
        )
    return ledger_revision, topic_revision


def _inject_failure(name: str) -> None:
    if os.environ.get("CODEX_DISCUSSION_TEST_FAILPOINT") == name:
        raise ProtocolError(
            "injected_failure", f"injected failure at checkpoint boundary: {name}"
        )


def _validated_string_list(
    value: Any, label: str, *, allow_empty: bool = False, max_items: int = 64
) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value) or len(value) > max_items:
        raise ProtocolError("invalid_request", f"{label} must be a bounded array")
    result = [_expect_string(item, f"{label} item", max_bytes=512) for item in value]
    if len(set(result)) != len(result):
        raise ProtocolError("invalid_request", f"{label} values must be unique")
    return result
