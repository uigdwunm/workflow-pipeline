#!/usr/bin/env python3
"""Read-only, committed function-plan catalog for Stage-2 scope authority."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
from pathlib import Path

import entry_prepare as entry
import stage_scope


PLAN_BLOCK = re.compile(rb"(?m)^```execution-plan-v1\r?\n(.*?)\r?\n```[ \t]*$", re.DOTALL)
HEADING = re.compile(rb"(?m)^(##|###) ([^\r\n]+)\r?$")
SECTION_ID = re.compile(rb"^(N[0-9]+|D[0-9]+)\b")
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\Z")
SYMBOL = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\Z")
PLAN_VERSION_LINE = re.compile(rb"(?m)^Plan Version: [1-9][0-9]*\r?\n")
SOURCE_LOCATION = re.compile(r"([^\s:]+):([1-9][0-9]*)\Z")
MAX_DOCUMENT = 1024 * 1024
MAX_PLAN = 256 * 1024
CODE_SUFFIXES = {'.py', '.js', '.jsx', '.ts', '.tsx', '.go', '.rs', '.java',
                 '.c', '.cc', '.cpp', '.h', '.hpp', '.sh'}
require = entry.require


def _incomplete(condition, message):
    require(condition, "execution_plan_incomplete", message)


def _committed_file(repository, commit, path):
    """Return bytes, mode and blob identity for one exact committed regular file."""
    stage_scope.validate_path(path, repository)
    _incomplete(isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit),
                "plan source requires a full planning commit")
    rows = entry.git(repository, "ls-tree", "-z", commit, "--", path).stdout.split(b"\0")
    _incomplete(len(rows) == 2 and rows[1] == b"", "missing committed plan source: " + path)
    header, sep, found = rows[0].partition(b"\t")
    _incomplete(sep == b"\t" and found.decode("utf-8", errors="replace") == path,
                "plan source must resolve to the exact path: " + path)
    parts = header.split(b" ")
    _incomplete(len(parts) == 3 and parts[0] in (b"100644", b"100755") and
                parts[1] == b"blob" and re.fullmatch(rb"[0-9a-f]{40}", parts[2]) is not None,
                "plan source must be a committed regular file: " + path)
    body = entry.git(repository, "cat-file", "blob", parts[2].decode()).stdout
    _incomplete(len(body) <= MAX_DOCUMENT, "plan source exceeds size limit: " + path)
    return {"path": path, "mode": parts[0].decode(), "blob": parts[2].decode(),
            "sha256": hashlib.sha256(body).hexdigest(), "body": body}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field: " + key)
        result[key] = value
    return result


def read_ticket_plan(repository, commit, path):
    """Read exactly one bounded execution-plan-v1 block from a Git document."""
    source = _committed_file(repository, commit, path)
    matches = list(PLAN_BLOCK.finditer(source["body"]))
    _incomplete(len(matches) == 1, "exactly one execution-plan-v1 block required: " + path)
    raw = matches[0].group(1)
    _incomplete(len(raw) <= MAX_PLAN, "function plan exceeds size limit: " + path)
    try:
        plan = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
                          parse_float=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (UnicodeError, ValueError) as exc:
        raise entry.PreparationError("execution_plan_incomplete", "invalid function-plan JSON: " + path) from exc
    _incomplete(isinstance(plan, dict), "function plan must be a JSON object: " + path)
    return {"path": path, "commit": commit, "mode": source["mode"],
            "sha256": source["sha256"], "blob": source["blob"],
            "plan": plan, "block_span": [matches[0].start(), matches[0].end()]}


def _sections(body):
    found = list(HEADING.finditer(body))
    sections = {}
    for index, match in enumerate(found):
        if match.group(1) == b"##":
            key = match.group(2).decode("utf-8", errors="replace")
            end = next((item.start() for item in found[index + 1:] if item.group(1) == b"##"), len(body))
        else:
            section = SECTION_ID.match(match.group(2))
            if section is None:
                continue
            key = section.group(1).decode()
            end = found[index + 1].start() if index + 1 < len(found) else len(body)
        _incomplete(key not in sections, "duplicate Spec section: " + key)
        sections[key] = body[match.start():end]
    return sections


def _ticket_contract(source):
    body = source["body"]
    blocks = list(PLAN_BLOCK.finditer(body))
    _incomplete(len(blocks) == 1, "exactly one function plan required: " + source["path"])
    block = blocks[0]
    protected = body[:block.start()] + body[block.end():]
    # The version belongs to the plan identity and may increase with an internal
    # revision. All other Ticket bytes, including later sections, are protected.
    _incomplete(len(PLAN_VERSION_LINE.findall(protected)) <= 1,
                "duplicate Ticket plan version: " + source["path"])
    return PLAN_VERSION_LINE.sub(b"", protected)


def contract_projection(repository, commit, sources, *, spec_path=None, contract_refs=()):
    """Keep immutable contract bytes distinct from each whole-document identity."""
    projection = []
    inline = not any("/issues/" in source["path"] for source in sources)
    for source in sources:
        path, body = source["path"], source["body"]
        if "/issues/" in path:
            protected = _ticket_contract(source)
        elif path == spec_path:
            # N2 production evidence and N4 inventory are the enumerated private
            # revision surfaces. Every other Spec byte remains in the contract.
            _sections(body)
            matches = list(HEADING.finditer(body))
            public = {ref.split(":", 2)[1] for ref in contract_refs if ref.startswith("PRD:")}
            spans = []
            for index, match in enumerate(matches):
                section = SECTION_ID.match(match.group(2)) if match.group(1) == b"###" else None
                if section is not None and section.group(1) in (b"N2", b"N4") and section.group(1).decode() not in public:
                    spans.append((match.start(), matches[index + 1].start() if index + 1 < len(matches) else len(body)))
            protected = body
            for start, end in reversed(spans):
                line_end = body.find(b"\n", start, end)
                protected = protected[:start] + body[start:line_end + 1 if line_end >= 0 else end] + protected[end:]
            if inline:
                protected = PLAN_BLOCK.sub(b"", protected)
                protected = PLAN_VERSION_LINE.sub(b"", protected)
        else:
            protected = body
        projection.append({"path": path, "mode": source["mode"],
                           "sha256": hashlib.sha256(protected).hexdigest(),
                           "blob_sha256": source["sha256"]})
    return {"files": projection, "digest": entry.digest([{k: value for k, value in item.items() if k != "blob_sha256"}
                                                   for item in projection])}


def _strings(value, label, *, empty=False):
    _incomplete(isinstance(value, list) and (empty or bool(value)) and len(value) <= 4096 and
                all(isinstance(item, str) and bool(item.strip()) for item in value),
                label + " requires bounded nonempty strings")
    return value


def _ids(value, label, *, empty=False):
    items = _strings(value, label, empty=empty)
    _incomplete(len(items) == len(set(items)) and all(IDENTIFIER.fullmatch(item) for item in items),
                label + " has duplicate or malformed IDs")
    return items


def _stable_dependency_order(source_order, edges, label):
    """Choose the first source-order item whose declared predecessors are ready."""
    remaining, completed, ordered = set(source_order), set(), []
    while remaining:
        ready = next((identity for identity in source_order
                      if identity in remaining and set(edges[identity]) <= completed), None)
        _incomplete(ready is not None, label + " dependency cycle")
        ordered.append(ready)
        completed.add(ready)
        remaining.remove(ready)
    return ordered


def _code_source(item):
    relative = item['path']
    return (item['kind'] == 'source' and Path(relative).suffix.lower() in CODE_SUFFIXES and
            not relative.startswith(('tests/', 'test/')) and
            not Path(relative).name.startswith('test_'))


def _python_symbols(body):
    try:
        tree = ast.parse(body.decode("utf-8"))
    except (UnicodeError, SyntaxError) as exc:
        raise entry.PreparationError("execution_plan_incomplete", "existing Python target is not parseable") from exc
    result = set()

    def walk(nodes, prefix=""):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + node.name
                if isinstance(node, ast.ClassDef):
                    walk(node.body, name + ".")
                else:
                    result.add(name)
    walk(tree.body)
    return result


def _non_python_symbol(symbol):
    """Check a bounded complete reference without assuming a language grammar."""
    return (isinstance(symbol, str) and 0 < len(symbol.encode("utf-8")) <= 512 and
            symbol.isprintable() and not any(char.isspace() for char in symbol) and
            not symbol.startswith(("::", ".", "#", "/")) and
            not symbol.endswith(("::", ".", "#", "/")) and
            bool(re.findall(r"[\w$]+", symbol)))


def _non_python_source_evidence(repository, baseline, target, approach):
    """Verify designer-supplied committed locations, without parsing another language."""
    leaf = re.findall(r"[\w$]+", target["symbol"])[-1]
    for label in ("source-location", "production-caller"):
        entries = [item[len(label) + 2:] for item in approach if item.startswith(label + ": ")]
        _incomplete(len(entries) == 1, "existing non-Python target needs one " + label)
        location = SOURCE_LOCATION.fullmatch(entries[0])
        _incomplete(location is not None, "invalid " + label + " reference")
        path, line_number = location.group(1), int(location.group(2))
        stage_scope.validate_path(path, repository)
        _incomplete(label != "source-location" or path == target["path"],
                    "source-location must name the target file")
        lines = _committed_file(repository, baseline, path)["body"].splitlines()
        _incomplete(line_number <= len(lines) and leaf.encode("utf-8") in lines[line_number - 1],
                    label + " does not identify the planned symbol in committed source: " + path)


def _declared_blockers(contract, ticket_ids):
    lines = re.findall(rb"(?m)^\*\*Blocked by:\*\* ([^\r\n]+)$", contract)
    _incomplete(len(lines) == 1, "Ticket requires one Blocked by declaration")
    declared = lines[0].decode("utf-8", errors="replace").strip()
    if re.match(r"^None(?:\s|$)", declared):
        _incomplete("," not in declared, "None cannot be combined with Ticket blockers")
        return []
    parts = [item.strip() for item in declared.split(",")]
    _incomplete(all(parts), "Ticket blocker declaration is incomplete")
    resolved = []
    for item in parts:
        if item in (ticket_ids or ()) or ticket_ids is None and IDENTIFIER.fullmatch(item):
            resolved.append(item)
            continue
        # The published Tickets use short numeric labels. Resolve them against
        # the actual committed stable Ticket IDs, never an assumed prefix.
        matches = [value for value in ticket_ids or () if item.isdigit() and value.rsplit("-", 1)[-1] == item]
        _incomplete(len(matches) == 1, "unknown or ambiguous Ticket blocker: " + item)
        resolved.append(matches[0])
    _incomplete(len(resolved) == len(set(resolved)), "duplicate Ticket blocker")
    return resolved


def validate_ticket_plan(source, repository, commit, manifest_paths, *, baseline, spec_sections,
                         ticket_ids=None):
    """Validate one Ticket's function, artifact and behavior contracts."""
    plan, path = source["plan"], source["path"]
    _incomplete(set(plan) == {"schema_version", "ticket_id", "plan_version", "spec_ref", "contract_refs",
                               "blocked_by", "verifications", "steps", "artifact_steps"},
                "function-plan root fields are incomplete: " + path)
    _incomplete(type(plan["schema_version"]) is int and plan["schema_version"] == 1 and
                type(plan["plan_version"]) is int and plan["plan_version"] > 0 and
                isinstance(plan["ticket_id"], str) and IDENTIFIER.fullmatch(plan["ticket_id"]) and
                isinstance(plan["spec_ref"], dict) and set(plan["spec_ref"]) == {"path", "section"},
                "invalid function-plan identity: " + path)
    ticket_body = _committed_file(repository, commit, path)["body"]
    start, end = source["block_span"]
    declarations = ticket_body[:start] + ticket_body[end:]
    ticket_lines = re.findall(rb"(?m)^Ticket ID:[^\r\n]*\r?$", declarations)
    version_lines = re.findall(rb"(?m)^Plan Version:[^\r\n]*\r?$", declarations)
    _incomplete(len(ticket_lines) == 1 and len(version_lines) == 1 and
                ticket_lines[0].removesuffix(b"\r") == ("Ticket ID: " + plan["ticket_id"]).encode() and
                version_lines[0].removesuffix(b"\r") == ("Plan Version: " + str(plan["plan_version"])).encode(),
                "Ticket identity/version differs from its function plan: " + path)
    _incomplete(plan["spec_ref"]["path"] in spec_sections and
                isinstance(plan["spec_ref"]["section"], str) and
                plan["spec_ref"]["section"] in spec_sections[plan["spec_ref"]["path"]],
                "Ticket Spec reference is missing: " + path)
    refs = _strings(plan["contract_refs"], "contract_refs")
    _incomplete(len(refs) == len(set(refs)), "duplicate contract reference: " + path)
    contract = _ticket_contract({"path": path, "body": ticket_body})
    for ref in refs:
        if ref.startswith(plan["ticket_id"] + ":"):
            part = ref.split(":", 1)[1]
            needle = ("**" + part + ":**").encode() if part == "What to build" else (part + ":").encode()
            _incomplete(needle in contract, "missing Ticket contract reference " + ref)
        elif ref.startswith("PRD:"):
            part = ref.split(":", 2)[1]
            _incomplete(part in spec_sections[plan["spec_ref"]["path"]],
                        "missing Spec contract reference " + ref)
        else:
            _incomplete(False, "unknown contract reference " + ref)
    blockers = _ids(plan["blocked_by"], "blocked_by", empty=True)
    declared = _declared_blockers(contract, ticket_ids)
    _incomplete(set(blockers) == set(declared), "Ticket blocker differs from plan: " + path)
    verifications = plan["verifications"]
    _incomplete(isinstance(verifications, list) and verifications and len(verifications) <= 4096,
                "bounded behavior verification required: " + path)
    verify_ids = []
    for item in verifications:
        _incomplete(isinstance(item, dict) and set(item) == {"id", "behavior", "seam", "commands"} and
                    isinstance(item["id"], str) and IDENTIFIER.fullmatch(item["id"]) and
                    isinstance(item["behavior"], str) and item["behavior"].strip() and
                    isinstance(item["seam"], str) and item["seam"].strip(),
                    "behavior verification is incomplete: " + path)
        _strings(item["commands"], "verification commands")
        verify_ids.append(item["id"])
    _incomplete(len(verify_ids) == len(set(verify_ids)), "duplicate verification ID: " + path)
    _incomplete(isinstance(plan["steps"], list) and isinstance(plan["artifact_steps"], list) and
                (plan["steps"] or plan["artifact_steps"]) and
                len(plan["steps"]) + len(plan["artifact_steps"]) <= 4096,
                "Ticket needs function or artifact steps: " + path)
    ids, covered = [], set()
    for step in plan["steps"]:
        _incomplete(isinstance(step, dict) and set(step) == {"id", "target", "capability", "approach", "inputs",
                                                            "outputs", "failures", "depends_on", "verification_refs", "read_only"},
                    "function step fields are incomplete: " + path)
        target = step["target"]
        _incomplete(isinstance(target, dict) and set(target) == {"path", "symbol", "operation"} and
                    isinstance(target["path"], str) and
                    isinstance(target["operation"], str) and target["operation"] in {"add", "modify", "delete"} and
                    isinstance(target["symbol"], str) and 0 < len(target["symbol"].encode("utf-8")) <= 512 and
                    (bool(SYMBOL.fullmatch(target["symbol"])) if target["path"].endswith(".py")
                     else _non_python_symbol(target["symbol"])),
                    "function target is incomplete: " + path)
        stage_scope.validate_path(target["path"], repository)
        _incomplete(target["path"] in manifest_paths,
                    "function target must name an implementation file: " + path)
        for key in ("capability",):
            _incomplete(isinstance(step[key], str) and step[key].strip(), "function capability required: " + path)
        for key in ("approach", "inputs", "outputs", "failures", "verification_refs"):
            _strings(step[key], key)
        _ids(step["depends_on"], "depends_on", empty=True)
        _strings(step["read_only"], "read_only", empty=True)
        for read_path in step["read_only"]:
            stage_scope.validate_path(read_path, repository)
        _incomplete(set(step["verification_refs"]) <= set(verify_ids),
                    "unknown behavior verification reference: " + path)
        _incomplete(isinstance(step["id"], str) and IDENTIFIER.fullmatch(step["id"]), "invalid step ID: " + path)
        ids.append(step["id"])
        covered.add(target["path"])
        if target["operation"] != "add":
            existing = _committed_file(repository, baseline, target["path"])
            if target["path"].endswith(".py"):
                _incomplete(target["symbol"] in _python_symbols(existing["body"]),
                            "existing Python symbol is missing: " + target["symbol"] +
                            " (target: " + target["path"] + ")")
            else:
                _non_python_source_evidence(repository, baseline, target, step["approach"])
    for artifact in plan["artifact_steps"]:
        _incomplete(isinstance(artifact, dict) and set(artifact) ==
                    {"id", "path", "operation", "capability", "approach", "verification_refs"} and
                    isinstance(artifact["id"], str) and IDENTIFIER.fullmatch(artifact["id"]) and
                    isinstance(artifact["path"], str) and artifact["path"] in manifest_paths and
                    isinstance(artifact["operation"], str) and
                    artifact["operation"] in {"add", "modify", "delete"} and
                    isinstance(artifact["capability"], str) and artifact["capability"].strip() and
                    isinstance(artifact["approach"], str) and artifact["approach"].strip(),
                    "artifact step is incomplete: " + path)
        _strings(artifact["verification_refs"], "artifact verification_refs")
        _incomplete(set(artifact["verification_refs"]) <= set(verify_ids),
                    "unknown artifact verification reference: " + path)
        ids.append(artifact["id"])
        covered.add(artifact["path"])
    _incomplete(len(ids) == len(set(ids)), "duplicate function/artifact ID: " + path)
    return {"ticket_id": plan["ticket_id"], "plan_version": plan["plan_version"], "source":
            {**{key: source[key] for key in ("path", "commit", "mode", "sha256", "blob")},
             "plan_version": plan["plan_version"]},
            "plan": plan, "covered_paths": sorted(covered)}


