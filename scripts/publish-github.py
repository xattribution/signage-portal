#!/usr/bin/env python3
"""Publish an unmodified release package to a NEW private GitHub repository.

Uses the locally authenticated GitHub CLI. Never accepts tokens, publishes a
public repository, force-pushes, or stages files outside MANIFEST.sha256.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {'.dockerignore', '.env.example', '.gitattributes', '.gitignore', 'README.md',
              'CHANGELOG.md', 'CONTRIBUTING.md', 'SECURITY.md', 'THIRD_PARTY.md',
              'pyproject.toml', 'requirements-dev.txt', 'docker-compose.yml', 'docker-compose.nas.yml'}
SOURCE_DIRS = {'web', 'worker', 'scripts', 'tests', 'docs', 'deploy', '.github'}
FORBIDDEN_PARTS = {'.git', '.venv', '__pycache__', 'node_modules', 'data', 'spool',
                   'originals', 'rendered', 'backups', '.ssh', 'release-evidence'}


def package_files(root: Path) -> list[str]:
    """Check the exact packaged tree, including all parent paths, before staging."""
    manifest = root / 'MANIFEST.sha256'
    if manifest.is_symlink():
        raise ValueError('MANIFEST.sha256 must not be a symlink.')
    paths: list[str] = []
    for line in manifest.read_text(encoding='utf-8').splitlines():
        match = re.fullmatch(r'([a-f0-9]{64})  (.+)', line)
        if not match:
            raise ValueError('Invalid MANIFEST.sha256 line.')
        digest, name = match.groups()
        rel = PurePosixPath(name)
        if rel.is_absolute() or '..' in rel.parts or '\\' in name or str(rel) != name:
            raise ValueError(f'Unsafe package path: {name}')
        if name in paths:
            raise ValueError(f'Duplicate package path: {name}')
        if name not in ROOT_FILES and (len(rel.parts) < 2 or rel.parts[0] not in SOURCE_DIRS):
            raise ValueError(f'Not a source/documentation path: {name}')
        if any(part.lower() in FORBIDDEN_PARTS for part in rel.parts):
            raise ValueError(f'Runtime or private path rejected: {name}')
        if (rel.name.startswith('.env') and rel.name != '.env.example') or rel.name == '.secret_key':
            raise ValueError(f'Private configuration rejected: {name}')
        if re.search(r'\.(?:pem|key|p12|pfx|db(?:-\w+)?|sqlite(?:3)?|credentials)$', name, re.I):
            raise ValueError(f'Private state/key file rejected: {name}')
        path = root
        for part in rel.parts:
            path /= part
            if path.is_symlink():
                raise ValueError(f'Symlink rejected: {name}')
        if not path.is_file() or path.stat().st_size > 25 * 1024 * 1024:
            raise ValueError(f'Missing or unexpectedly large packaged file: {name}')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f'Package changed: {name}. Publish from a fresh extracted package.')
        paths.append(name)
    if 'README.md' not in paths or not paths:
        raise ValueError('The package must include README.md.')
    return sorted(paths) + ['MANIFEST.sha256']


def run(args: list[str], *, root: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, GH_HOST='github.com', GH_PROMPT_DISABLED='1', GIT_TERMINAL_PROMPT='0')
    result = subprocess.run(args, cwd=root, env=env, text=True, encoding='utf-8', errors='replace',
                            capture_output=True, timeout=300, check=False)
    if check and result.returncode:
        # No credentials are passed as command arguments or printed by this tool.
        raise RuntimeError(f'{args[0]} {args[1]} failed:\n{result.stderr.strip() or result.stdout.strip()}')
    return result


def git(root: Path, *args: str) -> str:
    return run(['git', *args], root=root).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default='xattribution/signage-portal', metavar='OWNER/NAME')
    parser.add_argument('--check', action='store_true', help='Verify packaged files only; no GitHub access or writes.')
    parser.add_argument('--resume', action='store_true', help='Resume an interrupted first publish to an empty private repo.')
    args = parser.parse_args()
    files = package_files(ROOT)
    print(f'Package verified: {len(files)} files. Runtime files are not selected for upload.')
    if args.check:
        return 0
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', args.repo):
        raise ValueError('Use OWNER/NAME, not a URL or arbitrary path.')
    for executable in ('git', 'gh'):
        if not shutil.which(executable):
            raise RuntimeError(f'Install {executable} first; see docs/GITHUB.md.')
    run(['gh', 'auth', 'status', '--hostname', 'github.com'], root=ROOT)
    profile = json.loads(run(['gh', 'api', '--hostname', 'github.com', 'user'], root=ROOT).stdout)
    owner = args.repo.split('/')[0]
    if profile['login'].lower() != owner.lower():
        raise ValueError(f'Authenticated as {profile["login"]}, not {owner}. Select the correct account with gh auth switch.')

    marker = ROOT / '.git/signage-initial-publish.json'
    if (ROOT / '.git').exists():
        if not args.resume or not marker.is_file() or json.loads(marker.read_text()) != {'repo': args.repo}:
            raise ValueError('Use a fresh extraction, or --resume only for this script\'s interrupted initial publish.')
        if git(ROOT, 'rev-parse', '--show-toplevel').replace('\\', '/') != ROOT.as_posix():
            raise ValueError('Refusing to publish a different working tree.')
    elif run(['git', 'rev-parse', '--show-toplevel'], root=ROOT, check=False).returncode == 0:
        raise ValueError('Extract outside any existing Git working tree.')

    if not args.resume:
        # Creation failure is a hard stop. Never reuse, overwrite or delete an existing repository.
        run(['gh', 'repo', 'create', args.repo, '--private', '--disable-wiki', '--description',
             'Self-hosted office signage: persistent streams, overlays, media ingest and NAS library.'], root=ROOT)
    meta = json.loads(run(['gh', 'api', '--hostname', 'github.com', f'repos/{args.repo}'], root=ROOT).stdout)
    if not meta['private'] or meta['full_name'].lower() != args.repo.lower():
        raise ValueError('Refusing to upload unless the intended repository is private.')
    remote = f'https://github.com/{args.repo}.git'
    # Use gh for HTTPS credentials for THIS command, without editing global Git config.
    transport = ['git', '-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential']
    refs = run(transport + ['ls-remote', '--heads', '--tags', remote], root=ROOT).stdout.strip()
    if refs:
        if not marker.is_file() or refs.splitlines() != [f'{git(ROOT, "rev-parse", "HEAD")}\trefs/heads/main']:
            raise ValueError('Remote contains commits/refs. Refusing to overwrite or merge an existing repository.')
        print(f'Already published: https://github.com/{args.repo}')
        return 0
    if not (ROOT / '.git').exists():
        git(ROOT, 'init', '-b', 'main')
        marker.write_text(json.dumps({'repo': args.repo}), encoding='utf-8')
    for i in range(0, len(files), 40):
        git(ROOT, 'add', '--', *files[i:i+40])
    # Windows extraction does not preserve executable bits. Record them explicitly.
    for name in ('scripts/check.sh', 'scripts/release-check.sh', 'scripts/publish-github.py'):
        if name in files:
            git(ROOT, 'update-index', '--chmod=+x', '--', name)
    staged = set(git(ROOT, 'ls-files').splitlines())
    if staged != set(files):
        raise ValueError('The Git index contains files outside the verified package.')
    if run(['git', 'rev-parse', '--verify', 'HEAD'], root=ROOT, check=False).returncode:
        git(ROOT, '-c', f'user.name={profile["login"]}', '-c',
            f'user.email={profile["id"]}+{profile["login"]}@users.noreply.github.com',
            'commit', '-m', 'Initial signage portal import: streams, NAS, overlays and workspace UI')
    elif git(ROOT, 'status', '--porcelain', '--untracked-files=no'):
        raise ValueError('The initial commit changed. Inspect it before publishing manually.')
    origin = run(['git', 'remote', 'get-url', 'origin'], root=ROOT, check=False)
    if origin.returncode:
        git(ROOT, 'remote', 'add', 'origin', remote)
    elif origin.stdout.strip() != remote:
        raise ValueError('origin points to a different repository.')
    run(transport + ['push', '--set-upstream', 'origin', 'main'], root=ROOT)
    print(f'Published private repository: https://github.com/{args.repo}')
    print('GitHub stores the source. Install the running portal on the office Docker host; see docs/INSTALL.md.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired, KeyError) as exc:
        print(f'Publish stopped: {exc}', file=sys.stderr)
        print('No force-push, repository deletion, or deployment was attempted. See docs/GITHUB.md for recovery.', file=sys.stderr)
        sys.exit(1)
