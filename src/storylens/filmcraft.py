"""Optional, explicit FilmCraft 0.9 exchange. Never imported by core analysis."""

from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

from .access import PathPolicy
from .feedback import _coverage
from .ingest import fingerprint, snapshot
from .transcripts import MAX_BYTES, MAX_WORDS, normalize

TICKS = 254_016_000_000
VERSION = "0.9.0"


def _read_json(path, limit=MAX_BYTES):
    with Path(path).open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Editor input exceeds size limit")
    document = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(document, dict):
        raise ValueError("Editor input must be a JSON object")
    return raw, document


def _source(media, policy):
    for reference in media["paths"]:
        path = Path(reference["path"])
        try:
            if path.is_symlink():
                continue
            path = policy.authorize(path)
            if fingerprint(path, snapshot(path)) == media["id"]:
                return path
        except (OSError, ValueError):
            continue
    raise ValueError("No approved, unchanged source for " + media["id"] + "; rescan changed media")


def _destination(path, policy):
    path = Path(path).expanduser().absolute()
    parent = path.parent.resolve(strict=True)
    target = parent / path.name
    if target.exists() or target.is_symlink():
        raise ValueError("Editor output already exists; choose a new path")
    if any(target.is_relative_to(root) for root in policy.roots):
        raise ValueError("Editor outputs must be outside approved source roots")
    return target


def _approve(approved):
    if approved is not True:
        raise ValueError("Editor writes require explicit --approve after storyboard/project review")


def _ticks(seconds):
    return round(Fraction(str(seconds)) * TICKS)


def _rate(value):
    try:
        rate = Fraction(value)
    except (ValueError, TypeError, ZeroDivisionError) as error:
        raise ValueError("fps must be a positive rational, e.g. 24 or 24000/1001") from error
    if rate not in {Fraction(24), Fraction(25), Fraction(30), Fraction(50), Fraction(60),
                    Fraction(24000, 1001), Fraction(30000, 1001), Fraction(60000, 1001)}:
        raise ValueError("Unsupported FilmCraft frame rate")
    return rate


def _plan(document, rate):
    if not document["items"]:
        raise ValueError("Cannot create an empty editor sequence")
    result, cursor = [], Fraction(0)
    for entry in document["items"]:
        duration = Fraction(str(entry["duration_seconds"]))
        # Decimal JSON cannot exactly encode a repeating rational. Allow only
        # numerical representation error, never an actual frame snap.
        frames = round(duration * rate)
        exact = Fraction(frames, 1) / rate
        if frames < 1 or abs(duration - exact) > Fraction(1, 10**9):
            raise ValueError("Entry duration is not on the requested frame grid; edit the storyboard or fps")
        actual_start = None if entry["kind"] == "image" else _ticks(entry["start_seconds"])
        result.append({"entry": entry, "time": round(cursor * TICKS),
                       "duration": round(exact * TICKS), "sourceIn": actual_start or 0})
        cursor += exact
    if cursor > Fraction(str(document["duration_seconds"])) + Fraction(1, 10**9):
        raise ValueError("Frame timing would exceed the storyboard budget")
    return result


def _project_items(tree):
    if isinstance(tree, dict):
        if "item" in tree and "duration" in tree:
            yield tree
        for child in tree.get("children", []):
            yield from _project_items(child)


def _verify_origin(media, path, editor_duration):
    if media["kind"] == "image":
        return
    origin, end = _coverage(media)
    if origin == 0:
        if editor_duration < _ticks(end):
            raise ValueError("Editor source duration is shorter than catalog coverage")
        return
    # MP4 edit-list presentation timing is verified against a nonzero-origin
    # released-binary fixture. Do not generalize to demuxers that rebase PTS.
    if (origin < 0 or path.suffix.lower() not in {".mp4", ".mov", ".m4v"}
            or abs(editor_duration - _ticks(end)) > 1):
        raise ValueError("Unsupported nonzero source origin mapping; no automatic rebasing")