def collect_execution_plan(repository, commit, planning_paths, scope, requirement_identity):
    """Build one deterministic catalog from committed Ticket or inline Spec plans."""
    _incomplete(isinstance(planning_paths, list) and planning_paths == sorted(set(planning_paths)),
                "exact sorted planning paths required")
    sources = [_committed_file(repository, commit, path) for path in planning_paths]
    tickets = [item["path"] for item in sources if "/issues/" in item["path"] and item["path"].endswith(".md")]
    plan_paths = tickets or [item["path"] for item in sources if PLAN_BLOCK.search(item["body"])]
    _incomplete(bool(plan_paths) and len(plan_paths) <= 4096,
                "bounded committed function plans required for implementation scope")
    plans = [read_ticket_plan(repository, commit, path) for path in plan_paths]
    ids = [item["plan"].get("ticket_id") for item in plans]
    _incomplete(all(isinstance(item, str) and IDENTIFIER.fullmatch(item) for item in ids) and
                len(ids) == len(set(ids)), "duplicate or invalid Ticket ID")
    spec_paths = set()
    for item in plans:
        spec_ref = item["plan"].get("spec_ref")
        if isinstance(spec_ref, dict):
            spec_path = spec_ref.get("path")
            _incomplete(isinstance(spec_path, str) and bool(spec_path),
                        "invalid spec_ref.path: " + item["path"])
            spec_paths.add(spec_path)
    _incomplete(all(path in planning_paths for path in spec_paths), "Spec plan reference escapes planning scope")
    spec_sections = {item["path"]: _sections(item["body"]) for item in sources if item["path"] in spec_paths}
    manifest = scope["downstream"]["manifest"]
    manifest_paths = {item["path"] for item in manifest if item["owner"] == "implementation"}
    records = [validate_ticket_plan(plan, repository, commit, manifest_paths,
                                    baseline=scope["baseline"], spec_sections=spec_sections,
                                    ticket_ids=ids) for plan in plans]
    tickets_by_id = {record["ticket_id"]: record for record in records}
    ticket_edges = {ticket_id: tickets_by_id[ticket_id]["plan"]["blocked_by"] for ticket_id in ids}
    for ticket_id, blockers in ticket_edges.items():
        _incomplete(all(blocker in tickets_by_id and blocker != ticket_id for blocker in blockers),
                    "unknown Ticket blocker: " + ticket_id)
    ticket_order = _stable_dependency_order(ids, ticket_edges, "Ticket")
    records = [tickets_by_id[ticket_id] for ticket_id in ticket_order]
    all_steps = {step["id"]: record["ticket_id"] for record in records
                 for step in record["plan"]["steps"] + record["plan"]["artifact_steps"]}
    _incomplete(len(all_steps) <= 4096 and len(all_steps) ==
                sum(len(record["plan"]["steps"] + record["plan"]["artifact_steps"])
                    for record in records), "too many or duplicate plan IDs across Tickets")
    edges, source_order = {}, []
    for record in records:
        ticket = record["ticket_id"]
        _incomplete(all(blocker in ids and blocker != ticket for blocker in record["plan"]["blocked_by"]),
                    "unknown Ticket blocker: " + ticket)
        blockers = [step["id"] for blocker in record["plan"]["blocked_by"]
                    for step in tickets_by_id[blocker]["plan"]["steps"] +
                                tickets_by_id[blocker]["plan"]["artifact_steps"]]
        for step in record["plan"]["steps"] + record["plan"]["artifact_steps"]:
            deps = step.get("depends_on", [])
            _incomplete(len(deps) <= len(all_steps) and all(dep in all_steps and dep != step["id"] and
                            (all_steps[dep] == ticket or all_steps[dep] in record["plan"]["blocked_by"])
                        for dep in deps), "unknown or undeclared function dependency: " + step["id"])
            edges[step["id"]] = list(dict.fromkeys([*blockers, *deps]))
            source_order.append(step["id"])
    ordered = _stable_dependency_order(source_order, edges, "function/artifact")
    covered = {path for record in records for path in record["covered_paths"]}
    function_paths = {step['target']['path'] for record in records for step in record['plan']['steps']}
    for item in manifest:
        if item["owner"] == "implementation":
            _incomplete(item["path"] in covered or item["kind"] == "generated" and
                        any(source in covered for source in item["source_paths"]),
                        "implementation path lacks function/artifact coverage: " + item["path"])
            _incomplete(not _code_source(item) or item['path'] in function_paths,
                        "code source requires a real function step: " + item['path'])
    projection = contract_projection(repository, commit, sources,
                                     spec_path=next(iter(spec_paths)) if len(spec_paths) == 1 else None,
                                     contract_refs={ref for plan in plans for ref in plan["plan"]["contract_refs"]})
    catalog = {"schema_version": 1, "commit": commit, "requirement_identity": requirement_identity,
               "tickets": records, "ordered_step_ids": ordered,
               "sources": [{"path": item["path"], "commit": commit, "mode": item["mode"],
                            "sha256": item["sha256"], "blob": item["blob"]} for item in sources],
               "contract_projection": projection, "contract_digest": projection["digest"]}
    catalog["plan_digest"] = entry.digest({key: value for key, value in catalog.items() if key != "plan_digest"})
    return catalog


