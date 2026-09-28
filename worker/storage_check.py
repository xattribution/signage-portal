"""Worker-side boot guard; no dependency on web credentials or its database."""
import os
import stat
from pathlib import Path


def verify():
    if os.getenv('REQUIRE_MEDIA_MARKERS', 'false').lower() not in {'true', '1'}:
        return
    expected = os.environ.get('MEDIA_VOLUME_ID', '')
    if len(expected) < 16:
        raise RuntimeError('Configure the persistent media volume identity')
    for path in [Path(os.getenv('ORIGINALS_DIR', '/media/originals')), Path(os.getenv('WORK_DIR', '/media/work'))]:
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in path.absolute().parts[1:]:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd); fd = nxt
            mid = next(line.split(':',1)[1].strip() for line in Path(f'/proc/self/fdinfo/{fd}').read_text().splitlines() if line.startswith('mnt_id:'))
            record = next(line for line in Path('/proc/self/mountinfo').read_text().splitlines() if line.split(' ',1)[0] == mid)
            if record.split(' - ',1)[1].split(' ',1)[0] not in {'nfs','nfs4','cifs','smb3'}:
                raise OSError('Expected mounted NAS, not a local fallback directory')
            marker = os.open('.signage-volume-id', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                st = os.fstat(marker)
                if not stat.S_ISREG(st.st_mode) or not 0 < st.st_size <= 128 or os.read(marker,129).decode('ascii').strip() != expected:
                    raise OSError('NAS identity check failed')
            finally:
                os.close(marker)
        finally:
            os.close(fd)
