"""Strict, read-only validation of portable, editable storyboard documents."""

from collections.abc import Mapping
import math

from .feedback import _coverage, _number, _segments


MAX_ITEMS = 500
_COMPONENTS = {"technical", "relevance", "story"}
_DOCUMENT_KEYS = {"schema_version", "kind", "title", "duration_seconds", "total_seconds",
                  "strategy", "weights", "required", "excluded", "items", "warnings", "provenance"}
_ENTRY_KEYS = {"media_id", "kind", "start_seconds", "end_seconds", "duration_seconds",
               "required", "scores", "reason"}


def _object(value, keys, name):
    if not isinstance(value, dict) or value.keys() != keys:
        raise ValueError(name + " must contain exactly: " + ", ".join(sorted(keys)))


def _text(value, name, *, nonempty=False):
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise ValueError(name + " must be " + ("nonempty " if nonempty else "") + "text")


def _ids(value, name):
    if not isinstance(value, list):
        raise ValueError(name + " must be an array of media IDs")
    result = set()
    for media_id in value:
        _text(media_id, name + " media ID", nonempty=True)
        if media_id in result:
            raise ValueError(name + " must not contain duplicate media IDs")
        result.add(media_id)
    return result


def validate_constraints(required=None, excluded=None):
    """Return disjoint ID sets; reject malformed or contradictory requests."""
    required = _ids([] if required is None else required, "required")
    excluded = _ids([] if excluded is None else excluded, "excluded")
    conflict = required & excluded
    if conflict:
        raise ValueError("Conflicting required/excluded media IDs: " + ", ".join(sorted(conflict)))
    return required, excluded


def effective_constraints(media, required=None, excluded=None):
    """Merge explicit media constraints with current independent user feedback."""
    if not isinstance(media, Mapping):
        raise ValueError("media must map media IDs to catalog items")
    required, excluded = validate_constraints(required, excluded)
    unknown = (required | excluded) - media.keys()
    if unknown:
        raise ValueError("Unknown media IDs: " + ", ".join(sorted(unknown)))
    segments = {}
    for media_id, item in media.items():
        if not isinstance(item, dict):
            raise ValueError("Catalog media must be objects")
        feedback = item.get("feedback", {})
        if not isinstance(feedback, dict):
            raise ValueError("feedback must contain feedback values")
        selection = feedback.get("selection", "neutral")
        if selection not in ("neutral", "include", "exclude"):
            raise ValueError("feedback selection must be neutral, include or exclude")
        if selection == "include":
            required.add(media_id)
        elif selection == "exclude":
            excluded.add(media_id)
        segments[media_id] = _segments(feedback["segments"], item) if "segments" in feedback else []
    conflict = required & excluded
    if conflict:
        raise ValueError("Conflicting required/excluded media IDs: " + ", ".join(sorted(conflict)))
    for media_id in excluded:
        if any(segment["selection"] == "include" for segment in segments[media_id]):
            raise ValueError("Excluded media conflicts with required include segments: " + media_id)
    return required, excluded, segments


def merge_intervals(intervals):
    """Union half-open intervals, merging touching endpoints as well as overlaps."""
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def conversation_blocks(item):
    """Timing-only speech blocks, never a claim of semantic understanding."""
    if item.get("kind") not in ("audio", "video"):
        return []
    transcript = item.get("transcript")
    if transcript is None:
        analysis = item.get("analysis", {})
        transcript = analysis.get("transcript", {}).get("result")
        if transcript is None:
            transcript = analysis.get("audio_transcription", {}).get("result")
    if transcript is None:
        return []
    if not isinstance(transcript, dict) or not isinstance(transcript.get("segments"), list):
        raise ValueError("Transcript evidence must contain a segments array")
    if not transcript["segments"]:
        return []
    origin, source_end = _coverage(item)
    intervals = []
    for segment in transcript["segments"]:
        if not isinstance(segment, dict):
            raise ValueError("Transcript segments must be objects")
        start = _number(segment.get("start_seconds"), "transcript start")
        end = _number(segment.get("end_seconds"), "transcript end")
        if not origin <= start < end <= source_end:
            raise ValueError("Transcript interval must lie within source coverage")
        intervals.append((start, end))
    blocks = []
    for start, end in sorted(intervals):
        if blocks and start - blocks[-1][1] <= 2:
            blocks[-1] = (blocks[-1][0], max(blocks[-1][1], end))
        else:
            blocks.append((start, end))
    return blocks


def _overlaps(start, end, other_start, other_end):
    return max(start, other_start) < min(end, other_end)


def _strings(value, name):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(name + " must be an array of strings")


def _sum(values, name):
    try:
        return _number(math.fsum(values), name)
    except OverflowError as error:
        raise ValueError(name + " must be finite") from error