def authority_envelope(catalog, original_scope_revision, *, input_digest=None):
    """Project one catalog into the exact Scope Authority plan schema."""
    _incomplete(type(original_scope_revision) is int and original_scope_revision > 0,
                "original Scope Authority revision is invalid")
    _incomplete(isinstance(catalog, dict) and type(catalog.get("schema_version")) is int and
                catalog["schema_version"] == 1 and
                isinstance(catalog.get("tickets"), list) and len(catalog["tickets"]) <= 4096 and
                isinstance(catalog.get("ordered_step_ids"), list) and
                len(catalog["ordered_step_ids"]) <= 4096 and
                catalog.get("plan_digest") ==
                entry.digest({key: value for key, value in catalog.items() if key != "plan_digest"}),
                "function-plan catalog digest changed")
    if catalog["commit"] is None:
        _incomplete(isinstance(input_digest, str) and re.fullmatch(r"[0-9a-f]{64}", input_digest),
                    "inline plan requires original input digest")
        _incomplete(len(catalog["tickets"]) == 1 and
                    set(catalog["tickets"][0]["source"]) ==
                    {"kind", "commit", "sha256", "plan_version"} and
                    catalog["tickets"][0]["source"]["kind"] == "inline" and
                    catalog["tickets"][0]["source"]["commit"] is None and
                    catalog["tickets"][0]["source"]["plan_version"] ==
                    catalog["tickets"][0]["plan_version"],
                    "null planning commit requires an inline source")
        sources = [{"kind": "inline", "commit": None, "sha256": catalog["tickets"][0]["source"]["sha256"],
                    "input_digest": input_digest}]
    else:
        sources = copy_plan_refs(catalog.get("sources"))
        _incomplete(isinstance(catalog["commit"], str) and
                    re.fullmatch(r"[0-9a-f]{40}", catalog["commit"]) and
                    isinstance(sources, list) and bool(sources) and len(sources) <= 4096 and
                    all(isinstance(source, dict) and set(source) ==
                        {"path", "commit", "mode", "sha256", "blob"} and
                        source["commit"] == catalog["commit"] for source in sources),
                    "staged plan source requires committed identity")
    return {"schema_version": 1, "catalog": copy_plan_refs(catalog),
            "plan_digest": catalog["plan_digest"], "contract_digest": catalog["contract_digest"],
            "plan_commit": catalog["commit"], "sources": sources,
            "original_scope_revision": original_scope_revision}


