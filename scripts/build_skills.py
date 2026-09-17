#!/usr/bin/env python3
"""Build the five fixed, self-contained releases from explicit resource roots."""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import sys
import sysconfig

ROOT = Path(__file__).resolve().parents[1]
MARKER = re.compile(r'\{\{resource:([^}]+)\}\}')
PACKAGES = ('design-discussion', 'problem-framing', 'solution-design', 'guided-implementation', 'change-closure')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def validate_files(files):
    """Check imports and links against the actual emitted resource closure."""
    standard = set(getattr(sys, 'stdlib_module_names', ())) | set(sys.builtin_module_names)
    standard.update(p.stem for p in Path(sysconfig.get_path('stdlib')).iterdir())
    standard.update(p.name.split('.')[0] for p in Path(sysconfig.get_config_var('DESTSHARED')).glob('*'))
    standard.update({'__future__', 'typing'})
    modules = {p[len('scripts/'):-3].replace('/', '.').removesuffix('.__init__')
        for p in files if p.startswith('scripts/') and p.endswith('.py')}
    for path, data in files.items():
        if Path(path).name.startswith('test_') or ('SKILL.md' in path and path != 'SKILL.md'):
            raise ValueError(f'non-release resource: {path}')
        if re.search(rb'/(?:Users|home)/[^/\s]+/', data):
            raise ValueError(f'development machine path: {path}')
        if path.endswith('.py'):
            tree = ast.parse(data, filename=path)
            module = path[len('scripts/'):-3].replace('/', '.')
            package = module.rsplit('.', 1)[0] if '.' in module else ''
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        parent = package.split('.')[:len(package.split('.')) - node.level + 1]
                        imports = ['.'.join(parent + ([node.module] if node.module else []))]
                    else:
                        imports = [node.module or '']
                elif isinstance(node, ast.Call) and (isinstance(node.func, ast.Name) and node.func.id == '__import__'
                    or isinstance(node.func, ast.Attribute) and node.func.attr in {'import_module','spec_from_file_location'}):
                    raise ValueError(f'undeclared dynamic import: {path}:{node.lineno}')
                else:
                    continue
                for imported in imports:
                    if imported not in modules and imported.split('.')[0] not in standard:
                        raise ValueError(f'undeclared import {imported}: {path}:{node.lineno}')
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'with_name' and node.args and isinstance(node.args[0], ast.Constant)):
                    target = node.args[0].value
                    if isinstance(target, str) and target.endswith(('.py', '.json')):
                        if (Path(path).parent / target).as_posix() not in files:
                            raise ValueError(f'undeclared runtime resource {target}: {path}')
        if path.endswith('.md'):
            text = data.decode()
            if '{{' in text or re.search(r'(?<![\w/])skills/(?:design-discussion|problem-framing|solution-design|guided-implementation|change-closure)/', text):
                raise ValueError(f'unresolved or cross-package resource: {path}')
            for link in re.findall(r'\]\(([^)]+)\)', text):
                if '://' in link or link.startswith('mailto:'):
                    continue
                relative, _, anchor = link.partition('#')
                resolved = os.path.normpath(str(Path(path).parent / relative)) if relative else path
                if resolved not in files:
                    raise ValueError(f'missing link {link}: {path}')
                if anchor:
                    headings = re.findall(r'^#+\s+(.+)$', files[resolved].decode(), re.MULTILINE)
                    slugs = {re.sub(r'[^\w -]', '', h.lower()).replace(' ', '-') for h in headings}
                    if anchor not in slugs:
                        raise ValueError(f'missing anchor {link}: {path}')
            for prefix, script in re.findall(r'((?:\.\./)*)(scripts/[\w./-]+\.(?:py|json))', text):
                resolved = os.path.normpath(str(Path(path).parent / (prefix + script))) if prefix else script
                if resolved not in files:
                    raise ValueError(f'missing command resource {prefix + script}: {path}')


