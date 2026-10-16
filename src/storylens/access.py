"""Application-level media path authorization, not an operating-system sandbox."""

from pathlib import Path


class PathPolicy:
    def __init__(self, roots):
        self.roots = tuple(dict.fromkeys(Path(root).expanduser().resolve(strict=True) for root in roots))
        if not self.roots or any(not root.is_dir() for root in self.roots):
            raise ValueError("At least one existing approved media directory is required")

    def authorize(self, path):
        canonical = Path(path).expanduser().resolve(strict=True)
        if not any(canonical.is_relative_to(root) for root in self.roots):
            raise ValueError("Path is outside approved media roots")
        if not (canonical.is_file() or canonical.is_dir()):
            raise ValueError("Authorized path must be a regular file or directory")
        return canonical
