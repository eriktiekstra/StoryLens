"""A line-oriented, opt-in terminal workflow over private persisted projects."""

import asyncio
from copy import deepcopy
from dataclasses import replace
import math
from pathlib import Path
import sys
import time
import webbrowser

from .review import contact_sheets
from .workflow import Project, preflight


def _safe(value):
    """Keep filenames, model output and errors from becoming terminal commands."""
    return "".join(character if character.isprintable() else
                   character.encode("unicode_escape").decode("ascii")
                   for character in str(value))


def _say(value=""):
    print(_safe(value), flush=True)


def _ask(prompt, default=None):
    suffix = f" [{_safe(default)}]" if default is not None else ""
    value = input(prompt + suffix + ": ").strip()
    return value if value else (str(default) if default is not None else "")


def _choice(prompt, choices, default=None):
    while True:
        answer = _ask(prompt, default).lower()
        if answer in choices:
            return answer
        _say("Choose " + ", ".join(choices) + ".")


def _confirm(prompt):
    return _choice(prompt + " (y/n)", ("y", "n"), "n") == "y"


def _number(prompt, default=None, *, zero=False, signed=False):
    while True:
        try:
            value = float(_ask(prompt, default))
            if not math.isfinite(value) or (not signed and (value < 0 or (not zero and value == 0))):
                raise ValueError
            return value
        except ValueError:
            qualification = "" if signed else ("nonnegative " if zero else "positive ")
            _say("Enter a finite " + qualification + "number.")


def _index(prompt, count):
    while True:
        try:
            value = int(_ask(prompt))
            if not 1 <= value <= count:
                raise ValueError
            return value - 1
        except ValueError:
            _say(f"Enter an entry number from 1 to {count}.")


def _filename(media):
    references = media.get("paths", [])
    return Path(references[0]["path"]).name if references else media.get("id", "unavailable")


def _summary(summary):
    _say("Indexed {indexed}/{discovered}; analyzed {analyzed}; cache hits {cache_hits}; failed {failed}".format(
        **{key: summary.get(key, 0) for key in ("indexed", "discovered", "analyzed", "cache_hits", "failed")}))


def _preflight(source, config, mode, depth):
    _say("3. Preflight (availability is not GPU or codec certification)")
    _say(f"Configured ffprobe: {config.ffprobe}; ffmpeg: {config.ffmpeg}")
    report = preflight(source, config, mode=mode, depth=depth)
    counts = report.get("counts", {})
    _say("Media: " + ", ".join(f"{kind}={counts.get(kind, 0)}" for kind in ("image", "video", "audio")))
    for name, available in report.get("tools", {}).items():
        _say(f"Tool {name}: {available}")
    for warning in report.get("warnings", []):
        _say("Warning: " + str(warning))
    for error in report.get("errors", []):
        _say("Error: " + str(error))
    return not report.get("errors")