def build(output, selected=None, root=ROOT):
    config = json.loads((root / 'build/skill-packages.json').read_text())
    if set(config['packages']) != set(PACKAGES):
        raise ValueError('release must declare exactly the five workflow packages')
    if any(config['packages'][name]['stage'] != stage for stage, name in enumerate(PACKAGES)):
        raise ValueError('package name and stage must match the fixed workflow mapping')
    resources = config['resources']
    runtime = ast.parse((root / 'src/shared/scripts/skill_preflight.py').read_text())
    actions = next(ast.literal_eval(node.value) for node in runtime.body
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'ACTIONS' for target in node.targets))
    for package in config['packages'].values():
        if package['external_actions'] != actions[package['stage']]:
            raise ValueError('declared external actions differ from preflight runtime')
    for name, package in config['packages'].items():
        if selected and name != selected:
            continue
        files = {}
        pending = list(package['roots'])
        visited = set()
        while pending:
            key = pending.pop()
            if key in visited:
                continue
            visited.add(key)
            if key not in resources:
                raise ValueError(f'undeclared resource: {key}')
            resource = resources[key]
            source = root / resource['source']
            if (any(part.is_symlink() for part in [source, *source.parents] if part != root and root in part.parents)
                or not source.is_file() or not source.resolve().is_relative_to(root.resolve())):
                raise ValueError(f'invalid resource: {key}')
            target = resource['output']
            if Path(target).is_absolute() or '..' in Path(target).parts:
                raise ValueError(f'escaping resource: {key}')
            data = source.read_bytes()
            if source.suffix in {'.md', '.in'}:
                text = data.decode('utf-8')
                def resolve(match):
                    dependency = match[1]
                    if dependency not in resources:
                        raise ValueError(f'undeclared resource: {dependency}')
                    if dependency.endswith('/SKILL.md'):
                        raise ValueError('stage entry is external, not a resource')
                    pending.append(dependency)
                    return os.path.relpath(resources[dependency]['output'], str(Path(target).parent))
                data = MARKER.sub(resolve, text).encode('utf-8')
            if b'\r' in data:
                raise ValueError(f'non-LF resource: {key}')
            if target in files and files[target] != data:
                raise ValueError(f'output collision: {target}')
            files[target] = data
        validate_files(files)
        if 'SKILL.md' not in files or not re.search(rb'^name:\s*' + name.encode() + rb'\s*$', files['SKILL.md'], re.MULTILINE):
            raise ValueError(f'package must contain its own named entry: {name}')
        table = {path: {'sha256': hashlib.sha256(data).hexdigest(), 'mode': '0644'} for path, data in sorted(files.items())}
        manifest = {key: config[key] for key in ('format_version','release','source_revision','compatibility_key')}
        manifest.update(name=name, stage=package['stage'], resources=sorted(visited), files=table,
            bundle_digest=hashlib.sha256(canonical(table)).hexdigest(), external_actions=package['external_actions'])
        files['package.json'] = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode() + b'\n'
        destination = output / name
        if destination.exists():
            shutil.rmtree(destination)
        for path, data in sorted(files.items()):
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o644)


def snapshot(path):
    if any(p.is_symlink() for p in path.rglob('*')):
        raise ValueError('generated package contains a symlink')
    return {str(p.relative_to(path)): (p.read_bytes(), p.stat().st_mode & 0o777)
        for p in path.rglob('*') if p.is_file() and '__pycache__' not in p.parts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'skills')
    parser.add_argument('--package', choices=PACKAGES)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.check:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            build(output, args.package)
            expected = snapshot(output)
            actual = snapshot(args.output / args.package) if args.package else snapshot(args.output)
            if args.package:
                expected = snapshot(output / args.package)
            if actual != expected:
                parser.exit(1, 'generated package drift; run scripts/build_skills.py\n')
    else:
        build(args.output, args.package)


if __name__ == '__main__':
    main()
