"""Local, bounded visual review artifacts; no browser server or source mutation."""

import math
import os
from pathlib import Path
import tempfile
import uuid

from PIL import Image, ImageDraw

from .access import PathPolicy
from .providers import _frame

PAGE_SIZE = 24
_COLUMNS = 4
_CELL_WIDTH = 240
_CELL_HEIGHT = 196
_HEADER = 42


def _label(value, limit=38):
    text = str(value).encode("ascii", "backslashreplace").decode("ascii")
    text = "".join(character if 32 <= ord(character) < 127 else repr(character)[1:-1]
                   for character in text)
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _thumbnail_source(project, media, entry):
    if entry["kind"] == "image":
        policy = PathPolicy([project.source])
        for reference in media["paths"]:
            original = Path(reference["path"])
            try:
                if not original.is_symlink():
                    path = policy.authorize(original)
                    if path.is_file():
                        return path, "Source image"
            except (OSError, ValueError):
                continue
        return None, "Preview unavailable"
    if entry["kind"] == "audio":
        return None, "Audio (no visual thumbnail)"
    artifact_root = project.config.database.resolve().with_suffix(".artifacts")
    scenes = media.get("analysis", {}).get("video_technical", {}).get("result", {}).get("scenes", [])
    midpoint = (entry["start_seconds"] + entry["end_seconds"]) / 2
    ranked = sorted(scenes, key=lambda scene: abs(
        (scene["start_seconds"] + scene["end_seconds"]) / 2 - midpoint))
    for scene in ranked:
        frame = scene.get("representative_frame", {})
        if not isinstance(frame.get("path"), str):
            continue
        original = Path(frame["path"])
        try:
            if original.is_symlink():
                continue
            path = original.resolve(strict=True)
            if not path.is_relative_to(artifact_root) or not path.is_file():
                continue
        except OSError:
            continue
        seconds = frame.get("source_timestamp_seconds")
        label = f"Scene sample at {seconds:.3f}s" if (
            isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and math.isfinite(seconds)
        ) else "Scene reference sample"
        return path, label
    return None, "Preview unavailable"


def _save_page(image, directory, name):
    descriptor, temporary = tempfile.mkstemp(prefix=".review-", suffix=".jpg", dir=directory)
    os.close(descriptor)
    temporary = Path(temporary)
    target = directory / name
    try:
        image.save(temporary, format="JPEG", quality=88, exif=b"")
        os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def contact_sheets(project, document):
    """Render at most 24 entries per metadata-free JPEG into private local pages.

    Video tiles show stored scene references with their own timestamps; they are
    not a frame-accurate preview of a cut or of the eventual exported film.
    Missing thumbnails are explicit, never replaced by fabricated source images.
    """
    if project.load_storyboard() != document:
        raise ValueError("Storyboard changed; save/reload it before generating contact sheets")
    if not document["items"]:
        raise ValueError("An empty storyboard has no contact sheets")
    directory = project.directory / "review"
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise ValueError("Review output must be a real project directory")
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.resolve().parent != project.directory.resolve():
        raise ValueError("Review output escapes the project directory")
    group = uuid.uuid4().hex[:12]
    paths = []
    for offset in range(0, len(document["items"]), PAGE_SIZE):
        entries = document["items"][offset:offset + PAGE_SIZE]
        rows = math.ceil(len(entries) / _COLUMNS)
        with Image.new("RGB", (_COLUMNS * _CELL_WIDTH, _HEADER + rows * _CELL_HEIGHT), "white") as sheet:
            draw = ImageDraw.Draw(sheet)
            draw.text((10, 7), "Storyboard review - source/scene samples, NOT a rendered film", fill="black")
            draw.text((10, 23), f"Page {offset // PAGE_SIZE + 1}; entries {offset + 1}-{offset + len(entries)}", fill="black")
            for local, entry in enumerate(entries):
                x, y = (local % _COLUMNS) * _CELL_WIDTH, _HEADER + (local // _COLUMNS) * _CELL_HEIGHT
                media = project.media(entry["media_id"])
                path, sample_label = _thumbnail_source(project, media, entry)
                draw.rectangle((x + 5, y + 5, x + _CELL_WIDTH - 6, y + 133), outline="#999999")
                if path is not None:
                    try:
                        with _frame(path) as thumbnail:
                            thumbnail.thumbnail((_CELL_WIDTH - 16, 120), Image.Resampling.LANCZOS)
                            sheet.paste(thumbnail, (x + (_CELL_WIDTH - thumbnail.width) // 2,
                                                   y + 8 + (120 - thumbnail.height) // 2))
                    except (OSError, ValueError, RuntimeError, Warning):
                        sample_label = "Preview unavailable"
                        path = None
                if path is None:
                    draw.text((x + 16, y + 58), _label(sample_label), fill="black")
                references = media.get("paths", [])
                name = Path(references[0]["path"]).name if references else entry["media_id"]
                draw.text((x + 8, y + 138), _label(f"{offset + local + 1}. {name}", 35), fill="black")
                timing = f'{entry["duration_seconds"]:.3f}s hold' if entry["kind"] == "image" else (
                    f'{entry["start_seconds"]:.3f}-{entry["end_seconds"]:.3f}s source')
                draw.text((x + 8, y + 153), _label(timing), fill="black")
                draw.text((x + 8, y + 168), _label(sample_label), fill="black")
                if entry["required"]:
                    draw.text((x + 8, y + 182), "REQUIRED", fill="black")
            paths.append(_save_page(sheet, directory, f"storyboard-{group}-{offset // PAGE_SIZE + 1:02d}.jpg"))
    return paths
