"""Small, strictly validated configuration for implemented capabilities."""

from dataclasses import dataclass, fields
import math
import os
from pathlib import Path
import sys
import tomllib


def default_database() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    elif sys.platform == "darwin":
        root = Path.home() / "Library/Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return root / "storylens/catalog.sqlite3"


@dataclass(frozen=True)
class Config:
    database: Path
    ffprobe: str = "ffprobe"
    timeout: float = 30.0
    exclusions: tuple[str, ...] = ()
    trust_stat: bool = False
    ffmpeg: str = "ffmpeg"
    scene_threshold: float = 0.3
    minimum_scene_seconds: float = 0.5
    max_scenes: int = 256
    embedding_model: str = ""
    vision_model: str = ""
    ollama_endpoint: str = "http://127.0.0.1:11434"
    semantic_timeout: float = 120.0


def load_config(path: Path | None = None, **overrides) -> Config:
    values = {}
    if path is not None:
        with path.open("rb") as source:
            document = tomllib.load(source)
        if set(document) - {"storylens"}:
            raise ValueError("Configuration accepts only [storylens]")
        section = document.get("storylens", {})
        if not isinstance(section, dict):
            raise ValueError("storylens must be a TOML table")
        values.update(section)
    names = {field.name for field in fields(Config)}
    for name in names:
        value = os.environ.get("STORYLENS_" + name.upper())
        if value is not None:
            if name == "trust_stat":
                if value.lower() not in {"true", "false"}:
                    raise ValueError("STORYLENS_TRUST_STAT must be true or false")
                value = value.lower() == "true"
            elif name == "exclusions":
                value = value.split(os.pathsep) if value else []
            elif name == "max_scenes":
                value = int(value)
            elif name in {"timeout", "semantic_timeout", "scene_threshold", "minimum_scene_seconds"}:
                value = float(value)
            values[name] = value
    values.update({key: value for key, value in overrides.items() if value is not None})
    if unknown := set(values) - names:
        raise ValueError("Unknown configuration keys: " + ", ".join(sorted(unknown)))
    if not isinstance(values.get("trust_stat", False), bool):
        raise ValueError("trust_stat must be a boolean")
    exclusions = values.get("exclusions", ())
    if not isinstance(exclusions, (list, tuple)) or not all(isinstance(x, str) for x in exclusions):
        raise ValueError("exclusions must be an array of patterns")
    timeout = values.get("timeout", 30.0)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a finite positive number")
    for executable in ("ffprobe", "ffmpeg"):
        if not isinstance(values.get(executable, executable), str) or not values.get(executable, executable):
            raise ValueError(executable + " must be an executable name or path")
    for name, default in (("scene_threshold", 0.3), ("minimum_scene_seconds", 0.5)):
        value = values.get(name, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(name + " must be a finite positive number")
    if values.get("scene_threshold", 0.3) > 1:
        raise ValueError("scene_threshold must not exceed 1")
    semantic_timeout = values.get("semantic_timeout", 120.0)
    if isinstance(semantic_timeout, bool) or not isinstance(semantic_timeout, (int, float)) or not math.isfinite(semantic_timeout) or semantic_timeout <= 0:
        raise ValueError("semantic_timeout must be a finite positive number")
    for name in ("embedding_model", "vision_model", "ollama_endpoint"):
        if not isinstance(values.get(name, ""), str):
            raise ValueError(name + " must be a string")
    max_scenes = values.get("max_scenes", 256)
    if type(max_scenes) is not int or max_scenes < 1:
        raise ValueError("max_scenes must be a positive integer")
    if "database" not in values:
        values["database"] = default_database()
    values["database"] = Path(values["database"]).expanduser().absolute()
    values["exclusions"] = tuple(exclusions)
    return Config(**values)
