"""Advisory same-host resource locks; no GPU discovery or inference dependencies."""

from contextlib import contextmanager
import errno
import math
import os
from pathlib import Path
import stat
import sys
import time


def _cuda_lock_path():
    """One key per user/cache, independent of catalog and visible CUDA devices.

    The cache must be local and shared by cooperating StoryLens processes.
    Different users/cache roots, remote hosts, and noncooperating GPU programs
    are outside this advisory lock's scope. No hardware availability is inferred.
    """
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    directory = base / "storylens" / "resources"
    directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.mkdir(mode=0o700, exist_ok=True)
    for private in (directory.parent, directory):
        info = private.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("Resource cache must be a real private directory")
        if os.name != "nt":
            if info.st_uid != os.getuid():
                raise ValueError("Resource cache must belong to the current user")
            private.chmod(0o700)
    return directory / "cuda.lock"


@contextmanager
def cuda_inference_lock(timeout):
    """Serialize CUDA critical sections using an OS-released file lock.

    Wait at most timeout seconds; timeout is a failure, never CPU fallback.
    The persistent 0600 file contains no media paths, process IDs or model data;
    it is never unlinked because replacing its inode would split ownership.
    POSIX uses flock; Windows locks byte zero with msvcrt and inherits the
    per-user local-cache ACL. Closing/crashing the owner releases the OS lock.
    This coordinates cooperating callers only and is not GPU health detection.
    """
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("CUDA resource timeout must be positive and finite")
    deadline = time.monotonic() + timeout
    path = _cuda_lock_path()
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    locked = False
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("CUDA resource lock must be a regular file")
        if os.name != "nt":
            if info.st_uid != os.getuid():
                raise ValueError("CUDA resource lock must belong to the current user")
            os.fchmod(descriptor, 0o600)
        if os.name == "nt":
            import msvcrt
        else:
            import fcntl
        while True:
            try:
                if os.name == "nt":
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    # Windows permits byte-range locks beyond EOF. Avoid an
                    # initialization write racing another owner's locked byte.
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("CUDA inference resource wait exceeded configured timeout") from error
                time.sleep(min(0.05, remaining))
        yield
    finally:
        try:
            if locked:
                if os.name == "nt":
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