def current_catalog(authority):
    """Fail closed unless the current Scope Authority carries the canonical wrapper."""
    envelope = authority.get("execution_plan") if isinstance(authority, dict) else None
    if (not isinstance(envelope, dict) or set(envelope) != {
            "schema_version", "catalog", "plan_digest", "contract_digest", "plan_commit",
            "sources", "original_scope_revision"} or
            type(envelope.get("schema_version")) is not int or envelope["schema_version"] != 1):
        raise entry.PreparationError("execution_plan_stale", "current execution-plan envelope is invalid")
    catalog = envelope.get("catalog")
    try:
        expected = authority_envelope(catalog, envelope["original_scope_revision"],
            input_digest=(envelope["sources"][0].get("input_digest") if
                          isinstance(envelope.get("sources"), list) and envelope["sources"] and
                          isinstance(envelope["sources"][0], dict) else None))
        tickets = catalog["tickets"]
        if catalog["commit"] is None:
            valid_sources = (len(tickets) == 1 and tickets[0]["source"].get("commit") is None and
                tickets[0]["source"]["sha256"] == envelope["sources"][0]["sha256"])
        else:
            valid_sources = (len(envelope["sources"]) == len({source["path"] for source in envelope["sources"]}) and
                all(set(ticket["source"]) ==
                    {"path", "plan_version", "commit", "sha256", "mode", "blob"} and
                    ticket["source"]["plan_version"] == ticket["plan_version"] and
                    any(all(ticket["source"].get(key) == source[key] for key in
                            ("path", "commit", "mode", "sha256", "blob"))
                        for source in envelope["sources"])
                    for ticket in tickets))
    except (KeyError, TypeError, IndexError, ValueError, entry.PreparationError) as exc:
        raise entry.PreparationError("execution_plan_stale", "current execution-plan envelope is invalid") from exc
    if (envelope != expected or not valid_sources or
            envelope["original_scope_revision"] > authority.get("revision", -1)):
        raise entry.PreparationError("execution_plan_stale", "current execution-plan envelope changed")
    return catalog


