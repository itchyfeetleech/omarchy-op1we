#!/usr/bin/env python3
"""Allowlisted packaging and ownership-aware local development installation."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MARKER = '.op1we-dev.json'
PLUGIN_ID = 'hoppcx.op1we'


def runtime_files():
    fixed = ['manifest.json', 'README.md', 'LICENSE',
             'udev/70-op1we-control.rules', 'scripts/distribute.py',
             'scripts/install-dev.sh', 'scripts/uninstall-dev.sh', 'scripts/package.sh']
    files = fixed + [str(p.relative_to(ROOT)) for directory, pattern in (
        ('qml', '*.qml'), ('qml', '*.js'), ('backend/op1we', '*.py'),
        ('docs', '*.md'), ('docs', '*.png')) for p in sorted((ROOT / directory).glob(pattern))]
    for name in files:
        p = ROOT / name
        if not p.is_file() or p.is_symlink() or any(x.is_symlink() for x in p.parents):
            raise ValueError(f'Not a regular source file: {name}')
    return sorted(files)


def validate(path, portable=False):
    data = json.loads((path / 'manifest.json').read_text())
    if data['id'] != PLUGIN_ID or data['schemaVersion'] != 1:
        raise ValueError('Unexpected manifest identity/schema')
    if data['entryPoints']['barWidget'] != 'qml/BarWidget.qml':
        raise ValueError('Unexpected entry point')
    if any(c not in '0123456789.-abcdefghijklmnopqrstuvwxyz' for c in data['version']):
        raise ValueError('Unsafe version')
    for name in ('README.md', 'LICENSE', 'qml/BarWidget.qml', 'backend/op1we/__main__.py'):
        if not (path / name).is_file():
            raise ValueError(f'Missing runtime file: {name}')
    if not portable:
        for command in ('python3', 'qs', 'omarchy'):
            if not shutil.which(command):
                raise ValueError(f'Missing {command}; install on Omarchy 4 with Python 3 and Quickshell')
        subprocess.run(['omarchy', 'plugin', 'validate', str(path)], check=True)
    return data


def destination():
    base = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config')))
    dest = base / 'omarchy/plugins' / PLUGIN_ID
    # Never follow directory symlinks during install/removal.
    if any(p.is_symlink() for p in (dest, *dest.parents)):
        raise ValueError('Refusing a symlink in the installation path')
    return dest


def owned_files(dest):
    marker = dest / MARKER
    if not marker.is_file() or marker.is_symlink():
        raise ValueError(f'Refusing unmanaged installation: {dest}')
    data = json.loads(marker.read_text())
    if data.get('id') != PLUGIN_ID or not isinstance(data.get('files'), dict):
        raise ValueError('Invalid ownership record')
    for name, digest in data['files'].items():
        rel = Path(name)
        if rel.is_absolute() or '..' in rel.parts or name == MARKER:
            raise ValueError('Unsafe ownership path')
        p = dest / rel
        if any(x.is_symlink() for x in (p, *p.parents)):
            raise ValueError(f'Refusing symlink: {name}')
        if p.exists() and (not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != digest):
            raise ValueError(f'Locally modified file; preserve it before retrying: {p}')
    return data['files']


def generated_cache(path, dest, owned):
    """Recognize disposable Python caches of our own installed modules only."""
    if path.is_symlink() or path.parent.name != '__pycache__' or path.suffix != '.pyc':
        return False
    source = path.parent.parent / (path.name.split('.')[0] + '.py')
    return (str(source.relative_to(dest)) in owned
            and path.read_bytes()[:4] == importlib.util.MAGIC_NUMBER)


def install(portable):
    files = runtime_files()
    dest = destination()
    dest.parent.mkdir(parents=True, exist_ok=True)
    owned = owned_files(dest) if dest.exists() else {}
    if dest.exists():
        extras = [p for p in dest.rglob('*') if p.is_symlink() or
                  (p.is_file() and str(p.relative_to(dest)) not in {*owned, MARKER}
                   and not generated_cache(p, dest, owned))]
        if extras:
            raise ValueError(f'Preserve unexpected files before updating: {extras[0]}')
    with tempfile.TemporaryDirectory(prefix='.op1we-stage-', dir=dest.parent) as tmp:
        stage = Path(tmp) / PLUGIN_ID
        stage.mkdir()
        for name in files:
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        validate(stage, portable)
        record = {name: hashlib.sha256((stage / name).read_bytes()).hexdigest() for name in files}
        (stage / MARKER).write_text(json.dumps({'id': PLUGIN_ID, 'files': record}, indent=2))
        old = Path(tmp) / 'previous'
        if dest.exists():
            dest.rename(old)
        try:
            stage.rename(dest)
        except BaseException:
            if old.exists():
                old.rename(dest)
            raise
    print(f'Installed {dest}')
    print('Rescan: omarchy-shell shell rescanPlugins')
    print('Enable: omarchy plugin enable hoppcx.op1we --section right')
    print('No permissions, mouse settings or shell layout were changed.')


def uninstall():
    dest = destination()
    if not dest.exists():
        print('No development installation found')
        return
    owned = owned_files(dest)
    for name in owned:
        (dest / name).unlink(missing_ok=True)
    (dest / MARKER).unlink()
    for directory in sorted((p for p in dest.rglob('*') if p.is_dir() and not p.is_symlink()),
                            key=lambda p: len(p.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass
    try:
        dest.rmdir()
    except OSError:
        pass
    print('Removed owned development files; profiles, backups and unrelated files preserved.')


def package(portable):
    files = runtime_files()
    out = ROOT / 'dist'
    out.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='op1we-package-') as tmp:
        stage = Path(tmp) / PLUGIN_ID
        stage.mkdir()
        for name in files:
            p = stage / name
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, p)
        manifest = validate(stage, portable)
        archive = out / f"op1we-control-{manifest['version']}.tar.gz"
        with tarfile.open(archive, 'w:gz') as tar:
            for name in files:
                tar.add(stage / name, arcname=f'{PLUGIN_ID}/{name}', recursive=False)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = Path(str(archive) + '.sha256')
    checksum.write_text(f'{digest}  {archive.name}\n')
    print(archive)
    print(f'Verify: cd {out} && sha256sum -c {checksum.name}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install', 'uninstall', 'package'])
    parser.add_argument('--portable', action='store_true', help='CI only: skip native Omarchy dependency/validator checks')
    args = parser.parse_args()
    try:
        if args.action == 'install': install(args.portable)
        elif args.action == 'uninstall': uninstall()
        else: package(args.portable)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f'{args.action}: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
