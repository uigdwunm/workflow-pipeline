#!/usr/bin/env python3
"""Read-only entry evidence; host assertions remain a caller trust boundary."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import discussion_protocol
import skill_preflight
import thread_settings
from supervision_protocol import _binding, _verify_binding, ProtocolError as SupervisionError

PROTOCOL = "workflow-entry-v1"
MAX_BYTES = 2 * 1024 * 1024


class PreparationError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, code, message):
    if not condition:
        raise PreparationError(code, message)


def fields(value, required, optional=()):
    require(isinstance(value, dict) and set(required) <= set(value) <= set(required) | set(optional),
            "invalid_request", "missing or unknown object fields")


def nonempty(value):
    require(isinstance(value, str) and 0 < len(value.encode()) <= 4096 and "\0" not in value,
            "invalid_request", "nonempty bounded string required")
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def git(repository, *args, data=None, check=True):
    # Do not let inherited alternate Git state redirect repository evidence/writes.
    require(not any(k in os.environ for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES")),
            "git_environment", "alternate Git environment is unsupported")
    literal = [] if args[0] == "check-ignore" else ["--literal-pathspecs"]
    result = subprocess.run(["git", *literal, "-C", str(repository), *args],
                            input=data, capture_output=True)
    require(not check or result.returncode == 0, "git_failed", "Git operation failed: " + " ".join(args[:2]))
    return result


def git_text(repository, *args):
    return git(repository, *args).stdout.decode("utf-8").strip()


def repository_facts(cwd):
    root = Path(nonempty(cwd))
    require(root.is_absolute() and root.is_dir(), "invalid_project", "absolute project directory required")
    root = root.resolve()
    probe = git(root, "rev-parse", "--show-toplevel", check=False)
    if probe.returncode:
        return {"cwd": str(root), "root": str(root), "kind": "non-git"}
    top = Path(probe.stdout.decode().strip()).resolve()
    return {"cwd": str(root), "root": str(top), "kind": "git",
            "git_common_dir": str(Path(git_text(root, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()),
            "branch": git_text(root, "branch", "--show-current") or None,
            "head": git_text(root, "rev-parse", "HEAD")}


def document_path(root, relative):
    nonempty(relative)
    path = Path(relative)
    require(not path.is_absolute() and path.as_posix() == relative and ".." not in path.parts
            and path.parts and path.parts[0] != ".git", "invalid_path", "normalized repository document path required")
    current = Path(root)
    for part in path.parts:
        current = current / part
        require(not current.is_symlink(), "invalid_path", "symlink document component")
        if current.exists():
            require(current == Path(root) / path or current.is_dir(), "invalid_path", "non-directory path component")
    require(not current.exists() or stat.S_ISREG(current.stat().st_mode), "invalid_path", "regular document required")
    return current


def read_document(root, relative):
    target = document_path(root, relative)
    if not target.exists():
        return None
    require(target.stat().st_size <= MAX_BYTES, "invalid_document", "document exceeds limit")
    with target.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    require(len(data) <= MAX_BYTES, "invalid_document", "document exceeds limit")
    return data


def topic_read(project, attachment):
    fields(attachment, {"project_id", "tree_id", "actor_topic_id", "actor_conversation_ref"})
    return discussion_protocol.handle({"protocol_version": 1, "operation": "read-topic",
                                       "project_path": project, **attachment})


def reject_attached_standalone(project, source, actor_refs):
    discovered = discussion_protocol.handle({"protocol_version": 1, "operation": "discover-context",
                                             "project_path": project})
    require(discovered.get("state") != "ambiguous", "source_ambiguous", "discussion context is ambiguous; retain the existing source")
    if discovered.get("context") != "ledger":
        require(not (source.get("path") and source["path"].startswith("docs/discussions/")),
                "source_changed", "discussion documents require their original attachment")
        return
    _, records = discussion_protocol._load_records(Path(discovered["ledger_path"]))
    selected = str(Path(project) / source["path"]) if "path" in source else None
    for topic in records["Current Topics"]:
        require(selected is None or selected != topic.get("topic_document_path"),
                "source_changed", "topic document cannot become a standalone requirement")
        if source["kind"] not in {"stage1", "conversation"}:
            continue
        for actor in actor_refs:
            try:
                discussion_protocol._verify_topic_owner(records, topic["topic_id"], actor, operation="read-topic")
            except discussion_protocol.ProtocolError as error:
                if error.code != "document_ownership_conflict":
                    raise
            else:
                raise PreparationError("source_changed", "current actor has a discussion binding; use its existing document")


def resolve(request):
    fields(request, {"protocol", "operation", "stage", "action", "host", "source"},
           {"target", "registry", "target_stages", "required_skills", "expected", "pinned_packages"})
    require(request["protocol"] == PROTOCOL and request["operation"] in {"resolve", "verify"},
            "unsupported_protocol", "unsupported entry operation or version")
    if request["operation"] == "verify":
        require(isinstance(request.get("expected"), dict), "invalid_evidence", "original entry evidence required")
    host = request["host"]
    fields(host, {"project_path", "project_id", "thread_id", "controller_ref", "role", "source_ref", "receipt"},
           {"supported_configurations", "actor_ref"})
    for key in ("project_id", "thread_id", "controller_ref", "role", "receipt"):
        nonempty(host[key])
    if host["source_ref"] is not None:
        nonempty(host["source_ref"])
    if "actor_ref" in host:
        nonempty(host["actor_ref"])
    facts = repository_facts(os.getcwd())
    require(str(Path(host["project_path"]).resolve()) == facts["cwd"], "project_mismatch", "host project differs from actual cwd")
    settings = thread_settings.resolve_current_thread_settings()
    require(settings["thread_id"] == host["thread_id"], "identity_mismatch", "host task differs from runtime task")
    stage = request["stage"]
    require(type(stage) is int and stage in range(5), "invalid_stage", "stage must be 0 through 4")
    roles = ({"controller", "dedicated-discussion"}, {"controller", "dedicated-problem-framing"},
             {"controller", "solution-designer"}, {"controller", "implementation-dispatcher", "execution-agent", "scripted-carrier"},
             {"controller", "closure-agent", "scripted-carrier"})
    require(host["role"] in roles[stage], "role_mismatch", "role is not valid for this stage")
    actor_refs = {settings["thread_id"], "codex-thread:" + settings["thread_id"], host.get("actor_ref", settings["thread_id"])}
    require(host["role"] != "controller" or host["controller_ref"] in actor_refs,
            "identity_mismatch", "controller must be the current task")
    require(host["role"] == "controller" or host["source_ref"] is not None,
            "identity_unavailable", "carrier needs authenticated source receipt")
    if "supported_configurations" in host:
        supported = host["supported_configurations"]
        require(isinstance(supported, list) and bool(supported), "configuration_unavailable", "target tool configurations required")
        for pair in supported:
            fields(pair, {"model", "reasoning_effort"})
        require({k: settings[k] for k in ("model", "reasoning_effort")} in supported,
                "configuration_unsupported", "runtime configuration is not advertised by target tool")
    source = request["source"]
    fields(source, {"kind"}, {"path", "attachment"})
    require(source["kind"] in {"none", "stage1", "conversation", "discussion", "frozen"}, "invalid_source", "unknown requirement source")
    require(source["kind"] != "stage1" or stage == 1, "invalid_source", "stage1 draft requires Stage 1")
    require(source["kind"] != "conversation" or stage == 2, "invalid_source", "conversation snapshot requires Stage 2")
    requirement = dict(source)
    if source["kind"] == "discussion":
        fields(source, {"kind", "attachment"})
        fields(source["attachment"], {"project_id", "tree_id", "actor_topic_id", "actor_conversation_ref"})
        require(source["attachment"].get("actor_conversation_ref") in actor_refs,
                "identity_mismatch", "discussion actor must be the authenticated current task")
        topic = topic_read(facts["root"], source["attachment"])
        requirement["topic"] = topic
    else:
        require("attachment" not in source, "invalid_source", "non-discussion source cannot carry an attachment")
        if "path" in source:
            data = read_document(facts["root"], source["path"])
            requirement["sha256"] = None if data is None else hashlib.sha256(data).hexdigest()
        if source["kind"] != "none":
            reject_attached_standalone(facts["root"], source, actor_refs)
    target = request.get("target")
    if target is not None:
        fields(target, {"kind"}, {"repository", "branch", "binding"})
        if target["kind"] == "flow":
            fields(target, {"kind", "binding"})
            _verify_binding(_binding(target["binding"]), platform_cwd=Path(facts["cwd"]))
        else:
            fields(target, {"kind", "repository", "branch"})
            require(target["kind"] == "planning" and facts["kind"] == "git", "target_mismatch", "Git planning target required")
            require(str(Path(target["repository"]).resolve()) == facts["root"] and target["branch"] == facts["branch"],
                    "target_mismatch", "planning checkout or branch differs")
    preflight = {"stage": stage, "action": request["action"]}
    preflight.update({k: request[k] for k in ("target_stages", "required_skills") if k in request})
    preflight.update({"registry": request["registry"]} if "registry" in request else {"registry_query": {"cwd": facts["cwd"]}})
    packages = skill_preflight.preflight(preflight)
    pinned = request.get("pinned_packages")
    if pinned is not None:
        require(isinstance(pinned, dict) and set(pinned) == set(packages["packages"]), "package_changed", "original pinned package route required")
        for name, identity in pinned.items():
            skill_preflight.verify_identity(identity)
            require(identity["compatibility_key"] == packages["packages"][name]["compatibility_key"], "package_changed", "registered compatibility changed")
        packages["packages"] = pinned
    own = skill_preflight.STAGES[stage]
    executing_root = Path(__file__).resolve().parent.parent
    if (executing_root / "package.json").exists():
        expected_packages = request.get("expected", {}).get("packages", packages["packages"])
        require(expected_packages[own]["root"] == str(executing_root), "package_changed", "execute the stage's pinned package root")
    result = {"protocol": PROTOCOL, "repository": facts, "actor": host,
              "entry": {"stage": stage, "action": request["action"]}, "target": target,
              "packages": packages["packages"], "external": packages["external"],
              "configuration": settings, "requirement": requirement}
    if request["operation"] == "verify":
        expected = request.get("expected")
        require(isinstance(expected, dict) and expected.get("evidence_digest") == digest({k: v for k, v in expected.items() if k != "evidence_digest"}),
                "invalid_evidence", "complete entry evidence required")
        for identity in expected["packages"].values():
            skill_preflight.verify_identity(identity)
        # Current registration may move, but cannot replace any pinned package.
        for key in ("repository", "actor", "entry", "target", "requirement"):
            require(result[key] == expected[key], "entry_changed", "entry evidence changed: " + key)
        require(set(result["packages"]) == set(expected["packages"]), "entry_changed", "package route changed")
        for name, identity in result["packages"].items():
            require(identity["compatibility_key"] == expected["packages"][name]["compatibility_key"], "entry_changed", "registered package compatibility changed")
        result["packages"] = expected["packages"]
        require(all(settings[k] == expected["configuration"][k] for k in ("thread_id", "model", "reasoning_effort")),
                "configuration_changed", "confirmed runtime settings changed")
    else:
        require("expected" not in request, "invalid_request", "resolve cannot replace a pinned entry")
    return {**result, "evidence_digest": digest(result)}


def decode(raw):
    require(len(raw) <= MAX_BYTES, "invalid_request", "request exceeds limit")
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "invalid_request", "duplicate JSON key")
            result[key] = value
        return result
    def constant(value):
        raise PreparationError("invalid_request", "nonfinite JSON number")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def cli(handler):
    request = {}
    try:
        request = decode(sys.stdin.buffer.read(MAX_BYTES + 1))
        response = {"ok": True, "result": handler(request)}
    except (PreparationError, thread_settings.SettingsError, skill_preflight.PreflightError,
            discussion_protocol.ProtocolError, SupervisionError, OSError, ValueError, KeyError, TypeError) as error:
        response = {"ok": False, "error": {"code": getattr(error, "code", "preparation_failed"),
                    "message": str(error)[:1024], "operation": request.get("operation") if isinstance(request, dict) else None,
                    "recovery": "Retain the original checkpoint and document; reconcile the same operation before retry."}}
    print(json.dumps(response, ensure_ascii=False, sort_keys=True))
    return 0 if response["ok"] else 1


if __name__ == "__main__":
    sys.exit(cli(resolve))