def step_ref(ticket, step):
    source = ticket["source"]
    return {"ticket_id": ticket["ticket_id"], "step_id": step["id"],
            "plan_version": ticket["plan_version"], "source_commit": source.get("commit"),
            "source_sha256": source["sha256"], "step_digest": entry.digest(step)}


def validate_plan_refs(catalog, plan_refs, assigned_paths, read_only, completed_results, authority):
    """Resolve one ordered writer group against the current sealed plan authority.

    The catalog is supplied by C's verified authority. Callers must also compare
    ``authority`` with C's current control projection at each lifecycle boundary.
    """
    def stale(message):
        raise entry.PreparationError("execution_plan_stale", message)

    if (not isinstance(authority, dict) or authority.get("state") != "sealed" or
            current_catalog(authority) != catalog):
        stale("current sealed execution plan is missing or changed")
    expected_authority = {"revision": authority["revision"],
        "authority_digest": entry.digest(authority), "plan_digest": catalog["plan_digest"],
        "contract_digest": catalog["contract_digest"]}
    if not isinstance(plan_refs, list) or not plan_refs or len(plan_refs) > len(catalog["ordered_step_ids"]):
        stale("allocation requires current function plan refs")
    for field, values in (("assigned_paths", assigned_paths), ("read_only", read_only)):
        if not isinstance(values, list) or field == "assigned_paths" and not values:
            stale("allocation paths and read-only inputs must be exact and disjoint")
        for index, relative in enumerate(values):
            if not isinstance(relative, str) or not relative:
                stale(f"{field}[{index}] requires a path")
    if (len(assigned_paths) != len(set(assigned_paths)) or
            len(read_only) != len(set(read_only)) or
            set(assigned_paths) & set(read_only)):
        stale("allocation paths and read-only inputs must be exact and disjoint")
    tickets = {ticket["ticket_id"]: ticket for ticket in catalog["tickets"]}
    steps = {step["id"]: (ticket, step) for ticket in catalog["tickets"]
             for step in ticket["plan"]["steps"] + ticket["plan"]["artifact_steps"]}
    selected = []
    for index, ref in enumerate(plan_refs):
        if not isinstance(ref, dict) or set(ref) != {"ticket_id", "step_id", "plan_version",
                                                     "source_commit", "source_sha256", "step_digest"}:
            stale(f"plan_refs[{index}] has incomplete identity")
        for field in ("ticket_id", "step_id"):
            value = ref[field]
            if not isinstance(value, str) or IDENTIFIER.fullmatch(value) is None:
                stale(f"plan_refs[{index}].{field} requires a valid ID")
        if type(ref["plan_version"]) is not int or ref["plan_version"] <= 0:
            stale(f"plan_refs[{index}].plan_version requires a positive integer")
        source_commit = ref["source_commit"]
        if source_commit is not None and (not isinstance(source_commit, str) or
                re.fullmatch(r"[0-9a-f]{40}", source_commit) is None):
            stale(f"plan_refs[{index}].source_commit requires a full commit")
        for field in ("source_sha256", "step_digest"):
            value = ref[field]
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                stale(f"plan_refs[{index}].{field} requires a SHA-256 digest")
        pair = steps.get(ref["step_id"])
        if pair is None:
            stale("unknown function plan ref: " + str(ref["step_id"]))
        ticket, step = pair
        if ref != step_ref(ticket, step):
            stale("function plan source, version or step changed: " + step["id"])
        selected.append(step["id"])
    order = catalog["ordered_step_ids"]
    if len(selected) != len(set(selected)) or selected != [step_id for step_id in order if step_id in selected]:
        stale("function group must follow current dependency order without duplicate refs")
    completed = current_accepted_steps(catalog, completed_results, authority)
    available = set(completed)
    covered, required_read, verification_commands = set(), set(), set()
    for step_id in selected:
        ticket, step = steps[step_id]
        blockers = {item["id"] for other in ticket["plan"]["blocked_by"]
                    for item in tickets[other]["plan"]["steps"] + tickets[other]["plan"]["artifact_steps"]}
        if not set(step.get("depends_on", [])) | blockers <= available:
            stale("function dependency is not completed or earlier in this group: " + step_id)
        available.add(step_id)
        covered.add(step["target"]["path"] if "target" in step else step["path"])
        required_read.update(step.get("read_only", []))
        verification_ids = set(step["verification_refs"])
        for verification in ticket["plan"]["verifications"]:
            if verification["id"] in verification_ids:
                verification_commands.update(verification["commands"])
    manifest = authority["scope"]["downstream"]["manifest"]
    for item in manifest:
        if (item["owner"] == "implementation" and item["kind"] == "generated" and
                any(source in covered for source in item["source_paths"])):
            covered.add(item["path"])
    if not set(assigned_paths) <= covered or not required_read <= set(read_only):
        stale("assigned files or read-only inputs lack selected plan coverage")
    implementation = {item['path'] for item in manifest if item['owner'] == 'implementation'}
    if set(read_only) & implementation != required_read & implementation:
        stale('implementation read-only inputs must match selected plan refs')
    read_fingerprints = {}
    for relative in set(read_only) & implementation:
        owners = [result for result in completed_results if relative in result.get('paths', [])]
        if not owners:
            stale('implementation read-only input has no accepted owner: ' + relative)
        owner = owners[-1]
        delivered = owner.get('git_result_snapshot') or {}
        started = (owner.get('git_snapshot') or {}).get('files', {})
        has_fingerprint = relative in delivered or relative in started
        fingerprint = delivered[relative] if relative in delivered else started.get(relative)
        if (owner.get('state') != 'accepted' or owner.get('stopped') is not True or
                owner.get('plan_authority') != expected_authority or not has_fingerprint):
            stale('implementation read-only input lacks a current accepted fingerprint: ' + relative)
        read_fingerprints[relative] = fingerprint
    if not verification_commands:
        stale("selected function group has no behavior verification")
    return {"plan_refs": copy_plan_refs(plan_refs), "plan_authority": expected_authority,
            "ordered_step_ids": selected, "verification_commands": sorted(verification_commands),
            "required_read_only": sorted(required_read), "read_only_fingerprints": read_fingerprints,
            "group_digest": entry.digest([expected_authority, plan_refs])}


