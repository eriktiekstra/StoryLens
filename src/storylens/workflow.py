"""Persisted, UI-independent orchestration for a single private media project.

The manifest records intent and references, not authority: catalog and artifact
locations are derived locally and source/feedback evidence is checked again at
review and editor boundaries. Opening a project never launches an external tool.
"""

from copy import deepcopy
from dataclasses import asdict, fields
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
from .ingest import discover, fingerprint, kind, snapshot
from .service import Analyzer
from .storyboard import validate_storyboard

VERSION = "1.0"
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_REPORT_BYTES = 16 * 1024 * 1024
MAX_MEDIA = 10000
_DIRECTORIES = ("storyboards", "outputs", "reports", "review", "catalog.artifacts")
_STATE_KEYS = {"analysis", "storyboard", "approved_hash", "approved_evidence", "editor",
               "preview", "export", "reports", "operation"}


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
    if mode == "technical" and any(values[name] for name in
                                    ("embedding_model", "vision_model", "audio_transcription_model")):
        raise ValueError("Technical projects cannot enable model inference")
    if depth == "fast" and values["vision_model"]:
        raise ValueError("Fast projects cannot enable vision inference")
    if mode == "technical" or depth == "fast" or not values["vision_model"]:
        if values["ollama_endpoint"] != defaults.ollama_endpoint:
            raise ValueError("Unused vision configuration must use the default loopback endpoint")
    else:
        from .providers import OllamaVision
        OllamaVision(values["ollama_endpoint"], values["vision_model"], values["semantic_timeout"])
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
            "set STORYLENS_TIMEOUT before creating a project. Existing projects retain their saved timeout.")
    if mode == "local-ai":
        if counts["image"] or counts["video"]:
            model = Path(config.embedding_model).expanduser() if config.embedding_model else None
            if model is None or not model.is_dir() or not (model / "config.json").is_file():
                result["errors"].append("Local AI requires an installed embedding model directory with config.json")
            if depth != "fast":
                from .providers import OllamaVision
                try:
                    OllamaVision(config.ollama_endpoint, config.vision_model, config.semantic_timeout)
                except ValueError as error:
                    result["errors"].append(str(error))
                result["warnings"].append("Vision model installation is checked by the local runtime during analysis; no model is downloaded.")
        if config.audio_transcription_model and not Path(config.audio_transcription_model).expanduser().is_dir():
            result["errors"].append("Transcription requires an installed local model directory")
    return result


def _no_symlinks(path):
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError("Project paths must not use symlinks: " + str(item))


def _separate(directory, source):
    if directory.is_relative_to(source) or source.is_relative_to(directory):
        raise ValueError("Project directory and source directory must be separate, non-overlapping locations")


