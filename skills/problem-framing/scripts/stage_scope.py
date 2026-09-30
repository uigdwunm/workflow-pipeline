#!/usr/bin/env python3
"""Read-only validation of a stage's exact downstream file authority.

The module describes and checks scope. It never persists a decision, grants a
writer lease, publishes a commit, or treats a manifest as a semantic approval.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
import re
import sys
import unicodedata

sys.path.insert(0, str(Path(__file__).resolve().parent))
import entry_prepare as entry


OWNERS = {"implementation", "closure"}
KINDS = {"source", "test", "generated", "document"}
OPERATIONS = {"add", "modify", "delete"}
STATES = {"unresolved", "proposed", "sealed"}
DOWNSTREAM_FIELDS = {"state", "revision", "implementation_required", "manifest", "proposal_ref", "seal_ref"}
SCOPE_FIELDS = {"baseline", "owned_paths", "protected_paths", "implementation_paths", "closure_paths", "downstream"}
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
GLOB_CHARS = set("*?[]{}")


def require(condition, code, message):
    entry.require(condition, code, message)


def _alias(path):
    return unicodedata.normalize("NFC", path).casefold()


def _commit(value):
    require(isinstance(value, str) and COMMIT_RE.fullmatch(value), "invalid_commit", "full Git commit required")
    return value


def _repository(root):
    require(isinstance(root, (str, Path)), "invalid_scope", "repository path required")
    root = Path(root)
    require(root.is_absolute() and root.is_dir() and not root.is_symlink(), "invalid_scope", "absolute repository required")
    facts = entry.repository_facts(str(root))
    require(facts["kind"] == "git" and facts["root"] == str(root.resolve()),
            "invalid_scope", "scope root must be the Git worktree root")
    return root


def validate_path(relative, repository):
    """Require one literal POSIX file path with no filesystem alias or link."""
    return _validate_path(relative, _repository(repository))


def _validate_path(relative, root):
    require(isinstance(relative, str) and relative and len(relative.encode("utf-8")) <= 4096,
            "invalid_scope", "bounded file path required")
    parts = relative.split("/")
    require(not relative.startswith("/") and not re.match(r"^[A-Za-z]:", relative) and
            "\\" not in relative and "\0" not in relative and
            all(part not in {"", ".", ".."} and _alias(part) != ".git" and
                not any(char in GLOB_CHARS for char in part) for part in parts),
            "invalid_scope", "exact repository-relative POSIX file path required")
    current = root
    for index, part in enumerate(parts):
        require(not current.is_symlink(), "invalid_scope", "scope path traverses a symlink")
        if current.is_dir():
            aliases = [name for name in current.iterdir() if _alias(name.name) == _alias(part)]
            require(len(aliases) <= 1 and (not aliases or aliases[0].name == part),
                    "invalid_scope", "scope path aliases another filesystem entry")
        current = current / part
        require(not current.is_symlink(), "invalid_scope", "scope path contains a symlink")
        if index < len(parts) - 1 and current.exists():
            require(current.is_dir(), "invalid_scope", "scope path has a non-directory ancestor")
    require(not current.exists() or current.is_file(), "invalid_scope", "scope path must name a regular file")
    require(current.resolve().is_relative_to(root.resolve()), "invalid_scope", "scope path escapes repository")
    return relative


def validate_paths(paths, repository, *, nullable=False):
    return _validate_paths(paths, _repository(repository), nullable=nullable)


def _validate_paths(paths, root, *, nullable=False):
    if paths is None and nullable:
        return None
    require(isinstance(paths, list) and len(paths) <= 4096, "invalid_scope", "bounded file list required")
    for path in paths:
        _validate_path(path, root)
    require(paths == sorted(paths), "invalid_scope", "file paths must be sorted")
    aliases = [_alias(path) for path in paths]
    require(len(aliases) == len(set(aliases)), "invalid_scope", "duplicate or aliased file paths")
    _validate_prefix_aliases(paths)
    return copy.deepcopy(paths)


def _validate_prefix_aliases(paths):
    """Catch aliases in absent ancestor directories as well as leaf names."""
    prefixes, files, ancestors = {}, set(), set()
    for path in paths:
        parts = path.split("/")
        for depth in range(1, len(parts) + 1):
            literal = tuple(parts[:depth])
            alias = tuple(_alias(part) for part in literal)
            previous = prefixes.setdefault(alias, literal)
            require(previous == literal, "invalid_scope", "scope paths alias a directory component")
            (files if depth == len(parts) else ancestors).add(alias)
    require(not files & ancestors, "invalid_scope", "scope file is an ancestor of another file")


def _tree_entries(repository, revision, paths):
    """Read exact paths in bounded batches; never cache across validations."""
    revision = _commit(revision)
    entries, batch, size = {}, [], 0

    def read_batch():
        for record in entry.git(repository, "ls-tree", "-z", revision, "--", *batch).stdout.split(b"\0"):
            if not record:
                continue
            header, separator, raw_path = record.partition(b"\t")
            require(separator == b"\t", "invalid_scope", "invalid committed tree entry")
            relative = os.fsdecode(raw_path)
            require(relative in batch and relative not in entries,
                    "invalid_scope", "committed tree returned an unexpected path")
            entries[relative] = header

    for relative in sorted(set(paths)):
        length = len(relative.encode("utf-8")) + 1
        if batch and (len(batch) == 128 or size + length > 24000):
            read_batch()
            batch, size = [], 0
        batch.append(relative)
        size += length
    if batch:
        read_batch()
    return entries


def manifest_projection(manifest):
    """Derive the only authoritative owner projections from a manifest."""
    require(isinstance(manifest, list) and len(manifest) <= 4096, "invalid_scope", "bounded manifest required")
    paths = {owner: [] for owner in OWNERS}
    seen = set()
    for item in manifest:
        require(isinstance(item, dict) and set(item) ==
                ({"path", "owner", "kind", "operation", "basis", "source_paths"} if item.get("kind") == "generated"
                 else {"path", "owner", "kind", "operation", "basis"}),
                "invalid_scope", "manifest item has missing or unknown fields")
        relative, owner = item["path"], item["owner"]
        require(isinstance(relative, str) and relative and isinstance(owner, str) and owner in OWNERS and
                isinstance(item["kind"], str) and item["kind"] in KINDS and
                isinstance(item["operation"], str) and item["operation"] in OPERATIONS and
                isinstance(item["basis"], dict),
                "invalid_scope", "invalid manifest ownership, kind, or operation")
        require(item["kind"] != "document" or owner == "closure", "invalid_scope",
                "ordinary documentation belongs to closure: " + relative)
        require(relative not in seen, "invalid_scope", "manifest path has multiple owners")
        seen.add(relative)
        paths[owner].append(relative)
    require([item["path"] for item in manifest] == sorted(seen),
            "invalid_scope", "manifest must be sorted by unique path")
    return {"implementation_paths": paths["implementation"], "closure_paths": paths["closure"]}


def validate_manifest(manifest, repository, candidate_commit=None, *, baseline=None,
                      protected_paths=(), planning_paths=(), requirement_path=None,
                      generated_outputs=None):
    """Check declared files, committed bases, and generated source closure.

    ``generated_outputs`` is an optional source-path-to-output-path inventory
    supplied by the package builder. It lets a caller prove that every declared
    generated output of changed sources appears in the manifest.
    """
    root = _repository(repository)
    projections = manifest_projection(manifest)
    protected = set(_validate_paths(sorted(protected_paths), root))
    planning = set(_validate_paths(sorted(planning_paths), root))
    if requirement_path is not None:
        _validate_path(requirement_path, root)
        protected.add(requirement_path)
    declared = {item["path"]: item for item in manifest}
    _validate_prefix_aliases([*declared, *protected, *planning])
    # Planning/closure overlap is intentional; all other aliases must be exact.
    for path in declared:
        _validate_path(path, root)
        require(path not in protected or
                (path in planning and declared[path]["owner"] == "closure" and path != requirement_path),
                "invalid_scope", "manifest changes a protected file: " + path)
        if path in planning:
            require(declared[path]["owner"] == "closure", "invalid_scope",
                    "only closure may own a planning file: " + path)
    require(len({_alias(path) for path in declared}) == len(declared),
            "invalid_scope", "manifest has filesystem aliases")
    for path in protected | planning:
        require(not any(_alias(path) == _alias(item) and path != item for item in declared),
                "invalid_scope", "manifest aliases a protected or planning file")
    baseline_entries = _tree_entries(root, baseline, declared) if baseline is not None else {}
    basis_sections, undeclared_sources = {}, set()
    for item in manifest:
        relative = item["path"]
        basis = item["basis"]
        entry.fields(basis, {"commit", "path", "section"})
        revision = _commit(basis["commit"])
        basis_path = _validate_path(basis["path"], root)
        section = entry.nonempty(basis["section"])
        require(len(section) <= 512, "invalid_scope", "basis section is too long")
        basis_sections.setdefault(revision, {}).setdefault(basis_path, set()).add(section)
        allowed_basis = planning | ({requirement_path} if requirement_path else set())
        if allowed_basis:
            require(basis_path in allowed_basis, "invalid_scope",
                    "basis is outside approved planning or requirement documents")
        if baseline is not None:
            tree = baseline_entries.get(relative)
            exists = tree is not None
            if exists:
                require(tree.startswith((b"100644 blob ", b"100755 blob ")),
                        "invalid_scope", "baseline path is not a regular file")
            require(item["operation"] == "add" if not exists else item["operation"] != "add",
                    "invalid_scope", "manifest operation differs from baseline: " + relative)
        if item["kind"] == "generated":
            sources = item["source_paths"]
            require(isinstance(sources, list) and bool(sources), "invalid_scope", "generated output needs source paths")
            _validate_paths(sources, root)
            require(all(source not in declared or declared[source]["kind"] != "generated" for source in sources),
                    "invalid_scope", "generated outputs cannot serve as source paths")
            require(all(source not in declared or declared[source]["owner"] == item["owner"] for source in sources),
                    "invalid_scope", "generated output and sources must share an owner")
            for source in sources:
                if source not in declared:
                    undeclared_sources.add(source)
    if candidate_commit is not None:
        candidate_commit = _commit(candidate_commit)
    for revision, paths in basis_sections.items():
        if candidate_commit is not None:
            ancestor = entry.git(root, "merge-base", "--is-ancestor", revision, candidate_commit, check=False)
            require(ancestor.returncode == 0, "invalid_scope", "basis commit is not in planning ancestry")
        committed = _tree_entries(root, revision, paths)
        for relative, sections in paths.items():
            tree = committed.get(relative)
            require(tree is not None and tree.startswith((b"100644 blob ", b"100755 blob ")),
                    "invalid_scope", "basis must be a committed regular file")
            data = entry.git(root, "show", revision + ":" + relative).stdout
            require(all(section.encode("utf-8") in data for section in sections),
                    "invalid_scope", "basis section is absent from committed text")
    if undeclared_sources:
        revision = candidate_commit or baseline
        require(revision is not None, "invalid_scope", "undeclared generated source needs a committed base")
        committed = _tree_entries(root, revision, undeclared_sources)
        require(all(committed.get(source, b"").startswith((b"100644 blob ", b"100755 blob "))
                    for source in undeclared_sources), "invalid_scope",
                "undeclared generated source must be a committed regular file")
    if generated_outputs is not None:
        require(isinstance(generated_outputs, dict), "invalid_scope", "generated output inventory required")
        expected = set()
        mapped_sources = {}
        for source, outputs in generated_outputs.items():
            _validate_path(source, root)
            require(isinstance(outputs, list), "invalid_scope", "generated output inventory must use lists")
            _validate_paths(outputs, root)
            if source in declared:
                expected.update(outputs)
            for output in outputs:
                mapped_sources.setdefault(output, set()).add(source)
        _validate_prefix_aliases([*generated_outputs, *(output for outputs in generated_outputs.values()
                                                    for output in outputs), *declared])
        actual = {item["path"] for item in manifest if item["kind"] == "generated"}
        require(expected <= actual, "scope_incomplete",
                "generated outputs missing: " + ", ".join(sorted(expected - actual)))
        for item in manifest:
            if item["kind"] == "generated":
                require(mapped_sources.get(item["path"], set()) == set(item["source_paths"]),
                        "invalid_scope", "generated output source mapping differs: " + item["path"])
    return projections


def validate_scope(scope, repository, *, stage=None, requirement_path=None,
                   planning_paths=None, candidate_commit=None, manifest_baseline=None,
                   generated_outputs=None):
    """Validate a complete lifecycle scope and return an independent copy."""
    root = _repository(repository)
    entry.fields(scope, SCOPE_FIELDS)
    _commit(scope["baseline"])
    owned = _validate_paths(scope["owned_paths"], root)
    protected = _validate_paths(scope["protected_paths"], root)
    if requirement_path is not None:
        require(requirement_path in protected, "invalid_scope", "frozen requirement must stay protected")
    _validate_prefix_aliases([*owned, *protected])
    require(not set(owned) & set(protected), "invalid_scope", "planning writes overlap protected files")
    downstream = scope["downstream"]
    entry.fields(downstream, DOWNSTREAM_FIELDS)
    state = downstream["state"]
    revision = downstream["revision"]
    require(isinstance(state, str) and state in STATES and type(revision) is int and revision >= 0,
            "invalid_scope", "invalid downstream state or revision")
    if state == "unresolved":
        require(revision == 0 and all(downstream[key] is None for key in
                ("implementation_required", "manifest", "proposal_ref", "seal_ref")) and
                scope["implementation_paths"] is None and scope["closure_paths"] is None,
                "invalid_scope", "unresolved downstream scope must use null, not empty lists")
        require(stage in (None, 2), "scope_incomplete", "downstream scope is not sealed")
    else:
        require(type(downstream["implementation_required"]) is bool and
                isinstance(downstream["manifest"], list),
                "invalid_scope", "proposed or sealed scope requires an explicit manifest")
        entry.nonempty(downstream["proposal_ref"])
        require((state == "proposed" and downstream["seal_ref"] is None) or
                (state == "sealed" and revision >= 1 and isinstance(downstream["seal_ref"], str)
                 and bool(downstream["seal_ref"])),
                "invalid_scope", "scope seal reference or revision is missing")
        # A standalone Stage 3 has no planning candidate. Its frozen input must
        # still be in the ancestry of the scope baseline; inherited stages pass
        # the original planning candidate explicitly when available.
        basis_candidate = candidate_commit
        if basis_candidate is None and stage == 3 and planning_paths is None:
            basis_candidate = scope["baseline"]
        projected = validate_manifest(downstream["manifest"], root, basis_candidate,
                                      baseline=manifest_baseline or scope["baseline"], protected_paths=protected,
                                      planning_paths=(owned if stage in (None, 2) else ()) if planning_paths is None
                                      else planning_paths,
                                      requirement_path=requirement_path,
                                      generated_outputs=generated_outputs)
        for field in ("implementation_paths", "closure_paths"):
            _validate_paths(scope[field], root)
            require(scope[field] == projected[field], "invalid_scope", "scope projection differs from manifest")
        required = downstream["implementation_required"]
        require(required == bool(projected["implementation_paths"]), "scope_incomplete",
                "implementation_required disagrees with implementation paths")
        require(required or not projected["closure_paths"], "scope_incomplete",
                "planning-only cannot leave closure work; return pending documents to the Controller")
        if stage in (3, 4):
            require(state == "sealed", "scope_incomplete", "downstream scope must be sealed")
    return copy.deepcopy(scope)


def manifest_diff(old, new):
    """Return a deterministic item-level difference for a revision decision."""
    manifest_projection(old)
    manifest_projection(new)
    before = {item["path"]: item for item in old}
    after = {item["path"]: item for item in new}
    return {"added": [copy.deepcopy(after[path]) for path in sorted(after.keys() - before.keys())],
            "removed": [copy.deepcopy(before[path]) for path in sorted(before.keys() - after.keys())],
            "changed": [{"before": copy.deepcopy(before[path]), "after": copy.deepcopy(after[path])}
                        for path in sorted(before.keys() & after.keys()) if before[path] != after[path]]}


def validate_snapshot(expected, actual):
    """Compare a stopped writer's exact Git/index/file snapshot, without I/O."""
    require(isinstance(expected, dict) and isinstance(actual, dict) and expected == actual,
            "scope_changed", "stopped Git snapshot changed before scope activation")
    return copy.deepcopy(actual)
