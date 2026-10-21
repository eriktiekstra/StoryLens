"""Persisted, UI-independent orchestration for a private media analysis project.

The manifest records project intent and references, not authority. Project
catalogs and analysis artifacts stay separate from the read-only source.
"""

from concurrent.futures import CancelledError
from copy import deepcopy
from dataclasses import asdict, fields, replace
import fnmatch
import importlib.util
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
import time
from uuid import uuid4

from .config import Config
from .ingest import discover, kind
from .service import Analyzer

VERSION = "1.0"
PROJECT_VERSION = "3.0"
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_MEDIA = 10000
_DIRECTORIES = ("storyboards", "review", "catalog.artifacts")
_STATE_KEYS = {"analysis", "technical", "ai", "storyboard"}
_AI_FIELDS = {"embedding_model", "vision_model", "ollama_endpoint", "semantic_timeout",
              "audio_transcription_model", "audio_transcription_device", "audio_transcription_language"}
_LEGACY_STATE_KEYS = {"analysis", "storyboard", "approved_hash", "approved_evidence", "editor",
                      "preview", "export", "reports", "operation"}


def _empty_analysis():
    return {"status": "not_started", "summary": None, "error": None}


def _json_bytes(document, *, limit=MAX_JSON_BYTES):
    try:
        raw = json.dumps(document, sort_keys=True, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError("Document must be finite JSON") from error
    if len(raw) > limit:
        raise ValueError("JSON document exceeds size limit")
    return raw


def _hash(document):
    return hashlib.sha256(_json_bytes(document)).hexdigest()


def _read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key: " + key)
            result[key] = value
        return result

    def invalid(value):
        raise ValueError("Non-finite JSON value: " + value)

    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_nlink != 1:
        raise ValueError("JSON input must be a regular project-local file")
    with path.open("rb") as stream:
        raw = stream.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("JSON document exceeds size limit")
    try:
        result = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError("Invalid JSON document: " + str(path)) from error
    if not isinstance(result, dict):
        raise ValueError("JSON document must be an object")
    return result


def _options(mode, depth):
    if mode not in ("technical", "local-ai"):
        raise ValueError("mode must be technical or local-ai")
    if depth not in ("fast", "standard", "deep"):
        raise ValueError("depth must be fast, standard or deep")


def _configuration(values, database, mode, depth):
    """Validate saved fields without consulting ambient environment variables."""
    expected = {field.name for field in fields(Config)} - {"database"}
    if not isinstance(values, dict) or values.keys() != expected:
        raise ValueError("Project configuration must contain exactly the known Config fields")
    defaults = Config(database)
    for name, value in values.items():
        default = getattr(defaults, name)
        if name == "exclusions":
            if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
                raise ValueError("exclusions must contain strings")
        elif isinstance(default, bool):
            if type(value) is not bool:
                raise ValueError(name + " must be boolean")
        elif isinstance(default, str):
            if not isinstance(value, str) or len(value) > 8192:
                raise ValueError(name + " must be bounded text")
        elif isinstance(default, int):
            if type(value) is not int or value < 1:
                raise ValueError(name + " must be a positive integer")
        else:
            try:
                valid = (not isinstance(value, bool) and isinstance(value, (int, float))
                         and math.isfinite(value) and value > 0)
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError(name + " must be a finite positive number")
    if values["scene_threshold"] > 1 or values["max_audio_seconds"] > 1800:
        raise ValueError("Configuration limits exceeded")
    if not values["ffmpeg"] or not values["ffprobe"]:
        raise ValueError("ffmpeg and ffprobe must be executable names or paths")
    if values["audio_transcription_device"] not in ("cpu", "cuda"):
        raise ValueError("audio_transcription_device must be cpu or cuda")
    if len(values["audio_transcription_language"]) > 32:
        raise ValueError("audio_transcription_language is too long")
    if depth == "fast" and values["vision_model"] and mode == "local-ai":
        raise ValueError("Choose standard or deep depth to enable vision inference")
    if values["vision_model"] or values["ollama_endpoint"] != defaults.ollama_endpoint:
        from .providers import OllamaVision
        OllamaVision(values["ollama_endpoint"], values["vision_model"] or "configuration-check",
                     values["semantic_timeout"])
    return Config(database=database, **dict(values, exclusions=tuple(values["exclusions"])))


def preflight(source: Path, config: Config, *, mode="technical", depth="fast") -> dict:
    """Inspect a collection and installed tool configuration, without inference."""
    _options(mode, depth)
    result = {"counts": {"image": 0, "video": 0, "audio": 0},
              "tools": {"ffmpeg": shutil.which(config.ffmpeg), "ffprobe": shutil.which(config.ffprobe)},
              "errors": [], "warnings": []}
    heif_files = 0
    try:
        source = Path(source).expanduser().resolve(strict=True)
        if not source.is_dir():
            raise ValueError("Source must be an existing directory")
        for entry in discover(source, config.exclusions):
            if isinstance(entry, OSError):
                if len(result["errors"]) < 100:
                    result["errors"].append("Source traversal failed: " + str(entry))
                continue
            if isinstance(entry, Path) and entry.suffix.lower() in {".heic", ".heif"}:
                heif_files += 1
            result["counts"][kind(entry)] += 1
            if sum(result["counts"].values()) > MAX_MEDIA:
                result["errors"].append("Projects support at most 10000 current media files")
                break
    except (OSError, ValueError, RecursionError) as error:
        result["errors"].append(str(error))
    counts = result["counts"]
    if not sum(counts.values()):
        result["errors"].append("Source contains no recognized media")
    if counts["video"] or counts["audio"]:
        for name, executable in result["tools"].items():
            if executable is None:
                result["errors"].append(name + " is required for audio/video analysis")
        result["warnings"].append("Tool availability does not certify GPU support or media codecs.")
    if heif_files:
        try:
            heif_installed = importlib.util.find_spec("pillow_heif") is not None
        except (ImportError, ValueError):
            heif_installed = False
        if not heif_installed:
            result["warnings"].append(
                f"{heif_files} HEIC/HEIF files detected; install pillow-heif in this Python environment "
                "to decode them: python -m pip install 'pillow-heif>=1,<2'. Otherwise those files will fail analysis.")
    if counts["video"] or counts["audio"]:
        result["warnings"].append(
            f"External-tool timeout for this project: {config.timeout:g}s. Large/complex videos may exceed it; "
            "change timeout in project settings before retrying. Existing projects retain their saved timeout.")
    if mode == "local-ai":
        if not any((config.embedding_model, config.vision_model, config.audio_transcription_model)):
            result["errors"].append("Choose at least one local AI capability: embeddings, vision or speech transcription")
        if config.embedding_model:
            model = Path(config.embedding_model).expanduser()
            if not model.is_dir() or not (model / "config.json").is_file():
                result["errors"].append("Embeddings need an installed local model directory containing config.json; configure its path or disable embeddings")
        if config.vision_model:
            from .providers import OllamaVision
            try:
                if depth == "fast":
                    raise ValueError("Choose standard or deep depth for vision")
                OllamaVision(config.ollama_endpoint, config.vision_model, config.semantic_timeout)
            except ValueError as error:
                result["errors"].append(str(error))
            result["warnings"].append("Start Ollama and install the selected vision model explicitly; analysis never downloads models.")
        if config.audio_transcription_model and not Path(config.audio_transcription_model).expanduser().is_dir():
            result["errors"].append("Speech transcription needs an installed local model directory; configure its path or disable speech")
    return result


def _no_symlinks(path):
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError("Project paths must not use symlinks: " + str(item))


def _separate(directory, source):
    if directory.is_relative_to(source) or source.is_relative_to(directory):
        raise ValueError("Project directory and source directory must be separate, non-overlapping locations")




class Project:
    """A project-local catalog and versioned analysis/storyboard artifacts."""
    def __init__(self, directory, source, config, mode, depth, state):
        self.directory, self.source, self.config = directory, source, config
        self.mode, self.depth, self._state = mode, depth, state
        self.started_at = None
        self.activity = ""
        self._manifest_hash = None

    @property
    def state(self):
        return deepcopy(self._state)

    @property
    def document_path(self):
        reference = self._state["storyboard"]
        return self._path(reference, "storyboards", ".json") if reference else None

    @classmethod
    def create(cls, directory, source, config, *, mode="technical", depth="fast"):
        _options(mode, depth)
        source = Path(source).expanduser().resolve(strict=True)
        directory = Path(directory).expanduser().absolute()
        _no_symlinks(directory)
        directory = directory.resolve()
        _separate(directory, source)
        if directory.exists():
            raise ValueError("Project directory already exists; choose a new location or open a project")
        values = asdict(config)
        values.pop("database")
        if mode == "technical":
            for name in ("embedding_model", "vision_model", "audio_transcription_model"):
                values[name] = ""
        if mode == "technical" or depth == "fast" or not values["vision_model"]:
            values["ollama_endpoint"] = Config(directory / "catalog.sqlite3").ollama_endpoint
        if mode == "local-ai" and depth == "fast":
            values["vision_model"] = ""
        if mode == "local-ai":
            for name in ("embedding_model", "audio_transcription_model"):
                if values[name]:
                    values[name] = str(Path(values[name]).expanduser().resolve())
        config = _configuration(values, directory / "catalog.sqlite3", mode, depth)
        checked = preflight(source, config, mode=mode, depth=depth)
        if checked["errors"]:
            raise ValueError("Preflight failed: " + "; ".join(checked["errors"]))
        directory.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.mkdir(mode=0o700)
        for name in _DIRECTORIES:
            (directory / name).mkdir(mode=0o700)
        state = {"analysis": _empty_analysis(), "technical": _empty_analysis(),
                 "ai": _empty_analysis(), "storyboard": None}
        project = cls(directory, source, config, mode, depth, state)
        with Analyzer(config):
            pass
        project._persist()
        return project

    @classmethod
    def open(cls, directory):
        directory = Path(directory).expanduser().absolute()
        _no_symlinks(directory)
        directory = directory.resolve(strict=True)
        manifest = directory / "project.json"
        _no_symlinks(manifest)
        document = _read_json(manifest)
        if document.keys() != {"schema_version", "kind", "source", "mode", "depth", "config", "state"}:
            raise ValueError("Invalid project manifest fields")
        if document["schema_version"] not in (VERSION, "2.0", PROJECT_VERSION) or document["kind"] != "storylens_project":
            raise ValueError("Unsupported project kind or schema_version")
        _options(document["mode"], document["depth"])
        if not isinstance(document["source"], str):
            raise ValueError("Project source must be a canonical absolute directory")
        source = Path(document["source"])
        if not source.is_absolute() or source.resolve(strict=True) != source or not source.is_dir():
            raise ValueError("Project source must be a canonical absolute directory")
        _no_symlinks(source)
        _separate(directory, source)
        config = _configuration(document["config"], directory / "catalog.sqlite3", document["mode"], document["depth"])
        saved_state = document["state"]
        legacy_ai = False
        migrated = (document["schema_version"] != PROJECT_VERSION
                    or (isinstance(saved_state, dict) and saved_state.keys() == _LEGACY_STATE_KEYS))
        if isinstance(saved_state, dict) and saved_state.keys() == _LEGACY_STATE_KEYS:
            saved_state = {"analysis": saved_state["analysis"], "storyboard": saved_state["storyboard"]}
        if (document["schema_version"] in (VERSION, "2.0") and isinstance(saved_state, dict)
                and saved_state.keys() == {"analysis", "storyboard"}):
            # Old technical projects have an exact basic summary. A legacy AI
            # run mixed basic and model stages, so only claim basic success when
            # its complete scan reported no basic failures.
            previous = deepcopy(saved_state["analysis"])
            saved_state = dict(saved_state, technical=_empty_analysis(), ai=_empty_analysis())
            if document["mode"] == "technical":
                saved_state["technical"] = previous
            else:
                saved_state["ai"] = previous
                legacy_ai = True
            migrated = True
        project = cls(directory, source, config, document["mode"], document["depth"], saved_state)
        project._manifest_hash = _hash(document)
        project._layout()
        project._validate_state()
        if legacy_ai:
            analysis = project._state["ai"]
            summary = analysis["summary"]
            if summary is not None and analysis["status"] in {"completed", "partial"}:
                basic_errors = [error for error in summary["errors"]
                                if error["stage"] not in {"semantic_embeddings", "semantic_vision", "audio_transcription"}]
                metadata = summary["stages"].get("metadata", {})
                metadata_count = metadata.get("analyzed", 0) + metadata.get("cache_hits", 0)
                technical_count = sum(counts["analyzed"] + counts["cache_hits"]
                                      for name, counts in summary["stages"].items()
                                      if name in {"image_technical", "video_technical", "audio_technical"})
                if (not basic_errors and not summary["errors_truncated"]
                        and summary["discovered"] == summary["indexed"]
                        and metadata_count == technical_count == summary["indexed"]):
                    project._state["technical"] = {"status": "completed", "summary": None, "error": None}
        if not config.database.is_file():
            raise ValueError("Project-local catalog is missing")
        project._validate_catalog()
        # A killed process cannot honestly remain 'running' after a new UI opens it.
        changed = migrated
        for operation in ("analysis", "technical", "ai"):
            if project._state[operation]["status"] == "running":
                project._state[operation].update(status="interrupted", error="Previous analysis did not finish; cached stages can be resumed")
                changed = True
        if changed:
            project._persist()
        return project

    def _path(self, reference, folder, suffix):
        if not isinstance(reference, str):
            raise ValueError("Project artifact reference must be text")
        relative = Path(reference)
        if (relative.is_absolute() or len(relative.parts) != 2 or relative.parts[0] != folder
                or relative.as_posix() != reference or "\\" in reference
                or relative.suffix != suffix or any(part in (".", "..") for part in relative.parts)):
            raise ValueError("Invalid project-local artifact reference: " + reference)
        target = self.directory / relative
        _no_symlinks(target)
        if target.resolve().parent != self.directory / folder:
            raise ValueError("Artifact escaped the project directory")
        if not target.is_file():
            raise ValueError("Project artifact is missing: " + reference)
        return target

    def _layout(self):
        _no_symlinks(self.directory)
        if self.directory.resolve(strict=True) != self.directory:
            raise ValueError("Project directory changed")
        if self.source.resolve(strict=True) != self.source or not self.source.is_dir():
            raise ValueError("Project source changed; restore the approved directory")
        _no_symlinks(self.source)
        _separate(self.directory, self.source)
        for name in _DIRECTORIES:
            path = self.directory / name
            if not path.is_dir() or path.is_symlink():
                raise ValueError("Missing or unsafe project directory: " + name)
        def failed(error):
            raise ValueError("Cannot inspect project paths: " + str(error)) from error

        for root, directories, files in os.walk(self.directory, followlinks=False, onerror=failed):
            for name in directories + files:
                path = Path(root) / name
                info = path.lstat()
                if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)) or path.is_symlink():
                    raise ValueError("Unsafe project path: " + str(path))
                if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                    raise ValueError("Project state must not use hard links: " + str(path))

    def _validate_state(self):
        state = self._state
        if not isinstance(state, dict) or state.keys() != _STATE_KEYS:
            raise ValueError("Invalid project state fields")
        for operation in ("analysis", "technical", "ai"):
            analysis = state[operation]
            if (not isinstance(analysis, dict) or analysis.keys() != {"status", "summary", "error"}
                    or analysis["status"] not in ("not_started", "running", "completed", "partial", "interrupted", "failed")
                    or (analysis["summary"] is not None and not isinstance(analysis["summary"], dict))
                    or (analysis["error"] is not None and not isinstance(analysis["error"], str))):
                raise ValueError("Invalid project analysis state")
            if analysis["summary"] is not None:
                self._validate_summary(analysis["summary"])
        if state["storyboard"] is not None:
            self._path(state["storyboard"], "storyboards", ".json")
    def _validate_catalog(self):
        try:
            connection = sqlite3.connect(self.config.database.as_uri() + "?mode=ro", uri=True)
            try:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if version != 2 or not {"media", "paths", "stages", "failures", "transcripts"} <= tables:
                    raise ValueError("Not a supported project-local StoryLens catalog")
            finally:
                connection.close()
        except sqlite3.DatabaseError as error:
            raise ValueError("Invalid project-local catalog") from error

    def _validate_summary(self, summary):
        keys = {"schema_version", "root", "discovered", "indexed", "analyzed", "cache_hits",
                "failed", "errors", "errors_truncated", "stages"}
        if summary.keys() != keys or summary["schema_version"] != VERSION or summary["root"] != str(self.source):
            raise ValueError("Invalid persisted scan summary")
        for name in ("discovered", "indexed", "analyzed", "cache_hits", "failed"):
            if type(summary[name]) is not int or summary[name] < 0:
                raise ValueError("Invalid persisted scan count")
        if type(summary["errors_truncated"]) is not bool or not isinstance(summary["errors"], list) or len(summary["errors"]) > 100:
            raise ValueError("Invalid persisted scan errors")
        for error in summary["errors"]:
            if (not isinstance(error, dict) or error.keys() != {"path", "stage", "error"}
                    or any(not isinstance(value, str) for value in error.values())):
                raise ValueError("Invalid persisted scan error")
        if not isinstance(summary["stages"], dict):
            raise ValueError("Invalid persisted scan stages")
        stages = {"metadata", "image_technical", "video_technical", "audio_technical",
                  "semantic_embeddings", "semantic_vision", "audio_transcription"}
        for name, counts in summary["stages"].items():
            if (name not in stages or not isinstance(counts, dict)
                    or counts.keys() != {"analyzed", "cache_hits", "failed"}
                    or any(type(value) is not int or value < 0 for value in counts.values())):
                raise ValueError("Invalid persisted scan stage counts")

    def _persist(self):
        self._layout()
        self._validate_state()
        manifest = self.directory / "project.json"
        if self._manifest_hash is None:
            if manifest.exists() or manifest.is_symlink():
                raise ValueError("Project manifest already exists")
        elif _hash(_read_json(manifest)) != self._manifest_hash:
            raise ValueError("Project changed in another session; reopen it before continuing")
        # Catalog creates SQLite sidecars with the process umask; their enclosing
        # directory is already private, and these permissions persist on reopen.
        for name in ("catalog.sqlite3", "catalog.sqlite3-wal", "catalog.sqlite3-shm", "catalog.feedback.sqlite3"):
            path = self.directory / name
            if path.is_file():
                path.chmod(0o600)
        values = asdict(self.config)
        values.pop("database")
        raw = _json_bytes({"schema_version": PROJECT_VERSION, "kind": "storylens_project", "source": str(self.source),
                           "mode": self.mode, "depth": self.depth, "config": values, "state": self._state})
        descriptor, temporary = tempfile.mkstemp(prefix=".project-", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.directory / "project.json")
            self._manifest_hash = hashlib.sha256(raw).hexdigest()
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _new_path(self, folder, name, suffix):
        self._layout()
        return self.directory / folder / (name + "-" + uuid4().hex + suffix)

    def _write_new(self, path, document, *, limit=MAX_JSON_BYTES):
        raw = _json_bytes(document, limit=limit)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        return path.relative_to(self.directory).as_posix()


    def _authorize_item(self, item, *, allowed=None):
        """Authorize only this item's references; byte reads are an explicit gate."""
        current = False
        for reference in item["paths"]:
            path = Path(reference["path"])
            if not path.exists() and not path.is_symlink():
                continue
            if not path.is_absolute() or path.resolve(strict=True) != path or not path.is_relative_to(self.source):
                raise ValueError("Catalog contains a source outside the approved project source")
            _no_symlinks(path)
            if allowed is not None:
                permitted = path in allowed
            else:
                permitted = path.is_file() and kind(path) == item["kind"]
                # Discovery applies exclusions to directories as well as files.
                for candidate in (path, *path.parents):
                    if candidate == self.source:
                        break
                    relative = candidate.relative_to(self.source).as_posix()
                    if any(fnmatch.fnmatchcase(relative, pattern) or fnmatch.fnmatchcase(candidate.name, pattern)
                           for pattern in self.config.exclusions):
                        permitted = False
                        break
            if not permitted:
                raise ValueError("Catalog source is excluded or no longer recognized; use a clean project")
            current = True
        return current

    def _scope(self, app):
        """Check catalog-wide paths without loading/grouping every analysis row."""
        self._layout()
        allowed = set()
        for entry in discover(self.source, self.config.exclusions):
            if isinstance(entry, OSError):
                raise ValueError("Source traversal failed: " + str(entry))
            allowed.add(entry)
            if len(allowed) > MAX_MEDIA:
                raise ValueError("Projects support at most 10000 current media files")
        current = set()
        for row in app.catalog.connection.execute("SELECT path, media_id FROM paths"):
            item = {"id": row["media_id"], "paths": [{"path": row["path"]}]}
            if self._authorize_item(item, allowed=allowed):
                current.add(item["id"])
                if len(current) > MAX_MEDIA:
                    raise ValueError("Projects support at most 10000 current media")

    def list_media(self):
        with Analyzer(self.config) as app:
            self._scope(app)
            return app._storyboard_media()

    def media(self, media_id):
        # Contact sheets and terminal tables call this once per entry. Do not
        # rediscover the collection, hash source bytes, or group unrelated media.
        _no_symlinks(self.directory)
        _no_symlinks(self.config.database)
        _no_symlinks(self.source)
        if self.source.resolve(strict=True) != self.source or not self.source.is_dir():
            raise ValueError("Project source changed; restore the approved directory")
        info = self.config.database.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Unsafe project-local catalog")
        with Analyzer(self.config) as app:
            item = app.catalog.get(media_id)
            if not self._authorize_item(item):
                raise ValueError("No current approved project source for media ID: " + str(media_id))
            item["feedback"] = app.feedback(media_id)["values"]
            item["transcript"] = app.catalog.transcript(media_id)
            return item

    def configure(self, **settings):
        """Atomically save validated settings without removing catalog evidence."""
        values = asdict(self.config)
        values.pop("database")
        unknown = settings.keys() - (values.keys() | {"mode", "depth"})
        if unknown:
            raise ValueError("Unknown project settings: " + ", ".join(sorted(unknown)))
        mode = settings.pop("mode", None)
        depth = settings.pop("depth", None)
        values.update(settings)
        for name in ("embedding_model", "audio_transcription_model"):
            if isinstance(values[name], str) and values[name]:
                values[name] = str(Path(values[name]).expanduser().resolve())
        if mode is None:
            mode = "local-ai" if any(values[name] for name in
                                    ("embedding_model", "vision_model", "audio_transcription_model")) else "technical"
        if depth is None:
            depth = "standard" if values["vision_model"] and mode == "local-ai" else "fast"
        _options(mode, depth)
        configured = _configuration(values, self.config.database, mode, depth)
        previous = self.config, self.mode, self.depth, deepcopy(self._state)
        changed = {field.name for field in fields(Config)
                   if getattr(self.config, field.name) != getattr(configured, field.name)}
        basic_changed = bool(changed - _AI_FIELDS - {"timeout", "trust_stat"})
        ai_changed = bool(changed - {"trust_stat"}) or depth != self.depth or mode != self.mode
        self.config, self.mode, self.depth = configured, mode, depth
        if basic_changed:
            self._state["technical"] = _empty_analysis()
        if ai_changed:
            self._state["ai"] = _empty_analysis()
        if basic_changed or ai_changed:
            self._state["analysis"] = deepcopy(self._state["technical"])
        try:
            self._persist()
        except BaseException:
            self.config, self.mode, self.depth, self._state = previous
            raise
        return self.state

    def analysis_status(self, operation):
        if operation not in ("technical", "ai"):
            raise ValueError("operation must be technical or ai")
        return deepcopy(self._state[operation])

    def analyze(self, progress=None, *, operation="all", cancelled=None):
        """Run basic analysis and/or optional models, reusing successful stages.

        Model setup never gates basic results. Each pass has its own persisted
        status; the legacy ``analysis`` entry tracks the most recent pass.
        """
        if operation not in ("technical", "ai", "all"):
            raise ValueError("operation must be technical, ai or all")
        if cancelled is not None and not callable(cancelled):
            raise ValueError("cancelled must be a callable")
        enabled = self.mode == "local-ai" and any(
            (self.config.embedding_model, self.config.vision_model, self.config.audio_transcription_model))
        if operation == "ai" and not enabled:
            raise ValueError("Enable at least one local AI capability in project settings before AI analysis")
        self._layout()
        # One scan establishes current basic prerequisites and optional models.
        # Its independent basic summary keeps model failures from erasing basic
        # success without rediscovering or hashing the collection a second time.
        current = "ai" if operation != "technical" and enabled else "technical"
        last_save = time.monotonic()
        self.started_at = last_save
        self.activity = "Checking project"

        previous_technical = self.analysis_status("technical") if current == "ai" else None
        technical_updated = previous_technical is not None and previous_technical["status"] != "completed"
        self._state[current] = {"status": "running", "summary": None, "error": None}
        self._state["analysis"] = deepcopy(self._state[current])
        if technical_updated:
            self._state["technical"] = {"status": "running", "summary": None, "error": None}
        self._persist()
        
        def checkpoint(summary):
            nonlocal last_save, technical_updated
            self._state[current]["summary"] = deepcopy(summary)
            self._state["analysis"]["summary"] = deepcopy(summary)
            if current == "technical" and (
                    summary["failed"] or any(counts["analyzed"] for counts in summary["stages"].values())):
                self._state["ai"] = _empty_analysis()
            if current == "ai":
                basic = app.basic_summary
                technical_updated = technical_updated or bool(basic["failed"]) or any(
                    counts["analyzed"] or counts["failed"] for counts in basic["stages"].values())
                if technical_updated:
                    self._state["technical"] = {"status": "running", "summary": deepcopy(basic), "error": None}
            if summary["discovered"] > MAX_MEDIA:
                raise ValueError("Projects support at most 10000 current media files")
            self.activity = app.activity or ("Local AI" if current == "ai" else "Basic analysis")
            if time.monotonic() - last_save >= 1:
                self._persist()
                last_save = time.monotonic()
            if progress is not None:
                progress(deepcopy(summary))
        
        try:
            if cancelled is not None and cancelled():
                raise CancelledError("Analysis cancelled; completed stages are saved and can be resumed")
            # Model paths/runtime failures belong to their individual
            # stages, not a global gate that disables other capabilities.
            checked = preflight(self.source, self.config, mode="technical", depth="fast")
            if checked["errors"]:
                raise ValueError("Preflight failed: " + "; ".join(checked["errors"]))
            config = self.config if current == "ai" else replace(
                self.config, embedding_model="", vision_model="", audio_transcription_model="")
            with Analyzer(config) as app:
                for row in app.catalog.connection.execute("SELECT path FROM paths"):
                    path = Path(row["path"])
                    if path.exists() or path.is_symlink():
                        if not path.is_absolute() or path.resolve() != path or not path.is_relative_to(self.source):
                            raise ValueError("Catalog contains a source outside the approved project source")
                        _no_symlinks(path)
                summary = app.scan(self.source, analyze=True, stage="ai" if current == "ai" else "all",
                                   depth=self.depth if current == "ai" else None,
                                   checkpoint=checkpoint, cancelled=cancelled)
                if current == "ai":
                    basic = app.basic_summary
                    old_summary = previous_technical["summary"]
                    if (technical_updated or previous_technical["status"] != "completed"
                            or old_summary is None
                            or basic["discovered"] != old_summary["discovered"]
                            or basic["indexed"] != old_summary["indexed"]):
                        self._state["technical"] = {
                            "status": "partial" if basic["failed"] else "completed",
                            "summary": deepcopy(basic), "error": None}
            self._state[current].update(status="partial" if summary["failed"] else "completed",
                                        summary=deepcopy(summary), error=None)
            self._state["analysis"] = deepcopy(self._state[current])
        except BaseException as error:
            interrupted = isinstance(error, (KeyboardInterrupt, SystemExit, CancelledError)) or type(error).__name__ == "CancelledError"
            self._state[current].update(status="interrupted" if interrupted else "failed",
                                        error=str(error)[:2000] or type(error).__name__)
            if current == "ai" and technical_updated:
                self._state["technical"].update(status="interrupted" if interrupted else "failed",
                                                error=str(error)[:2000] or type(error).__name__)
            self._state["analysis"] = deepcopy(self._state[current])
            self.activity = "Interrupted" if interrupted else "Failed"
            self._persist()
            raise
        self._persist()
        self.activity = "Analysis complete"
        return summary

    def diagnostics(self):
        """Write an explicit, private local diagnostic report; never transmit it."""
        import re
        from urllib.parse import urlsplit, urlunsplit

        def safe_url(match):
            try:
                url = urlsplit(match.group())
                host = url.hostname or ""
                if ":" in host:
                    host = "[" + host + "]"
                if url.port:
                    host += ":" + str(url.port)
                return urlunsplit((url.scheme, host, url.path, "", ""))
            except ValueError:
                return "[redacted URL]"

        report = {"schema_version": VERSION, "kind": "storylens_diagnostics",
                  "source": str(self.source), "project": str(self.directory),
                  "mode": self.mode, "depth": self.depth,
                  "settings": {name: getattr(self.config, name) for name in
                               ("timeout", "semantic_timeout", "embedding_model", "vision_model",
                                "audio_transcription_model", "audio_transcription_device",
                                "audio_transcription_language")},
                  "technical": self.analysis_status("technical"), "ai": self.analysis_status("ai"),
                  "privacy": "Local report contains source paths and filenames. Review before sharing."}
        report = json.loads(re.sub(r"https?://[^\s\"<>]+", safe_url, json.dumps(report)))
        self._layout()
        path = self.directory / ("diagnostics-" + uuid4().hex + ".json")
        self._write_new(path, report)
        return {"path": str(path), "report": report}

    def recommend(self, *, duration_seconds=60, strategy="balanced"):
        with Analyzer(self.config) as app:
            self._scope(app)
            document = app.recommend(duration_seconds=duration_seconds, strategy=strategy)
        self.save_storyboard(document)
        return document

    def _document(self):
        if self.document_path is None:
            raise ValueError("Project has no storyboard; recommend one first")
        return _read_json(self.document_path)

    def load_storyboard(self):
        document = self._document()
        with Analyzer(self.config) as app:
            self._scope(app)
            app.validate_storyboard(document)
        return document

    def save_storyboard(self, document):
        # JSON round-trip detaches caller-owned objects and rejects nonfinite data.
        document = json.loads(_json_bytes(document))
        with Analyzer(self.config) as app:
            self._scope(app)
            app.validate_storyboard(document)
        path = self._new_path("storyboards", "storyboard", ".json")
        reference = self._write_new(path, document)
        self._state["storyboard"] = reference
        self._persist()
        return path

    def set_selection(self, media_id, selection):
        if selection not in ("include", "exclude", "neutral"):
            raise ValueError("selection must be include, exclude or neutral")
        self.media(media_id)
        with Analyzer(self.config) as app:
            result = app.set_feedback(media_id, {"selection": selection})
        self._persist()
        return result