def _digest(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


class Project:
    """A project-local catalog and versioned review/editor artifacts."""

    def __init__(self, directory, source, config, mode, depth, state):
        self.directory, self.source, self.config = directory, source, config
        self.mode, self.depth, self._state = mode, depth, state
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
        state = {"analysis": {"status": "not_started", "summary": None, "error": None},
                 "storyboard": None, "approved_hash": None, "approved_evidence": None,
                 "editor": None, "preview": None, "export": None, "reports": [], "operation": None}
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
        if document["schema_version"] != VERSION or document["kind"] != "storylens_project":
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
        project = cls(directory, source, config, document["mode"], document["depth"], document["state"])
        project._manifest_hash = _hash(document)
        project._layout()
        project._validate_state()
        if not config.database.is_file():
            raise ValueError("Project-local catalog is missing")
        project._validate_catalog()
        # A killed process cannot honestly remain 'running' after a new UI opens it.
        changed = False
        if project._state["analysis"]["status"] == "running":
            project._state["analysis"].update(status="interrupted", error="Previous analysis did not finish; cached stages can be resumed")
            changed = True
        operation = project._state["operation"]
        if operation and operation["status"] == "running":
            operation.update(status="interrupted", error="Previous editor operation did not finish; do not automatically retry")
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
        analysis = state["analysis"]
        if (not isinstance(analysis, dict) or analysis.keys() != {"status", "summary", "error"}
                or analysis["status"] not in ("not_started", "running", "completed", "partial", "interrupted", "failed")
                or (analysis["summary"] is not None and not isinstance(analysis["summary"], dict))
                or (analysis["error"] is not None and not isinstance(analysis["error"], str))):
            raise ValueError("Invalid project analysis state")
        if analysis["summary"] is not None:
            self._validate_summary(analysis["summary"])
        if state["storyboard"] is not None:
            self._path(state["storyboard"], "storyboards", ".json")
        for name in ("approved_hash", "approved_evidence"):
            if state[name] is not None and not _digest(state[name]):
                raise ValueError("Invalid approval hash")
        if (state["approved_hash"] is None) != (state["approved_evidence"] is None):
            raise ValueError("Incomplete project approval")
        if state["approved_hash"] and not state["storyboard"]:
            raise ValueError("Approval requires a storyboard")
        for name, suffix, hash_key in (("editor", ".fcproj", "storyboard_hash"), ("preview", ".png", "project_hash")):
            item = state[name]
            if item is not None:
                if not isinstance(item, dict) or item.keys() != {"path", hash_key} or not _digest(item[hash_key]):
                    raise ValueError("Invalid " + name + " state")
                self._path(item["path"], "outputs", suffix)
        if state["editor"] and (not state["approved_hash"] or state["editor"]["storyboard_hash"] != state["approved_hash"]):
            raise ValueError("Editor reference does not match storyboard approval")
        if state["preview"] and not state["editor"]:
            raise ValueError("Preview requires an editor project")
        if state["export"] is not None:
            self._path(state["export"], "outputs", ".mp4")
            if not state["preview"]:
                raise ValueError("Export requires a preview")
        if not isinstance(state["reports"], list) or len(state["reports"]) > 10000:
            raise ValueError("Invalid operation report references")
        for reference in state["reports"]:
            self._path(reference, "reports", ".json")
        operation = state["operation"]
        if operation is not None:
            if (not isinstance(operation, dict) or operation.keys() != {"kind", "status", "report", "error"}
                    or operation["kind"] not in ("create", "preview", "export")
                    or operation["status"] not in ("running", "succeeded", "failed", "interrupted")
                    or (operation["error"] is not None and not isinstance(operation["error"], str))):
                raise ValueError("Invalid editor operation state")
            if operation["report"] is not None:
                self._path(operation["report"], "reports", ".json")

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
        values = asdict(self.config)
        values.pop("database")
        raw = _json_bytes({"schema_version": VERSION, "kind": "storylens_project", "source": str(self.source),
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
        # Catalog creates SQLite sidecars with the process umask; their enclosing
        # directory is already private, and these permissions persist on reopen.
        for name in ("catalog.sqlite3", "catalog.sqlite3-wal", "catalog.sqlite3-shm", "catalog.feedback.sqlite3"):
            path = self.directory / name
            if path.is_file():
                path.chmod(0o600)

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

    def _invalidate(self):
        self._state.update(approved_hash=None, approved_evidence=None, editor=None, preview=None, export=None)

    def _authorize_item(self, item, *, allowed=None, verify=False):
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
            if verify and fingerprint(path, snapshot(path)) != item["id"]:
                raise ValueError("Source content changed; reanalyze the project before review")
            current = True
        return current

    def _scope(self, app, *, verify=False):
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
            if self._authorize_item(item, allowed=allowed, verify=verify):
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

    def analyze(self, progress=None):
        self._layout()
        self._invalidate()
        self._state["analysis"] = {"status": "running", "summary": None, "error": None}
        self._persist()
        last_save = time.monotonic()

        def checkpoint(summary):
            nonlocal last_save
            self._state["analysis"]["summary"] = deepcopy(summary)
            if summary["discovered"] > MAX_MEDIA:
                raise ValueError("Projects support at most 10000 current media files")
            if time.monotonic() - last_save >= 1:
                self._persist()
                last_save = time.monotonic()
            if progress is not None:
                progress(deepcopy(summary))

        try:
            checked = preflight(self.source, self.config, mode=self.mode, depth=self.depth)
            if checked["errors"]:
                raise ValueError("Preflight failed: " + "; ".join(checked["errors"]))
            with Analyzer(self.config) as app:
                # Analysis repairs changed IDs, but must never absorb foreign
                # catalog references. Check the catalog roots without stale hashes.
                for row in app.catalog.connection.execute("SELECT path FROM paths"):
                    path = Path(row["path"])
                    if path.exists() or path.is_symlink():
                        if not path.is_absolute() or path.resolve() != path or not path.is_relative_to(self.source):
                            raise ValueError("Catalog contains a source outside the approved project source")
                        _no_symlinks(path)
                summary = app.scan(self.source, analyze=True, stage="all",
                                   depth=self.depth if self.mode == "local-ai" else None,
                                   checkpoint=checkpoint)
            self._state["analysis"].update(status="partial" if summary["failed"] else "completed", summary=deepcopy(summary))
        except BaseException as error:
            interrupted = isinstance(error, (KeyboardInterrupt, SystemExit)) or type(error).__name__ == "CancelledError"
            self._state["analysis"].update(status="interrupted" if interrupted else "failed", error=str(error)[:2000] or type(error).__name__)
            self._persist()
            raise
        self._persist()
        return summary

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
        self._invalidate()
        self._persist()
        return path

    def _evidence(self, app, document):
        self._scope(app, verify=True)
        items = app._storyboard_media()
        validate_storyboard(document, {item["id"]: item for item in items})
        evidence = [{"id": item["id"], "paths": item["paths"], "analysis": item["analysis"],
                     "transcript": item.get("transcript")} for item in items]
        digest = hashlib.sha256()
        encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=False, allow_nan=False,
                                   separators=(",", ":"))
        # Aggregate catalog evidence is not an input JSON file: stream its hash
        # rather than imposing the per-document bound on a whole collection.
        for chunk in encoder.iterencode({"media": evidence, "feedback": app._user_state().snapshot()}):
            digest.update(chunk.encode("utf-8"))
        return digest.hexdigest()

    def approve(self, document):
        current = self._document()
        if _hash(document) != _hash(current):
            raise ValueError("Storyboard changed since review; reload and review the current version")
        with Analyzer(self.config) as app:
            evidence = self._evidence(app, current)
        # Re-read after source validation to reject a concurrent document edit.
        if _hash(_read_json(self.document_path)) != _hash(document):
            raise ValueError("Storyboard changed during approval; reload and review")
        if self._state["approved_hash"] != _hash(current) or self._state["approved_evidence"] != evidence:
            self._invalidate()
        self._state.update(approved_hash=_hash(current), approved_evidence=evidence)
        self._persist()

    def set_selection(self, media_id, selection):
        if selection not in ("include", "exclude", "neutral"):
            raise ValueError("selection must be include, exclude or neutral")
        self.media(media_id)
        with Analyzer(self.config) as app:
            result = app.set_feedback(media_id, {"selection": selection})
        self._invalidate()
        self._persist()
        return result

    def _approved(self, approved):
        if approved is not True:
            raise ValueError("Editor operation requires separate explicit approval")
        document = self._document()
        if self._state["approved_hash"] != _hash(document):
            raise ValueError("Current storyboard must be reviewed and approved before editor operations")
        with Analyzer(self.config) as app:
            if self._state["approved_evidence"] != self._evidence(app, document):
                raise ValueError("Source evidence or feedback changed; review and approve the storyboard again")
        return document

    def _editor(self):
        editor = self._state["editor"]
        if not editor or editor["storyboard_hash"] != self._state["approved_hash"]:
            raise ValueError("Create an editor project for the current approved storyboard first")
        return self._path(editor["path"], "outputs", ".fcproj")

    def _native_hash(self, path):
        # Native project JSON has the same bound as the existing FilmCraft adapter.
        with path.open("rb") as stream:
            raw = stream.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            raise ValueError("Native project exceeds size limit")
        return hashlib.sha256(raw).hexdigest()

    async def _operation(self, name, output, invoke, success):
        self._state["operation"] = {"kind": name, "status": "running", "report": None, "error": None}
        if name == "preview":
            self._state.update(preview=None, export=None)
        self._persist()
        report = None
        try:
            report = await invoke()
            if not isinstance(report, dict) or report.get("status") not in ("succeeded", "failed"):
                raise ValueError("Editor returned an invalid operation report")
            if report["status"] == "succeeded":
                if not output.is_file() or output.is_symlink() or output.stat().st_size == 0:
                    raise ValueError("Editor reported success without a saved output")
                success()
        except BaseException as error:
            interrupted = isinstance(error, (KeyboardInterrupt, SystemExit)) or type(error).__name__ == "CancelledError"
            if not isinstance(report, dict):
                report = {"schema_version": VERSION, "output": str(output), "operations": []}
            report.update(status="interrupted" if interrupted else "failed",
                          error=str(error)[:2000] or type(error).__name__)
            report.setdefault("warnings", []).append("External changes may have occurred; no automatic retry was performed.")
            self._record_report(name, report)
            raise
        self._record_report(name, report)
        return report

    def _record_report(self, name, report):
        path = self._new_path("reports", name, ".json")
        # The caller receives a normal path; manifest references stay relative.
        report["report_path"] = str(path)
        reference = self._write_new(path, report, limit=MAX_REPORT_BYTES)
        self._state["reports"].append(reference)
        self._state["operation"] = {"kind": name, "status": report["status"], "report": reference,
                                    "error": report.get("error")}
        self._persist()

    async def create_editor(self, executable, *, approved=False, fps="24", width=1920, height=1080):
        from .filmcraft import create
        document = self._approved(approved)
        output = self._new_path("outputs", "editor", ".fcproj")

        async def invoke():
            with Analyzer(self.config) as app:
                return await create(app, document, executable=executable, output=output,
                                    roots=[self.source], approved=True, fps=fps, width=width,
                                    height=height, timeout=self.config.timeout)

        def success():
            self._state.update(editor={"path": output.relative_to(self.directory).as_posix(),
                                       "storyboard_hash": self._state["approved_hash"]}, preview=None, export=None)

        return await self._operation("create", output, invoke, success)

    async def preview_editor(self, executable, *, approved=False, seconds=0.0):
        from .filmcraft import render
        self._approved(approved)
        native = self._editor()
        before = self._native_hash(native)
        output = self._new_path("outputs", "preview", ".png")

        async def invoke():
            return await render(executable=executable, project=native, output=output,
                                roots=[self.source], approved=True, seconds=seconds,
                                timeout=max(300, self.config.timeout))

        def success():
            if self._native_hash(native) != before:
                raise ValueError("Native project changed during preview; preview again before export")
            self._state["preview"] = {"path": output.relative_to(self.directory).as_posix(), "project_hash": before}

        return await self._operation("preview", output, invoke, success)

    async def export_editor(self, executable, *, approved=False):
        from .filmcraft import render
        self._approved(approved)
        native = self._editor()
        preview = self._state["preview"]
        if not preview or preview["project_hash"] != self._native_hash(native):
            raise ValueError("Native project needs a current successful preview before export")
        self._path(preview["path"], "outputs", ".png")
        before = preview["project_hash"]
        output = self._new_path("outputs", "export", ".mp4")

        async def invoke():
            return await render(executable=executable, project=native, output=output,
                                roots=[self.source], approved=True, timeout=max(300, self.config.timeout))

        def success():
            if self._native_hash(native) != before:
                raise ValueError("Native project changed during export; output retained but not marked current")
            self._state["export"] = output.relative_to(self.directory).as_posix()

        return await self._operation("export", output, invoke, success)
