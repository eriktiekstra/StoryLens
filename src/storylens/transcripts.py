"""Bounded transcript interchange. Imported words are evidence, not verified speech."""

import hashlib
import json
import math
from pathlib import Path
import re

ALGORITHM_VERSION = "transcript-import-1"
MAX_BYTES = 2 * 1024 * 1024
MAX_SEGMENTS = 10000
MAX_WORDS = 50000
_TIMESTAMP = re.compile(r"(?:(\d+):)?([0-5]\d):([0-5]\d)[,.](\d{3})\Z")


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(name + " must be a finite number")
    return float(value)


def _time(item, name):
    keys = [key for key in (name, name + "_seconds") if key in item]
    if len(keys) != 1:
        raise ValueError("Each segment/word needs one " + name + " timestamp")
    return _number(item[keys[0]], name)


def _text(value, name, *, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(name + " must be nonempty text without NUL")
    return value


def _timestamp(text):
    match = _TIMESTAMP.fullmatch(text)
    if match is None:
        raise ValueError("Invalid subtitle timestamp: " + text)
    hours, minutes, seconds, millis = match.groups()
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def _subtitles(text, format):
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if format == "vtt":
        if not lines or not re.fullmatch(r"WEBVTT(?:[ \t].*)?", lines[0]):
            raise ValueError("WebVTT input must begin with WEBVTT")
        # Header metadata ends at the first blank line.
        separator = next((i for i, line in enumerate(lines) if not line.strip()), None)
        if separator is None:
            raise ValueError("WebVTT header must end with a blank line")
        lines = lines[separator + 1:]
    segments = []
    for block in re.split(r"\n[ \t]*\n", "\n".join(lines).strip()):
        if not block.strip():
            continue
        cue = block.split("\n")
        if format == "vtt" and (cue[0] == "NOTE" or cue[0].startswith(("NOTE ", "NOTE\t"))
                                or cue[0] in {"STYLE", "REGION"}):
            continue
        if "-->" not in cue[0]:
            if format == "srt" and not cue[0].strip().isdigit():
                raise ValueError("SRT cue identifiers must be integers")
            cue = cue[1:]
        if len(cue) < 2:
            raise ValueError("Subtitle cue needs a timing line and text")
        timing = cue[0].split("-->")
        if len(timing) != 2:
            raise ValueError("Invalid subtitle timing line")
        end_fields = timing[1].strip().split()
        if not end_fields or (format == "srt" and len(end_fields) != 1):
            raise ValueError("Invalid subtitle end timestamp")
        segments.append({"start": _timestamp(timing[0].strip()), "end": _timestamp(end_fields[0]),
                         "text": "\n".join(cue[1:])})
        if len(segments) > MAX_SEGMENTS:
            raise ValueError("Transcript exceeds segment limit")
    return {"segments": segments}


def normalize(document, *, offset=0, language=None):
    """Normalize Whisper-like or StoryLens JSON; preserve overlap, text and speaker labels."""
    offset = _number(offset, "offset")
    if not isinstance(document, dict) or not isinstance(document.get("segments"), list):
        raise ValueError("Transcript JSON must contain a segments array")
    if len(document["segments"]) > MAX_SEGMENTS:
        raise ValueError("Transcript exceeds segment limit")
    language = language if language is not None else document.get("language")
    if language is not None:
        language = _text(language, "language")
    segments, word_count, previous_start = [], 0, -math.inf
    for index, item in enumerate(document["segments"]):
        if not isinstance(item, dict):
            raise ValueError("Transcript segments must be objects")
        start, end = _time(item, "start"), _time(item, "end")
        if end <= start or start < previous_start:
            raise ValueError("Transcript intervals must have positive duration and ordered starts")
        previous_start = start
        words = item.get("words", [])
        if not isinstance(words, list):
            raise ValueError("words must be an array")
        word_count += len(words)
        if word_count > MAX_WORDS:
            raise ValueError("Transcript exceeds word limit")
        normalized_words, previous_word = [], start
        for word in words:
            if not isinstance(word, dict):
                raise ValueError("Transcript words must be objects")
            ws, we = _time(word, "start"), _time(word, "end")
            if not start <= ws <= we <= end or ws < previous_word:
                raise ValueError("Word times must be ordered and contained in their segment")
            previous_word = ws
            probability = word.get("probability")
            if probability is not None:
                probability = _number(probability, "word probability")
                if not 0 <= probability <= 1:
                    raise ValueError("Word probability must be in [0,1]")
            text = word.get("text", word.get("word"))
            normalized_words.append({"start_seconds": ws + offset, "end_seconds": we + offset,
                                     "text": _text(text, "word text"), "probability": probability})
        segments.append({"index": index, "start_seconds": start + offset, "end_seconds": end + offset,
                         "text": _text(item.get("text"), "segment text"),
                         "speaker": _text(item.get("speaker"), "speaker", optional=True),
                         "words": normalized_words})
    # Validate additions too: individually finite floats can overflow when offset.
    for item in segments:
        for timed in (item, *item["words"]):
            _number(timed["start_seconds"], "shifted start")
            _number(timed["end_seconds"], "shifted end")
    return {"language": language, "segments": segments}


def load_transcript(path, *, format=None, offset=0, language=None):
    path = Path(path)
    format = format or path.suffix.lower().lstrip(".")
    if format not in {"json", "srt", "vtt"}:
        raise ValueError("Transcript format must be json, srt or vtt")
    with path.open("rb") as source:
        raw = source.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Transcript exceeds 2 MiB import limit")
    try:
        text = raw.decode("utf-8-sig")
        document = json.loads(text) if format == "json" else _subtitles(text, format)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Transcript must be valid UTF-8 " + format) from error
    result = normalize(document, offset=offset, language=language)
    return {"schema_version": "1.0", "provenance": {"algorithm": ALGORITHM_VERSION,
            "source": "imported", "format": format, "source_sha256": hashlib.sha256(raw).hexdigest(),
            "source_name": path.name, "offset_seconds": float(offset),
            "confidence_methodology": "Supplied text/speaker labels/timestamps; not independently verified"},
            "source_timing": {"semantics": "absolute source presentation seconds; supplied input times plus explicit offset",
                              "alignment": "user supplied; not automatically aligned"}, **result}


def _stamp(seconds, separator):
    if seconds < 0:
        raise ValueError("Subtitle formats cannot represent negative source times; export JSON instead")
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3600000)
    minutes, millis = divmod(millis, 60000)
    seconds, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


def serialize(result, format="json"):
    if format == "json":
        return json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    if format not in {"srt", "vtt"}:
        raise ValueError("Transcript format must be json, srt or vtt")
    normalized = normalize(result)
    separator = "," if format == "srt" else "."
    cues = []
    for item in normalized["segments"]:
        start, end = _stamp(item["start_seconds"], separator), _stamp(item["end_seconds"], separator)
        if start == end:
            raise ValueError("Subtitle millisecond precision collapses a segment; export JSON instead")
        if re.search(r"\n[ \t]*\n", item["text"]) or "-->" in item["text"]:
            raise ValueError("Subtitle text is ambiguous as cues; export JSON to preserve it")
        cues.append(f'{item["index"] + 1}\n{start} --> {end}\n{item["text"]}')
    return ("WEBVTT\n\n" if format == "vtt" else "") + "\n\n".join(cues) + ("\n" if cues else "")