async def _compatible(client, required):
    discovery = await client.discover()
    server = discovery["server"]
    if server.get("name") != "filmcraft" or server.get("version") != VERSION:
        raise ValueError("Editor writes support verified FilmCraft " + VERSION + " only")
    tools = {tool["name"]: tool for tool in discovery["tools"]}
    if not {"command_run", "media_import", "project_inspect", "sequence_inspect"} <= tools.keys():
        raise ValueError("Installed FilmCraft is missing required tools")
    commands = discovery["command_list"]
    if not isinstance(commands, list) or not required <= {c.get("id") for c in commands if isinstance(c, dict)}:
        raise ValueError("Installed FilmCraft is missing required commands")
    return discovery


async def create(app, document, *, executable, output, roots, approved=False,
                 fps="24", width=1920, height=1080, timeout=30):
    """Create a new saved project only; never open or modify an existing project."""
    from .filmcraft_transport import FilmCraft
    _approve(approved)
    policy = PathPolicy(roots)
    target = _destination(output, policy)
    if target.suffix.lower() != ".fcproj":
        raise ValueError("Project output must end in .fcproj")
    if any(type(v) is not int or not 16 <= v <= 7680 for v in (width, height)):
        raise ValueError("Sequence width/height must be integers in 16..7680")
    app.validate_storyboard(document)
    rate = _rate(fps)
    plan = _plan(document, rate)
    media = {entry["media_id"]: app.catalog.get(entry["media_id"]) for entry in document["items"]}
    paths = {key: _source(item, policy) for key, item in media.items()}
    report = {"schema_version": "1.0", "status": "failed", "project": str(target), "operations": [],
              "warnings": ["No automatic retry; editor batches are not transactional.",
                           "Source references are checked, not OS-sandboxed; use a trusted editor binary."]}
    async with FilmCraft(executable, timeout=timeout) as client:
        discovery = await _compatible(client, {"file.newSequence", "timeline.place", "file.saveAs"})
        report["server"] = discovery["server"]

        async def call(tool, arguments):
            try:
                result = await client.call(tool, arguments)
            except RuntimeError as error:
                report["operations"].append({"tool": tool, "arguments": arguments, "error": str(error)})
                raise
            report["operations"].append({"tool": tool, "arguments": arguments, "result": result})
            return result

        async def command(name, params):
            return await call("command_run", {"id": name, "params": params})

        try:
            sequence = await command("file.newSequence", {"name": document["title"] or "StoryLens",
                "width": width, "height": height, "fps": float(rate), "sampleRate": 48000,
                "video": 1, "audio": 1})
            imported = {}
            for media_id, path in paths.items():
                result = await call("media_import", {"paths": [str(path)]})
                if result.get("errors") or len(result.get("items", [])) != 1:
                    raise ValueError("Editor did not import exactly one source without errors")
                imported[media_id] = result["items"][0]
            project = await call("project_inspect", {})
            durations = {item["item"]: item["duration"] for item in _project_items(project["root"])}
            for key, item in media.items():
                _verify_origin(item, paths[key], durations[imported[key]])
            expected = []
            for row in plan:
                entry = row["entry"]
                item_id = imported[entry["media_id"]]
                params = {"item": item_id, "track": "A1" if entry["kind"] == "audio" else "V1",
                          "video": entry["kind"] != "audio",
                          "audio": entry["kind"] != "image", "insert": False,
                          "time": row["time"], "sourceIn": row["sourceIn"], "duration": row["duration"]}
                if entry["kind"] == "video":
                    params["audioTrack"] = "A1"
                placed = await command("timeline.place", params)
                if not placed.get("clips"):
                    raise ValueError("Editor placed no clips")
                expected.append((row, item_id, placed["clips"]))
            state = await call("sequence_inspect", {})
            _verify_sequence(state, rate, sequence["sequence"], expected)
            # Check sources again before saving the only persistent artifact.
            for key, item in media.items():
                if _source(item, policy) != paths[key]:
                    raise ValueError("Source reference changed during editor assembly")
            with tempfile.TemporaryDirectory(prefix=".storylens-filmcraft-", dir=target.parent) as temporary:
                saved = Path(temporary) / target.name
                await command("file.saveAs", {"path": str(saved)})
                async with FilmCraft(executable, project=saved, timeout=timeout) as reopened:
                    checked = await reopened.call("sequence_inspect", {})
                    _verify_sequence(checked, rate, sequence["sequence"], expected)
                os.link(saved, target)  # Atomic publication without overwriting another file.
            report.update(status="succeeded", sequence=state, media=imported)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
            report["error"] = str(error)
    return report


