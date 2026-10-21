"""Friendly standard-library terminal client for the shared wizard session."""

from contextlib import contextmanager
import builtins
import json
import os
from pathlib import Path
import time
import unicodedata

from .wizard_session import WizardSession


def _safe(value, *, multiline=False):
    text = str(value)
    return "".join(
        character if (multiline and character == "\n") or
        (ord(character) >= 32 and not 127 <= ord(character) <= 159
         and unicodedata.category(character) != "Cf" and character not in "\u2028\u2029")
        else f"\\u{ord(character):04x}"
        for character in text
    )


def print(*values, **kwargs):
    """Never pass terminal escapes from paths, errors or model output to a TTY."""
    builtins.print(*(_safe(value, multiline=True) for value in values), **kwargs)


class _Back(Exception):
    pass


class _Quit(Exception):
    pass


def _heading(title):
    print("\n" + "─" * 62)
    print("  " + title)
    print("─" * 62)


def _ask(label, default=""):
    try:
        value = input(_safe(label + (f" [{default}]" if default else "") + ": ")).strip()
    except EOFError:
        raise _Quit from None
    if value.lower() in (":back", ":cancel"):
        raise _Back
    if value.lower() in (":quit", ":q"):
        raise _Quit
    return value or str(default)


def _choice(label, options, default=""):
    while True:
        value = _ask(label, default).lower()
        if value in options:
            return value
        print("Choose " + ", ".join(options) + ". Use :back to go back or :quit to save and leave.")


@contextmanager
def _path_completion():
    """Temporarily enable directory completion without changing shell history."""
    try:
        import readline
    except ImportError:
        yield
        return
    previous, delimiters = readline.get_completer(), readline.get_completer_delims()
    matches = []

    def complete(text, state):
        if state == 0:
            matches.clear()
            expanded = Path(text).expanduser()
            parent = expanded if text.endswith(os.sep) else expanded.parent
            prefix = "" if text.endswith(os.sep) else expanded.name
            try:
                for item in sorted(parent.iterdir()):
                    if item.name.startswith(prefix) and item.is_dir() and _safe(str(item)) == str(item):
                        shown = str(item)
                        if text.startswith("~"):
                            shown = "~" + shown[len(str(Path.home())):]
                        matches.append(shown + os.sep)
            except OSError:
                pass
        return matches[state] if state < len(matches) else None

    readline.set_completer_delims("\n")
    readline.set_completer(complete)
    readline.parse_and_bind("bind ^I rl_complete" if "libedit" in (readline.__doc__ or "") else "tab: complete")
    try:
        yield
    finally:
        readline.set_completer(previous)
        readline.set_completer_delims(delimiters)


def _path(label, default=""):
    with _path_completion():
        value = _ask(label, default)
    # Accept paths pasted from a shell without interpreting escapes or variables.
    if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return str(Path(value).expanduser()) if value else ""


def choose_interface():
    """Prompt only after the CLI has established that both streams are TTYs."""
    _heading("StoryLens · your media, understood")
    print("Your originals stay where they are. Choose how to work:")
    print("  1  Open browser (recommended) — local, visual guidance")
    print("  2  Continue in terminal — no browser needed")
    print("  q  Quit")
    try:
        choice = _choice("Continue", ("1", "2", "web", "terminal", "q"), "1")
    except (_Back, _Quit, KeyboardInterrupt):
        return None
    return None if choice == "q" else "terminal" if choice in ("2", "terminal") else "web"


def _display(value, indent="  "):
    """Render small structured controller responses without hiding evidence."""
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                print(indent + _safe(key).replace("_", " ") + ":")
                _display(item, indent + "  ")
            elif item is not None and item != "":
                print(indent + _safe(key).replace("_", " ") + ": " + _safe(item))
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                _display(item, indent)
            else:
                print(indent + "• " + _safe(item))
    elif value is not None:
        print(indent + _safe(value))


def _quote(path):
    value = str(path)
    return '"' + value.replace('"', '\\"') + '"' if os.name == "nt" else "'" + value.replace("'", "'\"'\"'") + "'"


