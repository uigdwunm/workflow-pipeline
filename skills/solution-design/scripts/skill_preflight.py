#!/usr/bin/env python3
"""Read-only current-registry resolution and immutable workflow package identity.

The invoking host/controller authenticates the registry snapshot. Filesystem
candidates are diagnostics only; this CLI cannot attest host registration.
"""
from __future__ import annotations
import errno
import hashlib
import json
from pathlib import Path
import re
import sys

STAGES = ('design-discussion', 'problem-framing', 'solution-design', 'guided-implementation', 'change-closure')
ACTIONS = {
    0: {'discuss': []},
    1: {'route': ['ask-matt'], 'grill-with-docs': ['grill-with-docs', 'grilling', 'domain-modeling']},
    2: {'spec': ['to-spec'], 'decide-tickets': ['ask-matt'], 'tickets': ['to-tickets'], 'adr': ['domain-modeling']},
    3: {'dispatch': ['implement', 'tdd', 'code-review'], 'review': ['code-review']},
    4: {'route': ['ask-matt']},
}
MAX_BYTES = 2 * 1024 * 1024


class PreflightError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def entry_file(value):
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise PreflightError('invalid_entry', 'registered entry must be an absolute path')
    path = Path(value)
    try:
        actual = path.resolve(strict=True)
        if actual.name != 'SKILL.md' or not actual.is_file():
            raise PreflightError('invalid_entry', 'entry must resolve to a regular SKILL.md')
        actual.read_bytes()
        return actual
    except RuntimeError as exc:
        raise PreflightError('symlink_loop', str(exc)) from exc
    except OSError as exc:
        code = 'symlink_loop' if exc.errno == errno.ELOOP else 'unreadable' if isinstance(exc, PermissionError) else 'entry_unavailable'
        raise PreflightError(code, str(exc)) from exc


def skill_name(entry):
    try:
        text = entry.read_text(encoding='utf-8')
    except UnicodeError as exc:
        raise PreflightError('invalid_entry', 'entry is not UTF-8') from exc
    except OSError as exc:
        raise PreflightError('unreadable', 'entry cannot be read') from exc
    match = re.search(r'^name:\s*([a-z][a-z0-9-]*)\s*$', text, re.MULTILINE)
    if not match:
        raise PreflightError('invalid_entry', 'entry has no registered Skill name')
    return match[1]