def accepted_read_fingerprints(result, required):
    """Return the accepted read witness, never reconstructing it from later bytes."""
    required = set(required)
    if not required and 'source_result_digest' not in result:
        return {}
    if 'source_result_digest' in result:
        witness = result.get('read_only_fingerprints')
    else:
        snapshot = result.get('git_snapshot')
        witness = snapshot.get('files') if isinstance(snapshot, dict) else None
    if not isinstance(witness, dict) or not required <= set(witness):
        return None
    if 'source_result_digest' in result and set(witness) != required:
        return None
    return {relative: witness[relative] for relative in required}


def current_accepted_steps(catalog, results, authority, current_fingerprints=None):
    """Select each step's newest accepted result and check its original inputs."""
    expected = {'revision': authority['revision'],
        'authority_digest': entry.digest(authority), 'plan_digest': catalog['plan_digest'],
        'contract_digest': catalog['contract_digest']}
    _, steps = _catalog_steps(catalog)
    implementation = {item['path'] for item in authority['scope']['downstream']['manifest']
                      if item['owner'] == 'implementation'}
    latest_steps, latest_outputs = {}, {}
    for result in results:
        if (result.get('state') != 'accepted' or result.get('stopped') is not True or
                result.get('plan_authority') != expected):
            continue
        delivered = result.get('git_result_snapshot')
        for relative in result.get('paths', []):
            started = (result.get('git_snapshot') or {}).get('files', {})
            if not isinstance(delivered, dict):
                latest_outputs.pop(relative, None)
            elif relative in delivered:
                latest_outputs[relative] = delivered[relative]
            elif relative in started:
                latest_outputs[relative] = started[relative]
        refs = result.get('plan_refs')
        if not isinstance(refs, list) or not refs:
            continue
        selected = []
        valid_refs = True
        for ref in refs:
            pair = steps.get(ref.get('step_id')) if isinstance(ref, dict) else None
            if pair is None:
                valid_refs = False
                continue
            selected.append(ref['step_id'])
            if ref != step_ref(*pair):
                valid_refs = False
        valid_refs = (valid_refs and len(selected) == len(set(selected)) and selected ==
            [identity for identity in catalog['ordered_step_ids'] if identity in selected])
        witness = None
        if valid_refs:
            required = {relative for identity in selected
                        for relative in steps[identity][1].get('read_only', [])
                        if relative in implementation}
            witness = accepted_read_fingerprints(result, required)
        for identity in selected:
            latest_steps[identity] = (result, witness)
    completed = set()
    for identity, (result, witness) in latest_steps.items():
        if witness is None or not isinstance(result.get('git_result_snapshot'), dict):
            continue
        expected_inputs = latest_outputs if current_fingerprints is None else current_fingerprints
        if all(relative in expected_inputs and expected_inputs[relative] == fingerprint
               for relative, fingerprint in witness.items()):
            completed.add(identity)
    return completed


def copy_plan_refs(refs):
    return json.loads(json.dumps(refs, ensure_ascii=False))


def _catalog_steps(catalog):
    tickets = {ticket['ticket_id']: ticket for ticket in catalog['tickets']}
    steps = {step['id']: (ticket, step) for ticket in catalog['tickets']
             for step in ticket['plan']['steps'] + ticket['plan']['artifact_steps']}
    _incomplete(len(steps) == sum(len(ticket['plan']['steps'] + ticket['plan']['artifact_steps'])
                                  for ticket in catalog['tickets']),
                'ambiguous function identity in plan revision')
    return tickets, steps


def _step_path(step):
    return step['target']['path'] if 'target' in step else step['path']


def affected_plan_closure(old_authority, new_authority, changed_planning_paths):
    """Find affected functions from both plan versions and their dependency DAGs."""
    old, new = current_catalog(old_authority), current_catalog(new_authority)
    old_tickets, old_steps = _catalog_steps(old)
    new_tickets, new_steps = _catalog_steps(new)
    _incomplete(set(old_tickets) == set(new_tickets), 'plan revision changed behavior Tickets')
    old_manifest = {item['path']: item for item in old_authority['scope']['downstream']['manifest']
                    if item['owner'] == 'implementation'}
    new_manifest = {item['path']: item for item in new_authority['scope']['downstream']['manifest']
                    if item['owner'] == 'implementation'}
    def ownership(item):
        return None if item is None else {key: value for key, value in item.items()
                                           if key != 'basis'}
    changed_sources = set(changed_planning_paths)
    impacted = set(old_steps) ^ set(new_steps)
    affected_paths = {path for path in set(old_manifest) | set(new_manifest)
                      if ownership(old_manifest.get(path)) != ownership(new_manifest.get(path))}
    affected_verifications = set()
    for step_id in set(old_steps) & set(new_steps):
        old_ticket, old_step = old_steps[step_id]
        new_ticket, new_step = new_steps[step_id]
        old_checks = {item['id']: item for item in old_ticket['plan']['verifications']}
        new_checks = {item['id']: item for item in new_ticket['plan']['verifications']}
        referenced = set(old_step['verification_refs']) | set(new_step['verification_refs'])
        changed_checks = {identity for identity in referenced
                          if old_checks.get(identity) != new_checks.get(identity)}
        affected_verifications.update(changed_checks)
        if (old_step != new_step or old_ticket['ticket_id'] != new_ticket['ticket_id'] or
                old_ticket['plan']['blocked_by'] != new_ticket['plan']['blocked_by'] or
                changed_checks or changed_sources.intersection(old_step.get('read_only', [])) or
                changed_sources.intersection(new_step.get('read_only', [])) or
                ownership(old_manifest.get(_step_path(old_step))) !=
                ownership(new_manifest.get(_step_path(new_step)))):
            impacted.add(step_id)
    for ticket_id in old_tickets:
        before = {item['id']: item for item in old_tickets[ticket_id]['plan']['artifact_steps']}
        after = {item['id']: item for item in new_tickets[ticket_id]['plan']['artifact_steps']}
        for identity in set(before) | set(after):
            if before.get(identity) != after.get(identity):
                affected_paths.update(item['path'] for item in (before.get(identity), after.get(identity)) if item)
    dependents = {identity: set() for identity in set(old_steps) | set(new_steps)}
    for tickets, steps in ((old_tickets, old_steps), (new_tickets, new_steps)):
        for step_id, (ticket, step) in steps.items():
            dependencies = set(step.get('depends_on', []))
            for blocker in ticket['plan']['blocked_by']:
                _incomplete(blocker in tickets, 'unknown Ticket blocker in plan revision: ' + blocker)
                dependencies.update(item['id'] for item in
                    tickets[blocker]['plan']['steps'] + tickets[blocker]['plan']['artifact_steps'])
            _incomplete(dependencies <= set(steps), 'unknown function dependency in plan revision: ' + step_id)
            for dependency in dependencies:
                dependents[dependency].add(step_id)
    while True:
        expanded = impacted | {dependent for identity in impacted for dependent in dependents[identity]}
        affected_paths.update(_step_path(step) for step_id in expanded
                              for mapping in (old_steps, new_steps) if step_id in mapping
                              for _, step in (mapping[step_id],))
        expanded.update(step_id for step_id, (_, step) in {**old_steps, **new_steps}.items()
                        if _step_path(step) in affected_paths)
        expanded.update(step_id for mapping in (old_steps, new_steps)
                        for step_id, (_, step) in mapping.items()
                        if affected_paths.intersection(step.get('read_only', [])))
        if expanded == impacted:
            break
        impacted = expanded
    affected_verifications.update(reference for step_id in impacted
        for mapping in (old_steps, new_steps) if step_id in mapping
        for reference in mapping[step_id][1]['verification_refs'])
    order = list(dict.fromkeys(old['ordered_step_ids'] + new['ordered_step_ids']))
    return {'impacted_step_ids': [identity for identity in order if identity in impacted],
            'retired_step_ids': [identity for identity in old['ordered_step_ids'] if identity not in new_steps],
            'verification_ids': sorted(affected_verifications), 'paths': sorted(affected_paths)}


