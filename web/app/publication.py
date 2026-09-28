"""Atomic no-overwrite export publication on Linux NAS mounts."""
import ctypes
import errno
import os


def publish(folder: int, temporary: str, final: str) -> None:
    # renameat2(RENAME_NOREPLACE) works without relying on hard-link support.
    # If the kernel/filesystem cannot provide it, a same-directory hard link is
    # also atomic and refuses replacement. Never fall back to overwriting rename.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is not None:
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(folder, os.fsencode(temporary), folder, os.fsencode(final), 1) == 0:
            return
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise FileExistsError(error, 'Destination exists')
        if error not in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP, errno.EXDEV}:
            raise OSError(error, 'Atomic export publication failed')
    os.link(temporary, final, src_dir_fd=folder, dst_dir_fd=folder, follow_symlinks=False)
    os.unlink(temporary, dir_fd=folder)