class TerminalWizard:
    def __init__(self, config):
        self.session = WizardSession(config)

    def _readiness(self, source=""):
        _heading("Readiness · local tools")
        report = self.session.readiness(source)
        _display(report)
        print("Nothing is installed automatically. Run any suggested commands in a separate terminal,")
        print("then return here. Images can be analyzed without local AI models.")
        return report

    def _open(self, project=None, source=None):
        if project:
            try:
                self.session.open(str(project), str(source or ""))
                return
            except (OSError, ValueError, RuntimeError) as error:
                print("Could not open project: " + _safe(error))
        while True:
            _heading("1 · Open or create a private project")
            print("Source media is read-only. Pick a separate folder for saved analysis.")
            print("Paths support Tab completion where available; pasted quoted paths are accepted.")
            print("Use :back / :cancel to go back, or :quit to leave.")
            print("  1  Open an existing StoryLens project")
            print("  2  Create a new project")
            print("  3  Check tools and installation guidance")
            print("  q  Quit")
            try:
                choice = _choice("Choose", ("1", "2", "3", "q"), "2")
                if choice == "q":
                    raise _Quit
                if choice == "3":
                    self._readiness()
                    continue
                media = ""
                if choice == "2":
                    media = _path("Where are your photos, videos or audio?", str(source or ""))
                    if not media:
                        print("Choose the existing folder containing your media.")
                        continue
                    self._readiness(media)
                    suggested = Path.home() / "storylens-projects" / (Path(media).name or "my-media")
                    print("We'll create a separate local folder for saved analysis, not change your originals.")
                    print("Press Enter to use the suggested project folder, or choose another location.")
                    directory = _path("Save this project in", str(project or suggested))
                    if _choice("Continue and create project?", ("y", "n"), "y") != "y":
                        continue
                else:
                    directory = _path("Existing StoryLens project folder", str(project or ""))
                    if not directory:
                        print("Choose your saved project folder to continue.")
                        continue
                self.session.open(directory, media)
                return
            except _Back:
                continue
            except (OSError, ValueError, RuntimeError) as error:
                print("Could not open project: " + _safe(error))
                print("Check the paths and readiness guidance, then try again.")

    def _results(self, state):
        project = state.get("project") or {}
        _heading("Your StoryLens project")
        print("Media:   " + _safe(project.get("source", "")))
        print("Saved:   " + _safe(project.get("directory", "")))
        counts = state.get("counts") or {}
        print("  " + " · ".join(f"{counts.get(key, 0)} {label}" for key, label in
                                (("image", "photos"), ("video", "videos"), ("audio", "audio files"))))
        labels = {"not_started": "Not started", "completed": "Ready", "partial": "Some files need attention",
                  "interrupted": "Paused — resume when ready", "failed": "Needs attention", "running": "In progress"}
        for name, label in (("technical", "Basic analysis"), ("ai", "Optional local AI")):
            saved = state.get(name) or {}
            print(label + ": " + labels.get(saved.get("status"), "Not started"))
            summary = saved.get("summary") or {}
            if summary:
                print(f"  {summary.get('analyzed', 0)} analyzed · {summary.get('cache_hits', 0)} reused · "
                      f"{summary.get('failed', 0)} need attention")
        if state.get("error") or state.get("errors"):
            print("See Results & recovery for errors and next steps.")
        print("Your originals are untouched. Results are saved automatically.")

    def _analyze(self, operation="technical"):
        _heading("Analyzing locally" if operation == "technical" else "Enriching with local AI")
        print("Ctrl-C cancels safely; completed files remain saved for the next attempt.")
        self.session.analyze(operation)
        last = None
        cancelled = False
        while True:
            try:
                state = self.session.snapshot()
                activity = _safe(state.get("activity") or "Working locally")
                elapsed = float(state.get("elapsed") or 0)
                summary = state.get("summary") or {}
                progress = (f"{int(elapsed)}s · {activity} · "
                            f"{summary.get('analyzed', 0)} analyzed · "
                            f"{summary.get('cache_hits', 0)} cached · {summary.get('failed', 0)} failed")
                if progress != last:
                    print("  " + progress, flush=True)
                    last = progress
                if not state.get("busy"):
                    break
                time.sleep(1)
            except KeyboardInterrupt:
                if not cancelled:
                    self.session.cancel()
                    cancelled = True
                    print("\nCancellation requested. Waiting for the current operation to stop safely…", flush=True)
                else:
                    print("\nStill stopping safely; saved results will be kept.", flush=True)
        if state.get("errors") or state.get("error"):
            self._errors(state)
        print("Analysis stopped." if cancelled else "Analysis finished. Results are saved in your project folder.")

    def _errors(self, state=None):
        state = state or self.session.snapshot()
        _heading("Recovery · failed files and next steps")
        errors, seen = [], set()
        candidates = list(state.get("errors") or [])
        for name in ("technical", "ai"):
            candidates.extend(((state.get(name) or {}).get("summary") or {}).get("errors", []))
        for item in candidates:
            key = (str(item.get("path", "")), str(item.get("stage", "")), str(item.get("error", ""))) if isinstance(item, dict) else str(item)
            if key not in seen:
                seen.add(key)
                errors.append(item)
        if not errors:
            print("No per-file errors recorded.")
        for item in errors:
            if isinstance(item, dict):
                print("\n  " + _safe(item.get("path") or "Collection"))
                print("    " + _safe(item.get("error") or item.get("message") or "Analysis could not complete."))
                if item.get("action"):
                    print("    Next step: " + _safe(item["action"]))
            else:
                print("  " + _safe(item))
        if state.get("error"):
            print("Operation error: " + _safe(state["error"]))
        _display(state.get("recovery") or [])
        print("For timeouts, increase limits in Project settings. For missing tools, see Readiness.")
        print("For unreadable/moved files, restore access to the source, then retry.")
        print("Retry rechecks the collection and reuses successful cached stages; it does not force a full rebuild.")

    def _recovery(self):
        state = self.session.snapshot()
        self._results(state)
        self._errors(state)
        print("  1  Analyze again / Retry failed basic files (reuse caches)")
        print("  2  Retry local AI enrichment (reuse caches)")
        print("  3  Edit timeout settings")
        print("  4  Technical details")
        print("  b  Back")
        choice = _choice("Results & recovery", ("1", "2", "3", "4", "b"), "b")
        if choice in ("1", "2"):
            self._analyze("technical" if choice == "1" else "ai")
        elif choice == "3":
            self._settings()
        elif choice == "4":
            _heading("Technical details")
            _display({name: state.get(name) for name in ("technical", "ai")})
            _display(state.get("warnings") or [])

    def _settings(self):
        _heading("Project settings · recover from slow files")
        current = (self.session.snapshot().get("project") or {}).get("settings") or {}
        print("Limits are seconds per tool/request, not a deadline for the whole collection.")
        values = {}
        for key, label, default in (("timeout", "Media-tool timeout", 600),
                                    ("semantic_timeout", "Local vision request timeout", 120)):
            while True:
                text = _ask(label, str(current.get(key, default)))
                try:
                    values[key] = float(text)
                    break
                except ValueError:
                    print("Enter a positive number of seconds, or :back to leave unchanged.")
        if _choice("Save these settings?", ("y", "n"), "y") == "y":
            self.session.configure(values)
            print("Saved. Retry analysis to reuse completed work with the new limits.")

    def _ai(self):
        while True:
            _heading("Optional local AI")
            print("Add visual search, image descriptions, or speech transcription on this computer.")
            print("Basic results stay available. You can skip this now and return later.")
            print("\n  1  Guided setup (recommended)")
            print("  2  Reuse a saved local AI profile")
            print("  3  Advanced model controls")
            print("  b  Back / skip")
            choice = _choice("Continue", ("1", "2", "3", "b"), "1")
            if choice == "b":
                return
            try:
                if choice == "1":
                    self._guided_ai()
                elif choice == "2":
                    profile = self.session.ai_profile("load")
                    if not profile.get("settings"):
                        print("No saved profile yet. Guided setup can save one for your next project.")
                    else:
                        self._apply_ai(profile["settings"])
                elif choice == "3":
                    self._ai_advanced()
            except _Back:
                continue
            except (OSError, ValueError, RuntimeError) as error:
                print("Local AI setup: " + _safe(error))

    def _guided_ai(self):
        _heading("Local AI · choose what would help")
        pending = dict(self.session.snapshot()["project"]["settings"])
        fields = {"embeddings": "embedding_model", "vision": "vision_model",
                  "transcription": "audio_transcription_model"}
        selected = []
        for item in self.session.ai_options().get("capabilities", []):
            print("\n" + item["title"] + " — " + item.get("description", ""))
            key = fields[item["id"]]
            if _choice("Include this capability?", ("y", "n"), "y" if pending.get(key) else "n") == "y":
                selected.append(item)
            else:
                pending[key] = ""
        if not selected:
            print("No AI selected. Keep using basic analysis; no settings have changed.")
            return
        for item in selected:
            key = fields[item["id"]]
            _heading("Set up " + item["title"].lower())
            print("Recommended: " + _safe(item.get("recommendation", "")))
            print(_safe(item.get("hardware", "")))
            print("Suggested model: " + _safe(item.get("model_path", "")))
            print("Dependency guidance: " + _safe(item.get("install_command", "")))
            existing = pending.get(key) or (item.get("model_path") if item.get("installed") else "")
            if existing:
                print("  1  Use detected/current model: " + _safe(existing))
                print("  2  Download the recommendation")
                print("  3  Use another installed model")
                action = _choice("Model", ("1", "2", "3"), "1")
            else:
                print("No ready model detected.")
                print("  2  Download the recommendation")
                print("  3  Use an already installed model")
                action = _choice("Model", ("2", "3"), "2")
            if action == "1":
                pending[key] = existing
            elif action == "2":
                print(f"Approximate download: {item.get('download_bytes', 0) / 1_000_000:g} MB.")
                print("This contacts the model provider, but never uploads your media.")
                if _choice("Consent to download this model now?", ("y", "n"), "n") != "y":
                    print("Download cancelled. Your project settings are unchanged.")
                    return
                result = self.session.install_model(item["id"], consent=True, settings=pending)
                installed = result.get("settings") or {}
                pending[key] = installed.get(key) or result.get("model_path", "")
                if key == "vision_model":
                    pending["ollama_endpoint"] = installed.get("ollama_endpoint", "http://127.0.0.1:11434")
            else:
                pending[key] = (_ask("Installed Ollama model name", item.get("model_path", ""))
                                if key == "vision_model" else _path("Installed model directory", item.get("model_path", "")))
        pending.pop("mode", None)
        pending.pop("depth", None)
        self._apply_ai(pending)

    def _apply_ai(self, settings):
        _heading("Local AI · check before saving")
        check = self.session.check_ai(settings)
        if not check.get("ready"):
            print("Setup needs attention. Nothing has changed in your project.")
            _display(check.get("errors") or ["Choose at least one installed local model."])
            print("Follow the dependency guidance, then return to setup or Advanced controls.")
            return
        print("The selected models and dependencies are available.")
        _display(check.get("warnings") or [])
        print("A small sample can check actual inference on local media before enrichment.")
        if _choice("Run the local sample now? (no media upload)", ("y", "n"), "n") == "y":
            sample = self.session.sample_ai(settings)
            if not sample.get("ready"):
                print("The sample could not complete. Your basic results are safe.")
                _display(sample.get("errors") or [])
                return
            print("Sample completed:")
            _display(sample.get("results") or [])
        if _choice("Save and apply these AI settings to this project?", ("y", "n"), "y") != "y":
            return
        self.session.configure(settings)
        print("Saved. Basic analysis is preserved.")
        if _choice("Save a reusable profile for other projects?", ("y", "n"), "n") == "y":
            self.session.ai_profile("save", settings=settings)
            print("Profile saved. Other projects are not changed.")
        if _choice("Enrich this project with local AI now?", ("y", "n"), "n") == "y":
            self._analyze("ai")

    def _ai_advanced(self):
        pending = dict((self.session.snapshot().get("project") or {}).get("settings") or {})
        while True:
            _heading("Optional local AI · add meaning without replacing basic results")
            print("Skip this any time. Basic analysis needs no model. Models run on this computer.")
            options = self.session.ai_options()
            capabilities = options.get("capabilities", [])
            print("Advanced controls let you change individual settings without running inference.")
            print("\n  1  Choose capabilities / edit model settings")
            print("  2  Download a recommended model (asks consent)")
            print("  3  Validate pending settings (no sample inference)")
            print("  4  Run an explicit sample on local media")
            print("  5  Save pending settings to this project")
            print("  6  Enrich analyzed project with saved local AI settings")
            print("  7  Save pending settings as reusable local profile")
            print("  8  Load reusable profile into pending settings")
            print("  b  Back / skip for now")
            choice = _choice("Local AI", ("1", "2", "3", "4", "5", "6", "7", "8", "b"), "b")
            if choice == "b":
                return
            try:
                if choice == "1":
                    pending = self._models(pending, capabilities)
                elif choice == "2":
                    self._download(capabilities, pending)
                elif choice == "3":
                    _display(self.session.check_ai(pending))
                elif choice == "4":
                    print("This explicitly runs configured local models on a small media sample.")
                    print("No source media is sent to a remote service.")
                    if _choice("Run sample now?", ("y", "n"), "n") == "y":
                        _display(self.session.sample_ai(pending))
                elif choice == "5":
                    self.session.configure(pending)
                    pending = dict(self.session.snapshot()["project"]["settings"])
                    print("Saved to this project. Basic results remain intact; inference has not started.")
                elif choice == "6":
                    print("Enrichment uses saved project settings, not unsaved edits above.")
                    if _choice("Run local AI enrichment now?", ("y", "n"), "n") == "y":
                        self._analyze("ai")
                elif choice == "7":
                    _display(self.session.ai_profile("save", settings=pending))
                    print("Reusable profile saved explicitly. Other projects do not change.")
                elif choice == "8":
                    loaded = self.session.ai_profile("load")
                    pending.update(loaded.get("settings") or {})
                    print("Profile loaded into pending settings. Validate, then Save to activate in this project.")
            except _Back:
                continue
            except (OSError, ValueError, RuntimeError) as error:
                print("Local AI setup: " + _safe(error))

    def _models(self, pending, capabilities):
        edited = dict(pending)
        recommendations = {item["id"]: item for item in capabilities}
        fields = (("embeddings", "Find similar images and search by meaning", "embedding_model", "Embedding model directory"),
                  ("vision", "Describe what appears in images/video", "vision_model", "Installed Ollama vision model name"),
                  ("transcription", "Turn speech into searchable text", "audio_transcription_model", "Transcription model directory"))
        for capability, title, key, label in fields:
            enabled = "y" if edited.get(key) else "n"
            if _choice(title + "?", ("y", "n"), enabled) == "n":
                edited[key] = ""
                continue
            recommended = recommendations.get(capability, {}).get("model_path") or ""
            default = str(edited.get(key) or recommended)
            edited[key] = _ask(label, default) if key == "vision_model" else _path(label, default)
        if edited.get("vision_model"):
            edited["ollama_endpoint"] = _ask("Ollama numeric loopback URL", edited.get("ollama_endpoint") or "http://127.0.0.1:11434")
        if edited.get("audio_transcription_model"):
            edited["audio_transcription_device"] = _choice("Transcription device", ("cpu", "cuda"), edited.get("audio_transcription_device") or "cpu")
            language = _ask("Speech language (ISO code, or 'auto')", edited.get("audio_transcription_language") or "auto")
            edited["audio_transcription_language"] = "" if language.lower() == "auto" else language
        # Let the shared controller resolve mode/depth from the selected capabilities.
        edited.pop("mode", None)
        edited.pop("depth", None)
        print("Pending only: validate or sample, then Save to apply these settings.")
        return edited

    def _download(self, capabilities, pending):
        _heading("Download a local model · explicit network consent")
        choices = {}
        for index, capability in enumerate(capabilities, 1):
            choices[str(index)] = capability
            print(f"  {index}  {capability.get('title', capability['id'])}")
        if not choices:
            print("No supported downloads are available. Use the installation guidance above.")
            return
        choice = _choice("Model (:back to cancel)", tuple(choices))
        item = choices[choice]
        _display(item)
        print("This downloads model weights from their provider to this computer.")
        print("It uses network bandwidth and disk space. It does NOT upload your source media.")
        if _choice("Do you consent to this model download?", ("y", "n"), "n") != "y":
            return
        result = self.session.install_model(item["id"], consent=True, settings=pending)
        _display(result)
        settings = result.get("settings") or {}
        pending.update(settings)
        print("Review model settings, validate, then explicitly Save to activate them for this project.")

    def _handoff(self):
        _heading("External agent handoff · StoryLens is the analyzer")
        print("Use a trusted external agent with the StoryLens MCP extra installed in this Python")
        print("environment, and FilmCraft MCP configured separately in that agent's host.")
        print("The agent, not this wizard, makes creative edits. StoryLens does not launch it.")
        print("Media-inspection consent changes the PROMPT ONLY; it is not filesystem access control.")
        print("A cloud agent/provider requires separate informed consent before sharing any media.")
        inspect = _choice("Allow original-media inspection in the handoff prompt?", ("y", "n"), "n") == "y"
        handoff = self.session.handoff(inspect=inspect)
        _display(handoff.get("instructions"))
        print("\nMCP host configuration (only in a host you trust):")
        print(json.dumps(handoff.get("config"), indent=2, ensure_ascii=False))
        print("\nCopy this prompt into your external agent:\n")
        print(handoff.get("prompt", ""))

    def _more(self):
        _heading("More options")
        print("  1  Project settings / timeouts")
        print("  2  Save diagnostics report")
        print("  3  Open output folder")
        print("  4  External agent + FilmCraft MCP handoff")
        print("  5  Readiness / installation guidance")
        print("  6  Open another project")
        print("  b  Back")
        choice = _choice("Choose", ("1", "2", "3", "4", "5", "6", "b"), "b")
        if choice == "1":
            self._settings()
        elif choice == "2":
            report = self.session.diagnostics()
            print("Diagnostics saved: " + _safe(report.get("path", "")))
            print("Review this local report for private paths before sharing it.")
        elif choice == "3":
            self.session.open_folder()
            print("Output folder: " + _safe(self.session.snapshot()["project"]["directory"]))
        elif choice == "4":
            self._handoff()
        elif choice == "5":
            self._readiness(self.session.snapshot()["project"]["source"])
        elif choice == "6":
            self._open()

    def run(self, project=None, source=None):
        _heading("StoryLens · private by design")
        print("Analyze locally, keep saved results, and add optional AI whenever you're ready.")
        print("No automatic downloads. Original media stays untouched.")
        interrupted = False
        try:
            self._open(project, source)
            while True:
                try:
                    state = self.session.snapshot()
                    self._results(state)
                    basic_ready = (state.get("technical") or {}).get("status") == "completed"
                    ai_status = (state.get("ai") or {}).get("status")
                    next_step = ("Analyze / resume basic analysis" if not basic_ready else
                                 "Prepare optional agent handoff" if ai_status == "completed" else
                                 "Review incomplete AI analysis" if ai_status in ("partial", "failed", "interrupted") else
                                 "Add optional local AI")
                    print("\n  1  Recommended: " + next_step)
                    print("  2  Results & recovery")
                    print("  3  Local AI setup")
                    print("  4  More options")
                    print("  q  Save and quit")
                    choice = _choice("Next step", ("1", "2", "3", "4", "q"), "1")
                    if choice == "q":
                        break
                    if choice == "1":
                        if not basic_ready:
                            self._analyze()
                        elif ai_status == "completed":
                            self._handoff()
                        elif ai_status in ("partial", "failed", "interrupted"):
                            self._recovery()
                        else:
                            self._ai()
                    elif choice == "2":
                        self._recovery()
                    elif choice == "3":
                        self._ai()
                    elif choice == "4":
                        self._more()
                except (_Back, KeyboardInterrupt):
                    print("\nBack to project. Saved results are unchanged.")
                except (OSError, ValueError, RuntimeError) as error:
                    print("Could not complete that step: " + _safe(error))
                    print("Your saved results remain available. Correct the settings or use diagnostics.")
        except KeyboardInterrupt:
            interrupted = True
        except (_Quit, _Back):
            pass
        state = self.session.snapshot()
        project = state.get("project") or {}
        if project:
            print("\nSaved project: " + _safe(project.get("directory", "")))
            executable = project.get("executable") or "storylens"
            command = [_quote(executable), *(_quote(value) for value in project.get("executable_args", []))]
            resume = " ".join(command) + " wizard --terminal --project " + _quote(project["directory"])
            print("Resume: " + ("& " if os.name == "nt" else "") + _safe(resume))
        print("Goodbye.")
        unresolved = any(
            (state.get(name) or {}).get("status") in ("failed", "partial", "interrupted")
            or (((state.get(name) or {}).get("summary") or {}).get("failed") or 0) > 0
            for name in ("technical", "ai")
        )
        return 130 if interrupted else 2 if unresolved else 0


def run(config, *, project=None, source=None):
    return TerminalWizard(config).run(project, source)
