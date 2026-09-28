#!/usr/bin/env python3
"""Install the upstream HLS player from a pinned, checksum-verified release.
Run at image build time, never when a display opens the player.
"""
import hashlib
import io
import json
import ssl
import sys
import urllib.request
import zipfile
from pathlib import Path

VERSION = '1.7.3'
URL = f'https://github.com/video-dev/hls.js/releases/download/v{VERSION}/release.zip'
SHA256 = 'abbda85f3e9b8325fc6b8dbf4fa612ea87144ebad93c2150594e64aad58ec529'
LIMIT = 16 * 1024 * 1024

def main():
    dest = Path(__file__).resolve().parent / 'app/static/vendor'
    # An offline build may provide the *same* release archive as argv[1].
    local_archive = Path(__file__).resolve().parent / 'hls-archive/release.zip'
    if len(sys.argv) > 1 or local_archive.exists():
        with open(sys.argv[1] if len(sys.argv) > 1 else local_archive, 'rb') as source:
            archive = source.read(LIMIT + 1)
    else:
        req = urllib.request.Request(URL, headers={'User-Agent': 'Signage-build/1'})
        with urllib.request.urlopen(req, timeout=60, context=ssl.create_default_context()) as response:
            if not response.url.startswith('https://'):
                raise RuntimeError('Insecure download redirect')
            archive = response.read(LIMIT + 1)
    if len(archive) > LIMIT or hashlib.sha256(archive).hexdigest() != SHA256:
        raise RuntimeError('HLS release checksum mismatch; nothing installed')
    # Read named files only. Never extract untrusted archive paths to disk.
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        names = z.namelist()
        candidates = [n for n in names if Path(n).name == 'hls.light.min.js']
        if len(candidates) != 1:
            raise RuntimeError('Expected one hls.light.min.js in pinned archive')
        entry = z.getinfo(candidates[0])
        if entry.file_size > 2 * 1024 * 1024:
            raise RuntimeError('Unexpected player size')
        player = z.read(entry)
        # Upstream build embeds its Apache-2.0 license banner. Preserve it verbatim.
        if b'Apache' not in player[:20000] and b'license' not in player[:20000].lower():
            raise RuntimeError('Expected upstream license notice')
        license_names = [n for n in names if Path(n).name in {'LICENSE', 'LICENSE.txt', 'LICENSE.md'}]
        dest.mkdir(parents=True, exist_ok=True)
        (dest / 'hls.min.js').write_bytes(player)
        if license_names:
            info = z.getinfo(license_names[0])
            if info.file_size < 100000:
                (dest / 'HLS-LICENSE.txt').write_bytes(z.read(info))
        (dest / 'HLS-PROVENANCE.json').write_text(json.dumps({
            'project': 'video-dev/hls.js', 'version': VERSION, 'url': URL,
            'archive_sha256': SHA256, 'player_sha256': hashlib.sha256(player).hexdigest(),
            'license': 'Apache-2.0', 'build': 'hls.light.min.js'
        }, indent=2) + '\n')
    print(f'Installed HLS.js {VERSION}; verified release SHA-256')

if __name__ == '__main__':
    main()
