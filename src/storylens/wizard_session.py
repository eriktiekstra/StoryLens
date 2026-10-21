"""One project workflow shared by the terminal and loopback browser interfaces."""

from contextlib import contextmanager
from dataclasses import replace
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

from .config import Config
from .workflow import Project, preflight


_MODEL_KEYS = ("embedding_model", "vision_model", "audio_transcription_model")
_SETTING_KEYS = (*_MODEL_KEYS, "ollama_endpoint", "audio_transcription_device",
                 "audio_transcription_language", "timeout", "semantic_timeout")


def installation_instructions():
    """Explain external tools without executing package managers on the user's behalf."""
    if sys.platform == "darwin":
        ffmpeg = "macOS: install Homebrew from https://brew.sh if needed, then run brew install ffmpeg. The ffmpeg package supplies both ffmpeg and ffprobe."
    elif sys.platform == "win32":
        ffmpeg = "Windows: install a trusted FFmpeg build, for example winget install --id Gyan.FFmpeg --exact, then reopen the terminal."
    else:
        ffmpeg = "Linux: install FFmpeg using your distribution's package manager; on Ubuntu/Debian: sudo apt install ffmpeg."
    return [ffmpeg,
            "HEIC/HEIF photos need the optional decoder in the same environment as StoryLens: python -m pip install 'pillow-heif>=1,<2'.",
            "Keep your project folder on local storage, separate from the media folder. Original media is never modified."]


def _basic_config(config):
    return replace(config, embedding_model="", vision_model="", audio_transcription_model="",
                   ollama_endpoint=Config(config.database).ollama_endpoint)


def _entrypoint():
    executable = Path(sys.executable).with_name("storylens.exe" if sys.platform == "win32" else "storylens")
    if executable.is_file():
        return str(executable), []
    return sys.executable, ["-m", "storylens"]


