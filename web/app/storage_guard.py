"""Persistent-storage identity checks. Never create or repair a missing mount."""
import os
import stat
from contextlib import contextmanager
from pathlib import Path

from . import config


@contextmanager
def directory(path: Path):
    """Open each component without symlinks; subsequent access is descriptor-relative."""
    if not path.is_absolute():
        path = path.absolute()
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                          dir_fd=fd)
            os.close(fd)
            fd = nxt
        yield fd
    finally:
        os.close(fd)


def marker(fd: int, expected: str) -> None:
    check = os.open(".signage-volume-id", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        st = os.fstat(check)
        if not stat.S_ISREG(st.st_mode) or not 0 < st.st_size <= 128:
            raise OSError("Storage marker is not a small regular file")
        if os.read(check, 129).decode("ascii").strip() != expected:
            raise OSError("Storage identity does not match configuration")
    finally:
        os.close(check)


def network_filesystem(fd: int) -> None:
    """Check the descriptor's mount, not a pathname that can change underneath us."""
    fields = Path(f"/proc/self/fdinfo/{fd}").read_text().splitlines()
    mid = next((line.split(":", 1)[1].strip() for line in fields if line.startswith("mnt_id:")), None)
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        if line.split(" ", 1)[0] == mid:
            fs = line.split(" - ", 1)[1].split(" ", 1)[0]
            if fs not in {"nfs", "nfs4", "cifs", "smb3"}:
                raise OSError("Expected an NFS or SMB mount, not a local directory")
            return
    raise OSError("Unable to verify the shared filesystem")


def verify_media() -> None:
    for path in (config.ORIGINALS_DIR, config.WORK_DIR, config.RENDERED_DIR):
        with directory(path) as fd:
            network_filesystem(fd)
            marker(fd, config.MEDIA_VOLUME_ID)