def validate_storyboard(document, media):
    """Validate shape, budget, source evidence and unerasable current feedback.

    Playback order, title, image holds and source intervals are editable. Validation
    never rewrites the document or current catalog/feedback and returns the input.
    """
    _object(document, _DOCUMENT_KEYS, "storyboard")
    if document["schema_version"] != "1.0" or document["kind"] != "storylens_storyboard":
        raise ValueError("Unsupported storyboard kind or schema_version")
    _text(document["title"], "title")
    budget = _number(document["duration_seconds"], "duration_seconds")
    total = _number(document["total_seconds"], "total_seconds")
    if budget <= 0 or total < 0:
        raise ValueError("duration_seconds must be positive and total_seconds nonnegative")
    if document["strategy"] not in ("balanced", "technical", "story"):
        raise ValueError("strategy must be balanced, technical or story")
    _object(document["weights"], _COMPONENTS, "weights")
    for weight in document["weights"].values():
        if not 0 <= _number(weight, "weight") <= 1:
            raise ValueError("weights must be in [0,1]")
    if not math.isclose(_sum(document["weights"].values(), "weights"), 1, rel_tol=0, abs_tol=1e-9):
        raise ValueError("weights must sum to 1")
    _strings(document["warnings"], "warnings")
    _object(document["provenance"], {"algorithm", "caveats"}, "provenance")
    if document["provenance"]["algorithm"] != "recommendations-1":
        raise ValueError("Unsupported storyboard provenance algorithm")
    _strings(document["provenance"]["caveats"], "provenance caveats")
    _ids(document["required"], "required")
    _ids(document["excluded"], "excluded")
    required, excluded, segments = effective_constraints(media, document["required"], document["excluded"])
    if required != set(document["required"]) or excluded != set(document["excluded"]):
        raise ValueError("Storyboard required/excluded arrays must retain effective feedback constraints")
    entries = document["items"]
    if not isinstance(entries, list) or len(entries) > MAX_ITEMS:
        raise ValueError("items must be an array of at most 500 entries")
    selected, durations, blocks = {}, [], {}
    for entry in entries:
        _object(entry, _ENTRY_KEYS, "storyboard entry")
        media_id = entry["media_id"]
        _text(media_id, "entry media_id", nonempty=True)
        if media_id not in media:
            raise ValueError("Unknown entry media ID: " + media_id)
        if media_id in excluded:
            raise ValueError("Entry selects excluded media: " + media_id)
        item = media[media_id]
        kind = entry["kind"]
        if kind not in ("image", "audio", "video") or kind != item.get("kind"):
            raise ValueError("Entry kind must match source media kind")
        duration = _number(entry["duration_seconds"], "entry duration_seconds")
        if duration <= 0:
            raise ValueError("Entry duration_seconds must be positive")
        durations.append(duration)
        _text(entry["reason"], "entry reason")
        if not isinstance(entry["required"], bool):
            raise ValueError("Entry required must be boolean")
        _object(entry["scores"], _COMPONENTS | {"total"}, "entry scores")
        for name, score in entry["scores"].items():
            if score is None and name in ("technical", "relevance"):
                continue
            if not 0 <= _number(score, name + " score") <= 1:
                raise ValueError("Scores must be in [0,1]")
        intervals = selected.setdefault(media_id, [])
        expected_required = media_id in required
        if kind == "image":
            if entry["start_seconds"] is not None or entry["end_seconds"] is not None:
                raise ValueError("Images require null source start/end")
            if intervals:
                raise ValueError("Duplicate image selection: " + media_id)
            intervals.append((None, None))
        else:
            start = _number(entry["start_seconds"], "entry start_seconds")
            end = _number(entry["end_seconds"], "entry end_seconds")
            origin, source_end = _coverage(item)
            if not origin <= start < end <= source_end:
                raise ValueError("Entry must have positive duration within source coverage")
            if not math.isclose(duration, end - start, rel_tol=1e-9, abs_tol=1e-9):
                raise ValueError("Entry duration_seconds must match its source interval")
            if any(_overlaps(start, end, a, b) for a, b in intervals):
                raise ValueError("Overlapping source intervals: " + media_id)
            for segment in segments[media_id]:
                if _overlaps(start, end, segment["start_seconds"], segment["end_seconds"]):
                    if segment["selection"] == "exclude":
                        raise ValueError("Entry overlaps excluded segment: " + media_id)
                    expected_required = True
            if media_id not in blocks:
                blocks[media_id] = conversation_blocks(item)
            for block_start, block_end in blocks[media_id]:
                if _overlaps(start, end, block_start, block_end) and not start <= block_start < block_end <= end:
                    raise ValueError("Entry cuts an atomic conversation block: " + media_id)
            intervals.append((start, end))
        if entry["required"] != expected_required:
            raise ValueError("Entry required flag must match effective media/segment constraints")
    missing = required - selected.keys()
    if missing:
        raise ValueError("Missing required media: " + ", ".join(sorted(missing)))
    for media_id, source_segments in segments.items():
        includes = [segment for segment in source_segments if segment["selection"] == "include"]
        if not includes:
            continue
        intervals = merge_intervals(selected.get(media_id, []))
        for segment in includes:
            if not any(start <= segment["start_seconds"] < segment["end_seconds"] <= end
                       for start, end in intervals):
                raise ValueError("Missing coverage of required include segment: " + media_id)
    actual_total = _sum(durations, "total duration")
    if not math.isclose(total, actual_total, rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError("total_seconds must match the sum of entry durations")
    if actual_total > budget and not math.isclose(actual_total, budget, rel_tol=0, abs_tol=1e-9):
        raise ValueError("Storyboard exceeds duration_seconds budget")
    return document