def classify_retained_results(old_authority, new_authority, executions, carried_results,
                              previous_revalidation_source, snapshot, baseline_fingerprints,
                              changed_planning_paths):
    """Project accepted results into a new revision without editing original receipts."""
    impact = affected_plan_closure(old_authority, new_authority, changed_planning_paths)
    old_catalog, new_catalog = current_catalog(old_authority), current_catalog(new_authority)
    _, old_steps = _catalog_steps(old_catalog)
    _, new_steps = _catalog_steps(new_catalog)
    old_ref = {'revision': old_authority['revision'],
               'authority_digest': entry.digest(old_authority),
               'plan_digest': old_catalog['plan_digest'],
               'contract_digest': old_catalog['contract_digest']}
    new_ref = {'revision': new_authority['revision'],
               'authority_digest': entry.digest(new_authority),
               'plan_digest': new_catalog['plan_digest'],
               'contract_digest': new_catalog['contract_digest']}
    allowed = set(new_authority['scope']['implementation_paths'])
    old_implementation = set(old_authority['scope']['implementation_paths'])
    _incomplete(allowed <= set(snapshot['files']) and allowed <= set(baseline_fingerprints),
                'revision snapshot lacks exact implementation fingerprints')
    # Historical carry is effective until a current writer replaces its path.
    sources = list(carried_results) + list(executions)
    latest_owner = {}
    for index, result in enumerate(sources):
        for relative in result['paths']:
            latest_owner[relative] = index
    carried = []
    carried_paths = set()
    for index, result in enumerate(sources):
        if result.get('state') != 'accepted' or result.get('stopped') is not True:
            continue
        refs = result.get('plan_refs', [])
        if not refs or result.get('plan_authority') != old_ref or not result.get('git_result_snapshot'):
            continue
        selected = [ref['step_id'] for ref in refs]
        if (any(identity in impact['impacted_step_ids'] or identity not in new_steps
                for identity in selected) or
                any(relative not in allowed or relative in impact['paths'] or latest_owner[relative] != index
                    for relative in result['paths'])):
            continue
        delivered = result['git_result_snapshot']
        started = result.get('git_snapshot', {}).get('files', {})
        expected = {relative: delivered[relative] if relative in delivered else started.get(relative)
                    for relative in result['paths']}
        if any((relative not in delivered and relative not in started) or
               snapshot['files'][relative] != fingerprint
               for relative, fingerprint in expected.items()):
            continue
        required_reads = {relative for identity in selected
            for relative in old_steps[identity][1].get('read_only', [])
            if relative in old_implementation}
        read_fingerprints = accepted_read_fingerprints(result, required_reads)
        if read_fingerprints is None or any(
                snapshot['files'].get(relative) != fingerprint
                for relative, fingerprint in read_fingerprints.items()):
            continue
        mapped = []
        for old_plan_ref in refs:
            identity = old_plan_ref['step_id']
            old_ticket, old_step = old_steps[identity]
            new_ticket, new_step = new_steps[identity]
            _incomplete(old_plan_ref == step_ref(old_ticket, old_step) and
                old_step == new_step, 'accepted result plan ref changed: ' + identity)
            mapped.append(step_ref(new_ticket, new_step))
        original = copy.deepcopy(result)
        carried.append({'state': 'accepted', 'stopped': True,
            'agent_ref': result['agent_ref'], 'task_id': result['task_id'],
            'allocation_digest': result['allocation_digest'], 'paths': copy.deepcopy(result['paths']),
            'file_hashes': copy.deepcopy(result['file_hashes']),
            'git_result_snapshot': copy.deepcopy(result['git_result_snapshot']),
            'read_only_fingerprints': read_fingerprints,
            'plan_refs': mapped, 'plan_authority': new_ref,
            'source_result_digest': result.get('source_result_digest', entry.digest(original)),
            'source_revision': result.get('source_revision', old_ref['revision'])})
        carried_paths.update(result['paths'])
    prior_pending = set((previous_revalidation_source or {}).get('pending_paths', []))
    if previous_revalidation_source is not None:
        _incomplete(all(snapshot['files'].get(relative) ==
                        previous_revalidation_source['snapshot']['files'].get(relative)
                        for relative in prior_pending if relative not in latest_owner),
                    'unaccepted revision bytes changed before revalidation')
    pending = []
    for relative in sorted(allowed):
        if snapshot['files'][relative] == baseline_fingerprints[relative] or relative in carried_paths:
            continue
        _incomplete(relative in latest_owner or relative in prior_pending,
                    'unallocated implementation bytes cannot enter plan revision: ' + relative)
        _incomplete(all(result.get('stopped') is True for result in sources
                        if relative in result['paths']),
                    'writer has not stopped for plan revision: ' + relative)
        pending.append(relative)
    return {'impact': impact, 'carried_results': carried, 'pending_paths': pending}


