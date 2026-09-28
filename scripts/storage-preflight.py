#!/usr/bin/env python3
"""Fail-closed Linux host preflight. Read-only: never creates a missing mount.
Usage: storage-preflight.py /etc/signage/storage.json
"""
import json
import os
import re
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web'))
from app.storage_guard import directory, marker, network_filesystem


def mount_source(fd):
    mid = next(line.split(':', 1)[1].strip() for line in Path(f'/proc/self/fdinfo/{fd}').read_text().splitlines() if line.startswith('mnt_id:'))
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        if line.split(' ', 1)[0] == mid:
            fields = line.split(' - ', 1)[1].split()
            return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[1])
    raise OSError('Mount source not found')


def check(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > 65536:
            raise ValueError('Invalid preflight configuration')
        entries = json.loads(os.read(fd, 65537))
    finally:
        os.close(fd)
    if not isinstance(entries, list) or not 1 <= len(entries) <= 32:
        raise ValueError('Configure 1–32 required storage directories')
    for entry in entries:
        if set(entry) != {'path', 'volume_id', 'source'}:
            raise ValueError('Each storage entry needs path, volume_id and exact mount source')
        with directory(Path(entry['path'])) as root:
            network_filesystem(root)
            if mount_source(root) != entry['source']:
                raise OSError('NAS mount source differs from approved configuration')
            marker(root, entry['volume_id'])
    return len(entries)

if __name__ == '__main__':
    try:
        if len(sys.argv) != 2:
            raise ValueError('Usage: storage-preflight.py /etc/signage/storage.json')
        count = check(sys.argv[1])
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        print('Storage preflight failed; no containers started: ' + str(exc), file=sys.stderr)
        sys.exit(1)
    print(f'Storage preflight passed for {count} required directories')