def package_identity(value, name=None):
    entry = entry_file(value)
    root = entry.parent
    try:
        manifest_path = root / 'package.json'
        if not manifest_path.exists():
            raise PreflightError('incompatible_package', 'target has no self-contained package manifest')
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise PreflightError('invalid_package', 'manifest must be a package-local regular file')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if not isinstance(manifest, dict) or type(manifest.get('format_version')) is not int or manifest['format_version'] != 1:
            raise PreflightError('incompatible_package', 'unsupported package format')
        actual_name = skill_name(entry)
        if actual_name not in STAGES or manifest.get('name') != actual_name or name is not None and name != actual_name:
            raise PreflightError('invalid_entry', 'registered name, entry and manifest disagree')
        if type(manifest.get('stage')) is not int or manifest['stage'] != STAGES.index(actual_name):
            raise PreflightError('invalid_package', 'manifest stage does not match entry')
        table = manifest.get('files')
        if not isinstance(table, dict) or 'SKILL.md' not in table or 'package.json' in table:
            raise PreflightError('invalid_package', 'invalid resource table')
        if not isinstance(manifest.get('release'), str) or not manifest['release']:
            raise PreflightError('invalid_package', 'release must be a nonempty string')
        for relative, record in table.items():
            if (not isinstance(relative, str) or not relative or not isinstance(record, dict)
                or set(record) != {'sha256', 'mode'}
                or not isinstance(record['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', record['sha256'])
                or record['mode'] != '0644'):
                raise PreflightError('invalid_package', 'malformed resource record')
        actual_files = set()
        for path in root.rglob('*'):
            if path.is_symlink():
                raise PreflightError('invalid_package', 'package resources cannot be symlinks')
            if path.is_file() and '__pycache__' not in path.parts and path != manifest_path:
                actual_files.add(path.relative_to(root).as_posix())
        if actual_files != set(table):
            raise PreflightError('package_changed', 'resource file set changed')
        for relative, record in table.items():
            path = root / relative
            if Path(relative).is_absolute() or '..' in Path(relative).parts or not path.resolve().is_relative_to(root):
                raise PreflightError('invalid_package', 'resource escapes package root')
            content = path.read_bytes()
            if (hashlib.sha256(content).hexdigest() != record.get('sha256')
                or f'{path.stat().st_mode & 0o777:04o}' != record.get('mode')):
                raise PreflightError('package_changed', 'resource bytes or permissions changed', resource=relative)
        digest = hashlib.sha256(canonical(table)).hexdigest()
        if manifest.get('bundle_digest') != digest:
            raise PreflightError('package_changed', 'bundle digest differs from resource table')
        compatibility = manifest.get('compatibility_key')
        if not isinstance(compatibility, dict) or not compatibility:
            raise PreflightError('incompatible_package', 'missing protocol compatibility key')
        return {'name': actual_name, 'stage': STAGES.index(actual_name), 'entry': str(entry), 'root': str(root),
            'format_version': 1, 'release': manifest['release'], 'bundle_digest': digest, 'compatibility_key': compatibility}
    except FileNotFoundError as exc:
        raise PreflightError('package_unavailable', 'package manifest or resource is missing') from exc
    except PermissionError as exc:
        raise PreflightError('package_unavailable', 'package manifest or resource is unreadable') from exc
    except OSError as exc:
        raise PreflightError('package_unavailable', 'package resource cannot be opened') from exc
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise PreflightError('invalid_package', 'invalid package manifest or entry') from exc


def verify_identity(identity):
    if not isinstance(identity, dict):
        raise PreflightError('invalid_identity', 'fixed package identity required')
    try:
        current = package_identity(identity.get('entry'), identity.get('name'))
    except PreflightError as exc:
        if exc.code in {'entry_unavailable', 'unreadable', 'symlink_loop'}:
            raise PreflightError('package_unavailable', str(exc)) from exc
        raise
    if current != identity:
        raise PreflightError('package_changed', 'fixed package identity changed; retain the existing checkpoint')
    return current


def diagnostic_candidates(name, roots):
    found = {}
    for root in roots:
        if not isinstance(root, str) or not Path(root).is_absolute():
            raise PreflightError('invalid_request', 'diagnostic roots must be explicit absolute paths')
        candidate = Path(root) / name / 'SKILL.md'
        if not candidate.exists() and not candidate.is_symlink() and not candidate.parent.is_symlink():
            continue
        try:
            entry = entry_file(str(candidate))
            found[str(entry)] = {'entry': str(entry), 'status': 'readable', 'callable': False}
        except PreflightError as exc:
            found[str(candidate)] = {'entry': str(candidate), 'status': exc.code, 'callable': False}
    return list(found.values())


def resolve_skill(name, registry, roots):
    candidates = diagnostic_candidates(name, roots)
    details = {'required_skill': name, 'candidates': candidates,
        'recovery': 'Install or enable the named Skill, refresh the current task registry, and retry this action.'}
    if not isinstance(registry, dict) or registry.get('source') not in {'host-current-skills','controller-current-skills'}:
        raise PreflightError('registry_unavailable', 'current trusted host registry is required', **details)
    entries = registry.get('entries')
    if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
        raise PreflightError('invalid_registry', 'registry entries must be objects', **details)
    if any(not isinstance(e.get('name'), str) or not isinstance(e.get('source'), str)
        or not e['source'] or type(e.get('enabled', True)) is not bool for e in entries):
        raise PreflightError('invalid_registry', 'registry entry requires name, source and boolean enabled', **details)
    selected = [e for e in entries if e.get('name') == name and e.get('enabled', True)]
    if not selected:
        raise PreflightError('skill_not_active', 'Skill is not active in the current registry', **details)
    unique = {}
    for item in selected:
        try:
            path = entry_file(item.get('entry'))
            if skill_name(path) != name:
                raise PreflightError('invalid_entry', 'registered name differs from entry')
            unique[str(path)] = item
        except PreflightError as exc:
            raise PreflightError(exc.code, str(exc), registered_entry=item.get('entry'), **details) from exc
    if len(unique) != 1:
        raise PreflightError('registry_ambiguous', 'registry selected multiple distinct entries', **details)
    return next(iter(unique)), candidates


def preflight(request):
    if not isinstance(request, dict):
        raise PreflightError('invalid_request', 'one request object required')
    operation = request.get('operation', 'preflight')
    if operation == 'verify':
        return {'ok': True, 'identity': verify_identity(request.get('identity'))}
    stage = request.get('stage')
    if type(stage) is not int or stage not in range(5):
        raise PreflightError('invalid_request', 'stage must be 0 through 4')
    action = request.get('action')
    if not isinstance(action, str):
        raise PreflightError('invalid_action', 'action must be a string')
    selected = request.get('required_skills', [])
    if action == 'selected-capability':
        if not isinstance(selected,list) or not selected or any(not isinstance(n,str) or not re.fullmatch('[a-z][a-z0-9-]*',n) or n in STAGES for n in selected):
            raise PreflightError('invalid_request', 'selected external capability names required')
        required = selected
    elif action == 'setup':
        required = ['setup-matt-pocock-skills']
    elif action == 'entry':
        required = []
    elif action in ACTIONS[stage]:
        required = ACTIONS[stage][action]
    else:
        raise PreflightError('invalid_action', 'unknown stage action')
    targets = request.get('target_stages', [])
    if not isinstance(targets,list) or any(type(s) is not int or s not in range(5) for s in targets):
        raise PreflightError('invalid_request', 'target_stages must name explicit stages')
    registry = request.get('registry')
    roots = request.get('diagnostic_roots', [])
    if not isinstance(roots, list):
        raise PreflightError('invalid_request', 'diagnostic_roots must be a list')
    names = [STAGES[s] for s in dict.fromkeys([stage] + targets)] + required
    if operation == 'diagnose':
        return {'ok': True, 'evidence': 'filesystem-only', 'callable': False,
            'skills': {name: diagnostic_candidates(name, roots) for name in names}}
    if operation != 'preflight':
        raise PreflightError('invalid_request', 'unknown preflight operation')
    packages, external, diagnostics = {}, {}, {}
    for name in names:
        entry, candidates = resolve_skill(name, registry, roots)
        diagnostics[name] = candidates
        if name in STAGES:
            try:
                packages[name] = package_identity(entry, name)
            except PreflightError as exc:
                raise PreflightError(exc.code, str(exc), required_skill=name, registered_entry=entry,
                    candidates=candidates, **exc.details) from exc
        else:
            external[name] = {'entry':entry, 'callable':True, 'registry_source':registry['source']}
    own = packages[STAGES[stage]]
    for name, identity in packages.items():
        if identity['compatibility_key'] != own['compatibility_key']:
            raise PreflightError('incompatible_package', 'target protocol compatibility key differs', required_skill=name)
    return {'ok': True, 'packages':packages, 'external':external, 'diagnostics':diagnostics,
        'registry_source':registry['source']}


def main():
    request = {}
    try:
        data = sys.stdin.buffer.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise PreflightError('invalid_request', 'request exceeds limit')
        request = json.loads(data)
        result = preflight(request)
    except (PreflightError, json.JSONDecodeError, UnicodeError) as exc:
        details = exc.details if isinstance(exc, PreflightError) else {}
        result = {'ok':False, 'error': {'code':getattr(exc,'code','invalid_request'), 'message':str(exc),
            'stage':request.get('stage') if isinstance(request,dict) else None,
            'action':request.get('action') if isinstance(request,dict) else None,
            'recovery':'Retain the current checkpoint; restore the original package or refresh the current registry and retry the first unfinished action.', **details}}
    print(json.dumps(result,ensure_ascii=False,sort_keys=True))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