class WizardSession:
    """Serialize UI mutations and keep background analysis independent of rendering."""

    def __init__(self, config):
        self.config = config
        self._command, self._command_args = _entrypoint()
        self.project = None
        self.lock = threading.RLock()
        self._busy = False
        self._cancel = threading.Event()
        self._worker = None
        self._status = "idle"
        self._summary = None
        self._error = None
        self._operation = "technical"
        self._started = None
        self._elapsed = 0.0
        self._activity = "Choose your media and project folders."
        self._checked = {"counts": {}, "warnings": [], "tools": {}}
        self._existing = False

    @contextmanager
    def _exclusive(self, activity):
        with self.lock:
            if self._busy:
                raise ValueError("An operation is already running. Wait for it to finish or stop analysis first.")
            self._busy = True
            previous = self._activity
            self._activity = activity
        try:
            yield
        finally:
            with self.lock:
                self._busy = False
                self._activity = previous

    def _require_project(self):
        if self.project is None:
            raise ValueError("Choose a project first.")
        return self.project

    def readiness(self, source=""):
        if not isinstance(source, str):
            raise ValueError("The media folder must be a path.")
        if not source.strip():
            result = {"counts": {}, "errors": [], "warnings": [],
                      "tools": {name: shutil.which(getattr(self.config, name))
                                for name in ("ffmpeg", "ffprobe")}}
        else:
            result = preflight(Path(source).expanduser(), _basic_config(self.config))
        suggested = Path.home() / "storylens-projects" / (Path(source).expanduser().name if source.strip() else "project")
        return dict(result, installation_instructions=installation_instructions(),
                    suggested_project=str(suggested))

    def open(self, project, source=""):
        if not isinstance(project, str) or not project.strip():
            raise ValueError("Choose a project folder. StoryLens will create it if it does not exist.")
        if not isinstance(source, str):
            raise ValueError("The media folder must be a path.")
        with self._exclusive("Checking folders and installed tools…"):
            path = Path(project).expanduser()
            if path.exists():
                if not (path / "project.json").is_file():
                    raise ValueError("This folder exists but is not a StoryLens project. Choose a new project folder inside it; existing contents will not be overwritten.")
                opened = Project.open(path)
                if source.strip() and Path(source).expanduser().resolve() != opened.source:
                    raise ValueError("Source differs from the saved project source. Reopen without a media folder, or choose a new project.")
                existing = True
            else:
                if not source.strip():
                    raise ValueError("Choose the existing folder containing your photos and videos before creating a project.")
                checked = self.readiness(source)
                if checked["errors"]:
                    raise ValueError("Cannot start yet: " + "; ".join(checked["errors"]))
                opened = Project.create(path, source, self.config)
                existing = False
            checked = preflight(opened.source, _basic_config(opened.config))
            # Opening existing results must work even if a dependency is no longer installed.
            checked["warnings"].extend(checked["errors"])
            with self.lock:
                self.project, self._checked, self._existing = opened, checked, existing
                technical, ai = opened.analysis_status("technical"), opened.analysis_status("ai")
                self._operation = "ai" if ai["status"] != "not_started" else "technical"
                saved = ai if self._operation == "ai" else technical
                self._status = "ready" if saved["status"] == "not_started" else saved["status"]
                self._summary, self._error = saved["summary"], saved["error"]
                self._started, self._elapsed = None, 0.0
        self._activity = "Project opened. Choose an action; completed results are retained."
        return self.snapshot()

    def snapshot(self):
        with self.lock:
            project = self.project
            summary = self._summary or {}
            technical = project.analysis_status("technical") if project else {"status": "not_started", "summary": None, "error": None}
            ai = project.analysis_status("ai") if project else {"status": "not_started", "summary": None, "error": None}
            details = None
            if project:
                settings = {key: getattr(project.config, key) for key in _SETTING_KEYS}
                details = {"source": str(project.source), "directory": str(project.directory),
                           "database": str(project.config.database),
                           "executable": self._command, "executable_args": list(self._command_args),
                           "mode": project.mode, "depth": project.depth, "settings": settings}
            elapsed = time.monotonic() - self._started if self._started is not None else self._elapsed
            errors = summary.get("errors", [])
            recovery = []
            activity = self._activity
            if project and self._summary is not None and self._busy and self._status == "running" and not self._cancel.is_set():
                activity = project.activity or activity
            for item in errors:
                error = item.get("error", "").lower()
                if "timeout" in error or "timed out" in error:
                    hint = "Increase the saved timeout in Settings, then retry. Completed stages will be reused."
                elif "identify image" in error and Path(item.get("path", "")).suffix.lower() in (".heic", ".heif"):
                    hint = "Install the optional HEIC/HEIF decoder in the StoryLens environment, then retry."
                else:
                    hint = "Check that this file is readable and its format is supported, then retry."
                recovery.append(dict(item, action=hint))
            return {"project": details, "counts": dict(self._checked["counts"]),
                    "warnings": list(self._checked["warnings"]), "tools": dict(self._checked["tools"]),
                    "installation_instructions": installation_instructions(), "existing": self._existing,
                    "status": self._status, "summary": summary, "errors": errors,
                    "error": self._error, "operation": self._operation,
                    "elapsed": round(elapsed, 1), "activity": activity,
                    "technical": technical, "ai": ai, "busy": self._busy, "recovery": recovery}

    def configure(self, settings):
        if not isinstance(settings, dict):
            raise ValueError("Settings must be an object.")
        if set(settings) - {*_SETTING_KEYS, "mode", "depth"}:
            raise ValueError("Unknown wizard settings: " + ", ".join(sorted(set(settings) - {*_SETTING_KEYS, "mode", "depth"})))
        with self._exclusive("Saving project settings…"):
            project = self._require_project()
            selected = {key: getattr(project.config, key) for key in _MODEL_KEYS}
            selected.update({key: value for key, value in settings.items() if key in _MODEL_KEYS})
            values = dict(settings)
            models_changed = bool(settings.keys() & set(_MODEL_KEYS))
            inferred_mode = "local-ai" if any(selected.values()) else "technical"
            values.setdefault("mode", inferred_mode if models_changed else project.mode)
            if "depth" not in values:
                values["depth"] = ("standard" if selected["vision_model"] else "fast") if models_changed else project.depth
                if values["mode"] == "technical":
                    values["depth"] = "fast"
            project.configure(**values)
            with self.lock:
                self._operation = "ai" if project.mode == "local-ai" else "technical"
                saved = project.analysis_status(self._operation)
                self._status = "ready" if saved["status"] == "not_started" else saved["status"]
                self._summary, self._error = saved["summary"], saved["error"]
        return self.snapshot()

    def analyze(self, operation="technical"):
        if operation not in ("technical", "ai"):
            raise ValueError("Choose basic analysis or local AI analysis.")
        with self.lock:
            project = self._require_project()
            if self._busy:
                raise ValueError("An operation is already running.")
            self._busy, self._status, self._operation = True, "running", operation
            self._summary, self._error = None, None
            self._cancel.clear()
            self._started = time.monotonic()
            self._activity = "Analyzing media locally; completed stages are saved as they finish."

        def work():
            def update(summary):
                with self.lock:
                    self._summary = summary
            try:
                summary = project.analyze(progress=update, operation=operation, cancelled=self._cancel.is_set)
                with self.lock:
                    self._summary = summary
                    self._status = "partial" if summary.get("failed") else "completed"
                    self._activity = "Some files need attention. Completed stages are retained." if summary.get("failed") else "Analysis complete. Your results are saved."
            except BaseException as error:
                with self.lock:
                    self._error = str(error)[:2000] or type(error).__name__
                    saved = project.analysis_status(operation)
                    self._status = saved["status"] if saved["status"] in ("interrupted", "failed") else "failed"
                    self._activity = "Analysis stopped. Resume to reuse completed stages."
            finally:
                with self.lock:
                    self._elapsed = time.monotonic() - self._started
                    self._started, self._busy = None, False
        self._worker = threading.Thread(target=work, name="storylens-analysis", daemon=True)
        self._worker.start()
        return {"status": "running"}

    def cancel(self):
        with self.lock:
            if self._status != "running":
                raise ValueError("No analysis is running.")
            self._cancel.set()
            self._activity = "Stopping at a safe checkpoint. The current model call may need to finish first."
        return self.snapshot()

    def wait(self, timeout=None):
        worker = self._worker
        if worker is not None:
            worker.join(timeout)
        return self.snapshot()

    def ai_options(self):
        from . import local_ai
        return local_ai.options()

    def check_ai(self, settings):
        from . import local_ai
        with self._exclusive("Checking local AI setup…"):
            return local_ai.check(self._model_settings(settings))

    def sample_ai(self, settings):
        from . import local_ai
        with self._exclusive("Testing local models on a small sample; no collection analysis is changed…"):
            return local_ai.sample(self._require_project(), self._model_settings(settings))

    def _model_settings(self, settings):
        if not isinstance(settings, dict):
            raise ValueError("Model settings must be an object.")
        config = self.project.config if self.project else self.config
        values = {key: getattr(config, key) for key in _SETTING_KEYS}
        values.update(settings)
        return values

    def install_model(self, capability, consent, settings=None):
        from . import local_ai
        with self._exclusive("Downloading the selected model. No media is sent to the model host…"):
            return local_ai.install(capability, consent=consent, settings=self._model_settings(settings or {}))

    def ai_profile(self, action="load", settings=None):
        from . import local_ai
        if action not in ("load", "save"):
            raise ValueError("Choose load or save for reusable model settings.")
        with self._exclusive("Reading reusable model settings…" if action == "load" else "Saving reusable model settings…"):
            if action == "load":
                return {"settings": local_ai.load_profile()}
            values = self._model_settings(settings or {})
            local_ai.save_profile(values)
            return {"settings": local_ai.load_profile()}

    def browse(self, path=""):
        if not isinstance(path, str):
            raise ValueError("The folder path must be text.")
        directory = Path(path).expanduser() if path.strip() else Path.home()
        directory = directory.resolve(strict=True)
        if not directory.is_dir():
            raise ValueError("Choose a directory, not a file.")
        directories, truncated = [], False
        with os.scandir(directory) as entries:
            for entry in entries:
                if not entry.is_symlink() and entry.is_dir(follow_symlinks=False):
                    directories.append({"name": entry.name, "path": str(directory / entry.name)})
                    if len(directories) >= 500:
                        truncated = True
                        break
        directories.sort(key=lambda item: item["name"].casefold())
        return {"path": str(directory), "parent": str(directory.parent),
                "directories": directories, "truncated": truncated}

    def diagnostics(self):
        with self._exclusive("Saving a private diagnostics report…"):
            return self._require_project().diagnostics()

    def open_folder(self):
        with self._exclusive("Opening the project folder…"):
            path = str(self._require_project().directory)
            if sys.platform == "win32":
                os.startfile(path)
            else:
                executable = shutil.which("open" if sys.platform == "darwin" else "xdg-open")
                if not executable:
                    raise ValueError("A folder opener is not installed. Open this folder manually: " + path)
                subprocess.Popen([executable, path], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"path": path}

    def handoff(self, inspect=False):
        if type(inspect) is not bool:
            raise ValueError("Media inspection consent must be true or false.")
        with self.lock:
            project = self._require_project()
            command, prefix = self._command, self._command_args
            args = list(prefix)
            args += ["--database", str(project.config.database)]
            for key in ("embedding_model", "vision_model", "audio_transcription_model"):
                if value := getattr(project.config, key):
                    args += ["--" + key.replace("_", "-"), value]
            if project.config.vision_model:
                args += ["--ollama-endpoint", project.config.ollama_endpoint]
            args += ["serve", "--allow-root", str(project.source)]
            config = {"mcpServers": {"storylens": {"command": command or sys.executable, "args": args}}}
            permission = ("I explicitly allow you to inspect the original media in this source folder for clip selection and visual continuity. "
                          if inspect else "Do not open or decode original media without asking me for permission first. ")
            prompt = (f"Use StoryLens MCP to review the analyzed collection in {project.directory}. "
                      f"The source folder is {project.source}. " + permission +
                      "Do not send media, frames, transcripts, metadata, paths, or private analysis to a cloud provider without my separate explicit approval. "
                      "Check analysis status and failures before relying on evidence; distinguish technical measurements, AI hypotheses, and imported/user-provided information. "
                      "Use the official FilmCraft MCP tools advertised by my configured server; do not invent tool names or assume unsupported features. "
                      "Help me plan an editable home video: ask for target duration and creative preferences, select useful source clips and photo holds, preserve conversations, "
                      "and propose transitions and music where supported. Ask me to supply or approve music; do not assume licensing. "
                      "Before creating the native project, show me the plan and ask for approval. Leave the result editable with original media referenced. "
                      "Do not export a final movie unless I separately approve it.")
            instructions = ["Use an existing MCP-capable agent you trust; StoryLens does not launch or connect to it.",
                            "Install StoryLens's optional MCP dependency in the same environment. From a checkout: python -m pip install -e '.[mcp]'; otherwise: python -m pip install 'storylens[mcp] @ https://github.com/eriktiekstra/StoryLens/archive/refs/heads/main.zip'.",
                            "Add the generated StoryLens server configuration to that host, and configure the official FilmCraft MCP separately.",
                            "The server configuration authorizes access to this source root. The copied prompt is guidance, not a filesystem access control.",
                            "Review the prompt before sending it. Media inspection permission does not authorize disclosure to a cloud provider."]
            return {"prompt": prompt, "config": config, "instructions": instructions}