def _verify_sequence(state, rate, sequence_id, expected):
    settings = state["settings"]["frame_rate"]
    if Fraction(settings["num"], settings["den"]) != rate or state["id"] != sequence_id:
        raise ValueError("Editor sequence/rate differs from requested target")
    clips = {clip["clip"]: clip for track in state["video"] + state["audio"] for clip in track["items"]
             if "clip" in clip}
    if set(clips) != {clip for _, _, ids in expected for clip in ids}:
        raise ValueError("Editor clip inventory differs from approved storyboard")
    for row, item_id, ids in expected:
        for clip_id in ids:
            clip = clips[clip_id]
            if (clip["item"] != item_id or clip["start"] != row["time"]
                    or clip["end"] != row["time"] + row["duration"]
                    or clip["sourceIn"] != row["sourceIn"]
                    or clip["sourceOut"] != row["sourceIn"] + row["duration"]
                    or clip["speed"] != 1 or clip["reverse"] or not clip["enabled"]):
                raise ValueError("Editor changed approved clip timing, identity or playback")
    end = max(row["time"] + row["duration"] for row, _, _ in expected)
    if state["duration"] != end:
        raise ValueError("Editor sequence duration differs from approved storyboard")


def _saved_sources(project, policy):
    if project.get("format") != "filmcraft.project" or project.get("schema_version") != 15:
        raise ValueError("Only verified FilmCraft project schema 15 is supported")
    paths = {}
    for item_id, item in project["project"]["items"].items():
        media = item.get("kind", {}).get("Media")
        if media is not None:
            if media.get("proxy") is not None or media.get("offline"):
                raise ValueError("Offline/proxy editor sources are not supported")
            reference = media.get("media", {}).get("File")
            if not isinstance(reference, dict) or not isinstance(reference.get("path"), str):
                raise ValueError("Only local file-backed editor media is supported")
            path = Path(reference["path"])
            if not path.is_absolute() or path.is_symlink():
                raise ValueError("Editor sources must be absolute nonsymlink local paths")
            paths[item_id] = policy.authorize(path)
    return paths


async def render(*, executable, project, output, roots, approved=False,
                 seconds=None, timeout=300):
    """Render a PNG preview or full H.264 export to a new path, never overwrite."""
    from .filmcraft_transport import FilmCraft
    _approve(approved)
    policy = PathPolicy(roots)
    project_path = Path(project).resolve(strict=True)
    raw, document = _read_json(project_path)
    sources = _saved_sources(document, policy)
    target = _destination(output, policy)
    preview = seconds is not None
    if target.suffix.lower() != (".png" if preview else ".mp4"):
        raise ValueError("Preview output must be .png; movie output must be .mp4")
    if preview and (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                    or not math.isfinite(seconds) or seconds < 0):
        raise ValueError("Preview seconds must be finite and nonnegative")
    report = {"schema_version": "1.0", "status": "failed", "output": str(target), "operations": []}
    # A private snapshot prevents a concurrent project edit from introducing an
    # unapproved media path between validation and the external process opening it.
    with tempfile.TemporaryDirectory(prefix=".storylens-filmcraft-", dir=target.parent) as temporary:
        copy = Path(temporary) / "review.fcproj"
        copy.write_bytes(raw)
        saved = Path(temporary) / target.name
        async with FilmCraft(executable, project=copy, timeout=timeout) as client:
            await _compatible(client, {"file.exportFrame" if preview else "file.exportMedia", "media.status"})
            try:
                state = await client.call("sequence_inspect", {})
                if preview and _ticks(seconds) >= state["duration"]:
                    raise ValueError("Preview time lies outside the sequence")
                status = await client.call("command_run", {"id": "media.status", "params": {}})
                report["operations"].append({"id": "media.status", "result": status})
                if (not isinstance(status, list) or any(item.get("status") != "online" for item in status)
                        or {str(item["item"]) for item in status} != sources.keys()):
                    raise ValueError("Editor media is offline, unreadable or differs from approved sources")
                # Inspect all serialized sources before rendering; export also
                # reports offlineMedia and is rejected if placeholders were used.
                if any(not path.is_file() for path in sources.values()):
                    raise ValueError("Editor source is missing")
                params = {"path": str(saved)}
                if preview:
                    params.update(format="png", target="program", time=_ticks(seconds))
                else:
                    params.update(format="h264", range="entire", audio=True,
                                  hardwareEncoding="off", wait=True)
                name = "file.exportFrame" if preview else "file.exportMedia"
                result = await client.call("command_run", {"id": name, "params": params})
                report["operations"].append({"id": name, "result": result})
                if result.get("offlineMedia"):
                    raise ValueError("Export used offline-media placeholders")
                if not saved.is_file() or saved.stat().st_size == 0:
                    raise ValueError("Editor produced no output artifact")
                os.link(saved, target)
                report.update(status="succeeded", sequence=state)
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
                report["error"] = str(error)
    return report


