"""Local initial-publish safety checks. These tests never contact GitHub."""
import hashlib
import importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('publish_github', ROOT / 'scripts/publish-github.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


def write_manifest(root, names):
    lines = [f'{hashlib.sha256((root / name).read_bytes()).hexdigest()}  {name}' for name in names]
    (root / 'MANIFEST.sha256').write_text('\n'.join(lines) + '\n')


def test_publish_selects_only_verified_package(tmp_path):
    (tmp_path / 'README.md').write_text('Source project')
    (tmp_path / '.env').write_text('ADMIN_PASSWORD=not-to-be-staged')
    write_manifest(tmp_path, ['README.md'])
    assert publisher.package_files(tmp_path) == ['README.md', 'MANIFEST.sha256']


@pytest.mark.parametrize('name', ['.env', 'docs/secret.pem', 'web/.env.production', 'web/data/session.db'])
def test_publish_rejects_sensitive_paths_even_in_manifest(tmp_path, name):
    (tmp_path / 'README.md').write_text('Source project')
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('private')
    write_manifest(tmp_path, ['README.md', name])
    with pytest.raises(ValueError):
        publisher.package_files(tmp_path)


def test_publish_detects_changes(tmp_path):
    (tmp_path / 'README.md').write_text('Original')
    write_manifest(tmp_path, ['README.md'])
    (tmp_path / 'README.md').write_text('Changed')
    with pytest.raises(ValueError, match='Package changed'):
        publisher.package_files(tmp_path)


def test_publish_rejects_symlinks(tmp_path):
    target = tmp_path / 'real'
    target.write_text('Original')
    (tmp_path / 'README.md').symlink_to(target)
    write_manifest(tmp_path, ['README.md'])
    with pytest.raises(ValueError, match='Symlink'):
        publisher.package_files(tmp_path)


def test_publish_rejects_duplicate_entries(tmp_path):
    (tmp_path / 'README.md').write_text('Original')
    write_manifest(tmp_path, ['README.md', 'README.md'])
    with pytest.raises(ValueError, match='Duplicate'):
        publisher.package_files(tmp_path)


def test_publish_rejects_traversal(tmp_path):
    (tmp_path / 'MANIFEST.sha256').write_text('0' * 64 + '  ../README.md\n')
    with pytest.raises(ValueError, match='Unsafe'):
        publisher.package_files(tmp_path)