class _Wizard:
    def __init__(self, config):
        self.config = config
        self.project = None
        self.document = None
        self.pages = []
        self.duration = 60.0
        self.strategy = "balanced"
        self.selection_changed = False

    def _code(self):
        if self.project is None:
            return 0
        analysis = self.project.state.get("analysis", {})
        summary = analysis.get("summary") or {}
        return 2 if analysis.get("status") in ("partial", "failed", "interrupted", "running") or summary.get("failed", 0) else 0

    def _setup(self, directory, source):
        _say("1. Source and private project")
        _say("Keep the project/catalog on LOCAL storage, outside the source folder (including NAS sources).")
        while True:
            try:
                if directory is None:
                    name = Path(source).expanduser().name if source is not None else "project"
                    directory = Path(_ask("Project directory (new or existing)", Path.home() / "storylens-projects" / name))
                directory = Path(directory).expanduser()
                if directory.exists():
                    self.project = Project.open(directory)
                    _say(f"Resuming: {self.project.directory}")
                    _say(f"Source: {self.project.source}; mode: {self.project.mode}; depth: {self.project.depth}")
                    if source is not None and Path(source).expanduser().resolve() != self.project.source:
                        raise ValueError("Requested source differs from this project's saved source; choose another project.")
                    return False
                if source is None:
                    source = _ask("Source media directory")
                    if not source:
                        raise ValueError("A source directory is required; no current-directory fallback is used.")
                source = Path(source).expanduser()
                _say("2. Analysis options")
                _say("Technical mode uses metadata and local signal measurements only; no AI models, even if configured.")
                mode = _choice("Mode (technical/local-ai)", ("technical", "local-ai"), "technical")
                depth = "fast"
                config = self.config
                if mode == "technical":
                    config = replace(config, embedding_model="", vision_model="", audio_transcription_model="")
                else:
                    _say("Local AI uses only installed models. Nothing will be downloaded; vision uses configured loopback Ollama.")
                    depth = _choice("AI depth (fast embeddings; standard/deep also vision)", ("fast", "standard", "deep"), "standard")
                    if not config.embedding_model:
                        config = replace(config, embedding_model=_ask("Installed embedding model directory"))
                    if depth != "fast" and not config.vision_model:
                        config = replace(config, vision_model=_ask("Installed Ollama vision model name"))
                    _say(f"Embedding: {config.embedding_model}; vision: {config.vision_model or 'disabled'}")
                    _say(f"Audio transcription: {config.audio_transcription_model or 'disabled'}; device: {config.audio_transcription_device}")
                if not _preflight(source, config, mode, depth):
                    raise ValueError("Preflight failed. Correct source/options before creating a project.")
                self.project = Project.create(directory, source, config, mode=mode, depth=depth)
                _say(f"Created private project: {self.project.directory}")
                return True
            except (OSError, ValueError, RuntimeError) as error:
                _say(f"Cannot use this project/source: {error}")
                if not _confirm("Choose project/source again?"):
                    return None
                self.project = None
                directory = source = None

    def _analyze(self, *, prechecked=False):
        while True:
            result = self._analysis_attempt(prechecked=prechecked)
            if result is not None:
                return result
            prechecked = False

    def _analysis_attempt(self, *, prechecked=False):
        project = self.project
        _say(f"Source: {project.source}")
        if not prechecked and not _preflight(project.source, project.config, project.mode, project.depth):
            return False
        _say("4. Analyze source (read-only); checkpoints and derived files stay inside your project.")
        _say(f"Explicit mode: {project.mode}; depth: {project.depth}.")
        if project.mode == "local-ai":
            _say(f"Embedding model: {project.config.embedding_model}; vision model: {project.config.vision_model or 'disabled'}")
            _say(f"Ollama endpoint: {project.config.ollama_endpoint}")
            _say(f"Audio transcription model: {project.config.audio_transcription_model or 'disabled'}; device: {project.config.audio_transcription_device}")
            _say("Configured transcription applies to standalone audio only, NOT audio inside videos.")
        if not _confirm("Start analysis using these options?"):
            return False
        last = -math.inf

        def progress(summary):
            nonlocal last
            now = time.monotonic()
            if now - last >= 1:
                _summary(summary)
                last = now

        summary = project.analyze(progress=progress)
        self.document = None
        self.pages = []
        _summary(summary)
        for error in summary.get("errors", []):
            _say(f"Failure: {error.get('path', '')} [{error.get('stage', '')}]: {error.get('error', '')}")
        if summary.get("errors_truncated"):
            _say("Further errors were truncated; inspect the saved analysis summary.")
        if summary.get("failed", 0):
            _say("Partial analysis is NOT success. Continuing uses only available evidence; exit status remains 2 until resolved.")
            action = _choice("Partial analysis: continue/reanalyze/quit", ("continue", "reanalyze", "quit"), "quit")
            if action == "reanalyze":
                return None
            return action == "continue"
        return True

    def _load(self):
        document = self.project.load_storyboard()
        self.document = document
        self.duration = document["duration_seconds"]
        self.strategy = document["strategy"]
        self.selection_changed = False
        self.pages = []
        _say(f"Loaded storyboard: {self.project.document_path}")

    def _recommend(self):
        _say("5. Recommendation (a hard duration budget; required media are never silently relaxed)")
        self.duration = _number("Target duration in seconds", self.duration)
        self.strategy = _choice("Strategy", ("balanced", "technical", "story"), self.strategy)
        if self.document is not None and not _confirm("Generate a new version, replacing the current review (older JSON is preserved)?"):
            return
        document = self.project.recommend(duration_seconds=self.duration, strategy=self.strategy)
        self.document = document
        self.selection_changed = False
        self.pages = []
        _say(f"Saved storyboard: {self.project.document_path}")
        self._selected()

    def _selected(self):
        document = self.document
        if document is None:
            _say("No current storyboard. Browse sources to correct constraints, then regenerate.")
            return
        _say(f"Title: {document['title']}; selected {document['total_seconds']:g}s / {document['duration_seconds']:g}s")
        for warning in document["warnings"]:
            _say("Warning: " + warning)
        for caveat in document["provenance"]["caveats"]:
            _say("Caveat: " + caveat)
        items = document["items"]
        page = 0
        while True:
            _say(f"Selected entries — page {page + 1}/{max(1, math.ceil(len(items) / 12))}")
            for index in range(page * 12, min(len(items), (page + 1) * 12)):
                entry = items[index]
                media = self.project.media(entry["media_id"])
                interval = "image hold" if entry["kind"] == "image" else f"source {entry['start_seconds']:g}–{entry['end_seconds']:g}s"
                scores = ", ".join(f"{key}={'n/a' if value is None else format(value, '.3f')}" for key, value in entry["scores"].items())
                _say(f"{index + 1}. {_filename(media)} | {entry['kind']} | {interval} | {entry['duration_seconds']:g}s | required={entry['required']} | {scores}")
                _say("   " + entry["reason"])
            if len(items) <= 12:
                return
            action = _choice("Selected page: n next / p previous / q review menu", ("n", "p", "q"), "q")
            if action == "q":
                return
            page = min(max(0, page + (1 if action == "n" else -1)), (len(items) - 1) // 12)

    def _browse(self):
        media = self.project.list_media()
        if not media:
            _say("No indexed media. Analyze the source first.")
            return
        page = 0
        selected = {entry["media_id"] for entry in (self.document or {}).get("items", [])}
        while True:
            _say(f"All source media — page {page + 1}/{math.ceil(len(media) / 12)}")
            for index in range(page * 12, min(len(media), (page + 1) * 12)):
                item = media[index]
                _say(f"{index + 1}. {_filename(item)} | {item['kind']} | {item.get('feedback', {}).get('selection', 'neutral')} | selected={item['id'] in selected} | failures={len(item.get('failures', []))}")
            _say("Whole-media feedback: i NUMBER require/include; x NUMBER exclude; u NUMBER neutral. Regenerate explicitly afterward.")
            command = _ask("Sources: n/p page, i/x/u NUMBER, q back", "q").lower().split()
            if command == ["q"]:
                return
            if command in (["n"], ["p"]):
                page = min(max(0, page + (1 if command[0] == "n" else -1)), (len(media) - 1) // 12)
                continue
            if len(command) != 2 or command[0] not in ("i", "x", "u"):
                _say("Use i/x/u followed by a displayed source number, or n/p/q.")
                continue
            try:
                index = int(command[1]) - 1
                if not page * 12 <= index < min(len(media), (page + 1) * 12):
                    raise ValueError("Choose a number on the current page.")
                selection = {"i": "include", "x": "exclude", "u": "neutral"}[command[0]]
                self.project.set_selection(media[index]["id"], selection)
                self.selection_changed = True
                self.pages = []
                media[index] = self.project.media(media[index]["id"])
                _say("Feedback saved; approval invalidated. Regenerate to apply it to the storyboard.")
            except (ValueError, OSError, RuntimeError) as error:
                _say(f"Feedback not applied: {error}")

    def _save_edit(self, edited):
        edited["total_seconds"] = math.fsum(entry["duration_seconds"] for entry in edited["items"])
        path = self.project.save_storyboard(edited)
        self.document = edited
        self.pages = []
        _say(f"Saved edited version (approval invalidated): {path}")

    def _edit(self, action):
        if self.document is None:
            raise ValueError("Generate or reload a storyboard first.")
        if self.selection_changed:
            raise ValueError("Regenerate after whole-media feedback before editing or approving.")
        edited = deepcopy(self.document)
        if action == "t":
            edited["title"] = _ask("New title", edited["title"])
        else:
            if not edited["items"]:
                raise ValueError("The storyboard has no selected entries.")
            index = _index("Entry number", len(edited["items"]))
            if action == "m":
                target = _index("New position", len(edited["items"]))
                edited["items"].insert(target, edited["items"].pop(index))
            else:
                entry = edited["items"][index]
                if entry["kind"] == "image":
                    entry["duration_seconds"] = _number("Image hold seconds", entry["duration_seconds"])
                else:
                    _say("Source-time edits must preserve source coverage, exclusions and whole conversation blocks.")
                    entry["start_seconds"] = _number("Source start seconds", entry["start_seconds"], signed=True)
                    entry["end_seconds"] = _number("Source end seconds", entry["end_seconds"], signed=True)
                    entry["duration_seconds"] = entry["end_seconds"] - entry["start_seconds"]
        self._save_edit(edited)

    def _contacts(self):
        if self.document is None:
            raise ValueError("Generate or reload a storyboard first.")
        self.pages = contact_sheets(self.project, self.document)
        _say("Contact sheets: still images and labeled video scene samples, NOT frame-accurate cut previews. Audio has no visual playback; missing previews are labeled.")
        for index, path in enumerate(self.pages, 1):
            _say(f"{index}. {path}")
        _say("Nothing was opened. Use o to request opening a page.")

    def _open_contact(self):
        if not self.pages:
            raise ValueError("Generate contact sheets with c first.")
        path = self.pages[_index("Contact page to open", len(self.pages))]
        if not webbrowser.open(path.resolve().as_uri()):
            _say("No viewer accepted the request. Open the printed file path manually.")

    def _editor(self):
        _say("7. Optional FilmCraft: native project, PNG preview, then separately approved MP4 export.")
        if not _confirm("Use an installed trusted FilmCraft 0.9.0 executable?"):
            return
        executable = Path(_ask("Absolute trusted FilmCraft executable path")).expanduser()
        if not executable.is_absolute() or not executable.is_file():
            raise ValueError("FilmCraft executable must be an existing absolute file path.")
        _say(f"Executable: {executable}. It will run as your user; source checks are not an OS sandbox.")
        if not _confirm("Trust and allow running this executable?"):
            return
        preview = None
        saved_preview = self.project.state.get("preview")
        if saved_preview:
            preview = self.project.directory / saved_preview["path"]
        while True:
            _say("FilmCraft: d discover (read-only), c create native project, p render PNG preview, x export MP4, o open PNG, q finish")
            action = _choice("FilmCraft action", ("d", "c", "p", "x", "o", "q"), "q")
            if action == "q":
                return
            try:
                if action == "d":
                    async def discover():
                        from .filmcraft_transport import FilmCraft
                        async with FilmCraft(executable) as client:
                            return await client.discover()
                    discovery = asyncio.run(discover())
                    _say(f"Server: {discovery['server']}")
                    _say(f"Advertised tools: {len(discovery['tools'])}; commands are checked again before writes.")
                    continue
                if action == "o":
                    if preview is None:
                        raise ValueError("Render a PNG preview first.")
                    if not webbrowser.open(preview.resolve().as_uri()):
                        _say("No viewer accepted the PNG; use its printed path.")
                    continue
                _say("Editor operations are not transactional. Failures are recorded; no automatic mutation retry.")
                if action == "c":
                    fps = _ask("Exact frame rate (24,25,30,50,60,24000/1001,30000/1001,60000/1001)", "24")
                    if not _confirm("Create a NEW native FilmCraft project from the approved storyboard (1920x1080)?"):
                        continue
                    report = asyncio.run(self.project.create_editor(executable, approved=True, fps=fps))
                    preview = None
                elif action == "p":
                    seconds = _number("PNG timeline time in seconds", 0, zero=True)
                    if not _confirm("Render a NEW PNG preview using FilmCraft?"):
                        continue
                    report = asyncio.run(self.project.preview_editor(executable, approved=True, seconds=seconds))
                else:
                    _say("Export requires an unchanged native project with a successfully rendered PNG preview.")
                    if not _confirm("Export a NEW MP4 now?"):
                        continue
                    report = asyncio.run(self.project.export_editor(executable, approved=True))
                if report.get("report_path"):
                    _say(f"Operation report: {report['report_path']}")
                for warning in report.get("warnings", []):
                    _say("Warning: " + warning)
                if report.get("status") != "succeeded":
                    _say(f"Operation did not succeed: {report.get('error', report.get('status', 'unknown status'))}")
                    _say("Inspect the report and existing outputs before explicitly choosing any further mutation.")
                    continue
                _say(f"Saved: {report.get('output', report.get('project', 'see operation report'))}")
                if action == "p":
                    saved = self.project.state.get("preview")
                    preview = self.project.directory / saved["path"] if saved else None
                    _say("PNG is not opened automatically; use o to inspect it before export.")
            except (OSError, ValueError, RuntimeError, ImportError) as error:
                _say(f"FilmCraft operation stopped: {error}")
                _say("No automatic retry. Inspect saved state/reports before another mutation.")

    def _review(self):
        _say("6. Review — edits save new versions; sources and previous outputs are unchanged.")
        _say("Audio cannot be auditioned here. Contact sheets are not full video playback or an audio review.")
        while True:
            _say("s selected | b all sources/feedback | r regenerate | l reload JSON | t title | m reorder | e hold/source interval | c contact sheets | o open page | a approve | q save/quit")
            action = _choice("Review action", ("s", "b", "r", "l", "t", "m", "e", "c", "o", "a", "q"), "s")
            try:
                if action == "q":
                    _say("Saved state retained; storyboard is not newly approved.")
                    return self._code()
                if action == "s":
                    self._selected()
                elif action == "b":
                    self._browse()
                elif action == "r":
                    self._recommend()
                elif action == "l":
                    _say("Reload validates the current JSON against sources and feedback; unsaved external edits are not imported elsewhere.")
                    self._load()
                    self._selected()
                elif action in ("t", "m", "e"):
                    self._edit(action)
                elif action == "c":
                    self._contacts()
                elif action == "o":
                    self._open_contact()
                else:
                    if self.document is None:
                        raise ValueError("No storyboard to approve.")
                    if self.selection_changed:
                        raise ValueError("Regenerate after feedback before approving.")
                    _say("Approval records this exact storyboard. FilmCraft creation/export still need separate consent.")
                    if _confirm("Approve this reviewed storyboard?"):
                        self.project.approve(self.document)
                        _say("Storyboard approved.")
                        self._editor()
                        return self._code()
            except (OSError, ValueError, RuntimeError, ImportError) as error:
                _say(f"Action stopped: {error}")
                _say("Saved project state is retained. Correct constraints/budget, reload or choose another action; no operation was automatically retried.")

    def run(self, directory, source):
        created = self._setup(directory, source)
        if created is None:
            return 2
        if created:
            if not self._analyze(prechecked=True):
                return self._code()
        else:
            analysis = self.project.state.get("analysis", {})
            _say(f"Saved analysis status: {analysis.get('status', 'unknown')}")
            if analysis.get("summary"):
                _summary(analysis["summary"])
            if analysis.get("status") in ("partial", "failed", "interrupted", "running"):
                _say("Analysis is incomplete or has failures. Existing review is not proof that all sources succeeded.")
            default = "r" if self.project.document_path is not None else "a"
            action = _choice("Resume: r review existing / a cached reanalysis / g recommend cached media / q quit", ("r", "a", "g", "q"), default)
            if action == "q":
                return self._code()
            if action == "r":
                try:
                    self._load()
                    self._selected()
                except (ValueError, OSError, RuntimeError) as error:
                    _say(f"Cannot review current storyboard: {error}")
            elif action == "a" and not self._analyze():
                return self._code()
            if action in ("r", "g") and analysis.get("status") in ("partial", "failed", "interrupted", "running"):
                if not _confirm("Continue review/recommendation despite incomplete analysis?"):
                    return self._code()
        if self.document is None:
            try:
                self._recommend()
            except (OSError, ValueError, RuntimeError) as error:
                _say(f"Recommendation stopped: {error}")
                _say("Use source feedback and regenerate to correct impossible constraints or budgets.")
        return self._review()


def run(config, *, project=None, source=None):
    """Run interactively; all work remains resumable on EOF or Ctrl-C."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("The terminal wizard requires interactive stdin and stdout (a TTY).")
    wizard = _Wizard(config)
    try:
        return wizard.run(project, source)
    except (EOFError, KeyboardInterrupt):
        _say("Stopped safely; completed checkpoints and earlier outputs are retained.")
        return 130
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        _say(f"Wizard stopped: {error}")
        return 2
    finally:
        if wizard.project is not None:
            _say(f"Resume project: {wizard.project.directory}")
            _say(f"Run storylens wizard --project {_safe(wizard.project.directory)} (quote paths containing spaces).")