def collect_inline_plan(plan, scope, semantic, requirement_identity, *, repository=None):
    """Validate a standalone logical slice without inventing a Ticket document."""
    _incomplete(isinstance(plan, dict) and set(plan) == {
        "schema_version", "ticket_id", "plan_version", "spec_ref", "contract_refs",
        "blocked_by", "verifications", "steps", "artifact_steps"},
        "standalone inline plan needs the execution-plan-v1 fields")
    _incomplete(type(plan["schema_version"]) is int and plan["schema_version"] == 1 and
                type(plan["plan_version"]) is int and
                plan["plan_version"] > 0 and isinstance(plan["ticket_id"], str) and
                IDENTIFIER.fullmatch(plan["ticket_id"]) is not None and
                plan["spec_ref"] == {"path": "inline", "section": "brief"} and
                plan["blocked_by"] == [], "invalid standalone inline plan identity")
    _strings(plan["contract_refs"], "inline contract_refs")
    _incomplete(isinstance(plan["verifications"], list) and plan["verifications"] and
                len(plan["verifications"]) <= 4096,
                "inline plan needs behavior verification")
    verify_ids = []
    for verification in plan["verifications"]:
        _incomplete(isinstance(verification, dict) and set(verification) ==
                    {"id", "behavior", "seam", "commands"} and
                    isinstance(verification["id"], str) and IDENTIFIER.fullmatch(verification["id"]) and
                    isinstance(verification["behavior"], str) and verification["behavior"].strip() and
                    isinstance(verification["seam"], str) and verification["seam"].strip(),
                    "incomplete inline behavior verification")
        _strings(verification["commands"], "inline verification commands")
        verify_ids.append(verification["id"])
    _incomplete(len(verify_ids) == len(set(verify_ids)) and
                isinstance(plan["steps"], list) and isinstance(plan["artifact_steps"], list) and
                (plan["steps"] or plan["artifact_steps"]) and
                len(plan["steps"]) + len(plan["artifact_steps"]) <= 4096,
                "duplicate verification or missing inline steps")
    implementation = set(scope["implementation_paths"])
    protected = set(scope["protected_paths"])
    covered, steps, ids = set(), {}, []
    for step in plan["steps"]:
        _incomplete(isinstance(step, dict) and set(step) == {
            "id", "target", "capability", "approach", "inputs", "outputs", "failures",
            "depends_on", "verification_refs", "read_only"}, "incomplete inline function step")
        target = step["target"]
        _incomplete(isinstance(target, dict) and set(target) == {"path", "symbol", "operation"} and
                    isinstance(target["path"], str) and target["path"] in implementation and
                    isinstance(target["operation"], str) and
                    target["operation"] in {"add", "modify", "delete"} and
                    isinstance(target["symbol"], str) and 0 < len(target["symbol"].encode("utf-8")) <= 512 and
                    (bool(SYMBOL.fullmatch(target["symbol"])) if target["path"].endswith(".py")
                     else _non_python_symbol(target["symbol"])),
                    "inline function target escapes implementation scope")
        _incomplete(isinstance(step["id"], str) and IDENTIFIER.fullmatch(step["id"]) and
                    isinstance(step["capability"], str) and step["capability"].strip(),
                    "inline function identity or capability is incomplete")
        for field in ("approach", "inputs", "outputs", "failures"):
            _strings(step[field], field)
        if target["operation"] != "add":
            _incomplete(repository is not None,
                        "existing inline target needs its Git repository")
            existing = _committed_file(repository, scope["baseline"], target["path"])
            if target["path"].endswith(".py"):
                _incomplete(target["symbol"] in _python_symbols(existing["body"]),
                            "existing Python symbol is missing: " + target["symbol"] +
                            " (target: " + target["path"] + ")")
            else:
                _non_python_source_evidence(repository, scope["baseline"], target, step["approach"])
        _ids(step["depends_on"], "inline depends_on", empty=True)
        _ids(step["verification_refs"], "inline verification_refs")
        _strings(step["read_only"], "inline read_only", empty=True)
        _incomplete(set(step["verification_refs"]) <= set(verify_ids) and
                    set(step["read_only"]) <= protected | implementation,
                    "inline verification or read-only reference is invalid")
        ids.append(step["id"])
        steps[step["id"]] = step
        covered.add(target["path"])
    _incomplete(len(ids) == len(set(ids)), "duplicate inline function ID")
    for artifact in plan["artifact_steps"]:
        _incomplete(isinstance(artifact, dict) and set(artifact) ==
                    {"id", "path", "operation", "capability", "approach", "verification_refs"} and
                    isinstance(artifact["path"], str) and artifact["path"] in implementation and
                    isinstance(artifact["operation"], str) and
                    artifact["operation"] in {"add", "modify", "delete"} and
                    isinstance(artifact["id"], str) and IDENTIFIER.fullmatch(artifact["id"]) and
                    isinstance(artifact["capability"], str) and artifact["capability"].strip() and
                    isinstance(artifact["approach"], str) and artifact["approach"].strip(),
                    "incomplete inline artifact step")
        _ids(artifact["verification_refs"], "inline artifact verification_refs")
        _incomplete(set(artifact["verification_refs"]) <= set(verify_ids) and
                    artifact["id"] not in ids, "inline artifact verification or identity is invalid")
        ids.append(artifact["id"])
        steps[artifact["id"]] = artifact
        covered.add(artifact["path"])
    edges = {step_id: step.get("depends_on", []) for step_id, step in steps.items()}
    _incomplete(all(len(deps) <= len(steps) and set(deps) <= set(steps)
                    for deps in edges.values()),
                "unknown inline function dependency")
    ordered = _stable_dependency_order(ids, edges, "inline function/artifact")
    for item in scope["downstream"]["manifest"]:
        if item["owner"] == "implementation":
            _incomplete(item["path"] in covered or item["kind"] == "generated" and
                        any(source in covered for source in item["source_paths"]),
                        "inline plan lacks implementation path coverage: " + item["path"])
            _incomplete(not _code_source(item) or
                        any(step['target']['path'] == item['path'] for step in plan['steps']),
                        "inline code source requires a real function step: " + item['path'])
    contract = {key: semantic[key] for key in
                ("objective", "testing_basis", "completion_criteria", "constraints")}
    projection = {"brief": contract, "requirement_identity": requirement_identity,
                  "functional_contract": {"ticket_id": plan["ticket_id"],
                      "contract_refs": plan["contract_refs"],
                      "verifications": [{key: item[key] for key in ("id", "behavior", "seam")}
                                        for item in plan["verifications"]]}}
    record = {"ticket_id": plan["ticket_id"], "plan_version": plan["plan_version"],
              "source": {"kind": "inline", "commit": None, "sha256": entry.digest(plan),
                         "plan_version": plan["plan_version"]},
              "plan": copy_plan_refs([plan])[0], "covered_paths": sorted(covered)}
    catalog = {"schema_version": 1, "commit": None, "requirement_identity": requirement_identity,
               "tickets": [record], "ordered_step_ids": ordered,
               "contract_projection": projection, "contract_digest": entry.digest(projection)}
    catalog["plan_digest"] = entry.digest(catalog)
    return catalog


def inline_authority(plan, scope, semantic, requirement_identity, generated_outputs=None, *,
                     repository=None, original_scope_revision=None, input_digest=None):
    catalog = collect_inline_plan(plan, scope, semantic, requirement_identity, repository=repository)
    return {"kind": "inline", "state": "sealed", "revision": scope["downstream"]["revision"],
            "candidate_commit": scope["baseline"], "scope": copy_plan_refs([scope])[0],
            "execution_plan": authority_envelope(catalog, original_scope_revision or scope["downstream"]["revision"],
                                                 input_digest=input_digest),
            "generated_outputs": copy_plan_refs([generated_outputs or {}])[0]}