def import_transcript(app, *, project, item_id, media_id, roots):
    """Import saved source words, never edited-sequence words or inferred alignment."""
    policy = PathPolicy(roots)
    raw, document = _read_json(project)
    sources = _saved_sources(document, policy)
    media = app.catalog.get(media_id)
    path = _source(media, policy)
    if sources.get(str(item_id)) != path:
        raise ValueError("Explicit editor item does not map to the selected catalog source")
    try:
        transcript = document["project"]["transcripts"][str(item_id)]
    except KeyError as error:
        raise ValueError("No saved source transcript for editor item") from error
    words, speakers = transcript["words"], transcript.get("speakers", [])
    if not isinstance(words, list) or len(words) > MAX_WORDS:
        raise ValueError("Invalid or oversized editor words array")
    origin, end = _coverage(media)
    info = document["project"]["items"][str(item_id)]["kind"]["Media"]["info"]
    _verify_origin(media, path, info["duration"])
    segments = []
    for word in words:
        start, stop = word["start"], word["end"]
        if type(start) is not int or type(stop) is not int or not _ticks(origin) <= start < stop <= _ticks(end):
            raise ValueError("Editor transcript word lies outside source coverage")
        speaker = word.get("speaker")
        if speaker is not None:
            if type(speaker) is not int or not 0 <= speaker < len(speakers):
                raise ValueError("Invalid editor speaker index")
            speaker = speakers[speaker]["name"]
        normalized_word = {"start": start / TICKS, "end": stop / TICKS, "text": word["text"],
                           "probability": word.get("confidence")}
        if (segments and segments[-1]["speaker"] == speaker
                and normalized_word["start"] >= segments[-1]["end"]
                and normalized_word["start"] - segments[-1]["end"] <= 2):
            segment = segments[-1]
            segment["end"] = normalized_word["end"]
            segment["text"] += " " + word["text"]
            segment["words"].append(normalized_word)
        else:
            segments.append({"start": normalized_word["start"], "end": normalized_word["end"],
                             "text": word["text"], "speaker": speaker, "words": [normalized_word]})
    normalized = normalize({"language": transcript.get("language"), "segments": segments})
    result = {"schema_version": "1.0", "provenance": {
        "algorithm": "filmcraft-transcript-import-1", "source": "imported", "format": "filmcraft-project",
        "source_sha256": hashlib.sha256(raw).hexdigest(), "source_name": Path(project).name,
        "editor_item": str(item_id), "offset_seconds": 0,
        "confidence_methodology": "Supplied FilmCraft source words; not independently verified"},
        "source_timing": {"semantics": "absolute source presentation seconds from FilmCraft integer ticks",
                          "alignment": "explicit source identity mapping; no edited-sequence remapping"}, **normalized}
    app.catalog.save(media_id, "transcript", app._transcript_cache_key(result), result)
    app.catalog.save_transcript(media_id, result)
    return result
