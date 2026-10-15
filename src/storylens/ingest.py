"""Read-only discovery and bounded-memory, content-addressed identity."""

import fnmatch
import hashlib
import os
from pathlib import Path
import stat

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tif", ".tiff", ".bmp", ".gif", ".avif", ".dng", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2", ".raf"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".mts", ".m2ts", ".3gp", ".mpg", ".mpeg"}
AUDIO_EXTENSIONS = {".mp3", ".aac", ".wav", ".flac", ".m4a", ".ogg", ".opus", ".aiff", ".aif", ".wma"}


def kind(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    return None


def discover(root: Path, exclusions: tuple[str, ...]):
    """Yield paths or OSErrors; never silently swallow traversal failures."""
    def excluded(path):
        relative = path.relative_to(root).as_posix() if path != root else path.name
        return any(fnmatch.fnmatchcase(relative, pattern) or fnmatch.fnmatchcase(path.name, pattern)
                   for pattern in exclusions)

    def visit(directory):
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    if excluded(path) or entry.is_symlink():
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            yield from visit(path)
                        elif entry.is_file(follow_symlinks=False) and kind(path):
                            yield path
                    except OSError as error:
                        yield error
        except OSError as error:
            yield error

    if root.is_file():
        if kind(root) and not excluded(root):
            yield root
        elif not kind(root):
            raise ValueError("Unsupported media extension: " + root.suffix)
    elif root.is_dir():
        yield from visit(root)
    else:
        raise ValueError("Media path is not an existing file or directory")


def snapshot(path: Path):
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Source is no longer a regular file")
    return info


def signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def fingerprint(path: Path, before) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        opened = os.fstat(source.fileno())
        # Windows Python 3.12 path stat uses creation time for ctime, while
        # fstat uses change time. Compare ctime only within the same stat API.
        if (not os.path.samestat(opened, before) or opened.st_size != before.st_size
                or opened.st_mtime_ns != before.st_mtime_ns
                or signature(snapshot(path)) != signature(before)):
            raise ValueError("Source changed before hashing")
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
        if signature(os.fstat(source.fileno())) != signature(opened):
            raise ValueError("Source changed during hashing")
    if signature(snapshot(path)) != signature(before):
        raise ValueError("Source changed during hashing")
    return "sha256:" + digest.hexdigest()
