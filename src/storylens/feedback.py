"""Private user corrections, independent of disposable analysis and model output."""

from contextlib import contextmanager
from datetime import datetime
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import uuid

from .storage import now


FIELDS = frozenset({"selection", "capture_time", "gps", "event_id", "segments"})
MAX_SEGMENTS = 100
MAX_TEXT = 256
_CAPTURE_TIME = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?\Z")


def _number(value, name):
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(name + " must be a finite number")
    return value


def _text(value, name, *, nonempty=False):
    if not isinstance(value, str) or len(value) > MAX_TEXT or (nonempty and not value.strip()):
        raise ValueError(f"{name} must be {'nonempty ' if nonempty else ''}text of at most {MAX_TEXT} characters")
    return value


def _metadata_number(value):
    # ffprobe emits decimal strings, unlike the JSON numbers accepted from users.
    if isinstance(value, bool):
        raise ValueError("Invalid metadata timing")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("Cannot validate source coverage from metadata") from error
    if not math.isfinite(result):
        raise ValueError("Cannot validate source coverage from metadata")
    return result


def _coverage(media):
    kind = media.get("kind")
    if kind not in ("audio", "video"):
        raise ValueError("segments are only supported for audio/video")
    analysis = media.get("analysis", {})
    timing = analysis.get(kind + "_technical", {}).get("result", {}).get("source_timing")
    if timing is not None:
        start = _number(timing.get("origin_seconds"), "source origin")
        end = _number(timing.get("end_seconds"), "source end")
    else:
        metadata = analysis.get("metadata", {}).get("result", {})
        stream = next((item for item in metadata.get("streams", [])
                       if item.get("codec_type") == kind
                       and not item.get("disposition", {}).get("attached_pic")), None)
        if stream is None:
            raise ValueError("Cannot validate source coverage without timing evidence")
        if stream.get("start_time") not in (None, "N/A"):
            start = _metadata_number(stream["start_time"])
        elif stream.get("start_pts") not in (None, "N/A") and stream.get("time_base"):
            try:
                start = float(Fraction(str(stream["start_pts"])) * Fraction(stream["time_base"]))
            except (TypeError, ValueError, ZeroDivisionError, OverflowError) as error:
                raise ValueError("Cannot validate source origin") from error
        else:
            raise ValueError("Cannot validate unknown source origin; run technical analysis")
        duration = stream.get("duration")
        if duration in (None, "N/A") and stream.get("duration_ts") not in (None, "N/A") and stream.get("time_base"):
            try:
                duration = float(Fraction(str(stream["duration_ts"])) * Fraction(stream["time_base"]))
            except (TypeError, ValueError, ZeroDivisionError, OverflowError) as error:
                raise ValueError("Cannot validate source duration") from error
        # Nonzero-origin container durations can include the offset, not just stream coverage.
        if duration in (None, "N/A"):
            if start != 0:
                raise ValueError("Cannot validate nonzero-origin coverage without stream duration")
            duration = metadata.get("duration_seconds")
        end = start + _metadata_number(duration)
    if not math.isfinite(start) or not math.isfinite(end) or end <= start:
        raise ValueError("Cannot validate source coverage")
    return start, end


def _segments(value, media):
    if not isinstance(value, list) or len(value) > MAX_SEGMENTS:
        raise ValueError(f"segments must be a list of at most {MAX_SEGMENTS} entries")
    if media.get("kind") not in ("audio", "video"):
        raise ValueError("segments are only supported for audio/video")
    if not value:
        return []
    origin, source_end = _coverage(media)
    result = []
    for segment in value:
        required = {"start_seconds", "end_seconds", "selection"}
        if not isinstance(segment, dict) or not required <= segment.keys() or segment.keys() - required - {"label"}:
            raise ValueError("segments require start_seconds, end_seconds, selection and optional label")
        start = _number(segment["start_seconds"], "segment start")
        end = _number(segment["end_seconds"], "segment end")
        if not origin <= start < end <= source_end:
            raise ValueError("segment must have positive duration within absolute source coverage")
        if segment["selection"] not in ("include", "exclude"):
            raise ValueError("segment selection must be include or exclude")
        item = {"start_seconds": start, "end_seconds": end, "selection": segment["selection"]}
        if "label" in segment:
            item["label"] = _text(segment["label"], "segment label")
        for previous in result:
            if (previous["selection"] != item["selection"]
                    and max(previous["start_seconds"], start) < min(previous["end_seconds"], end)):
                raise ValueError("conflicting include/exclude segment overlaps are not allowed")
        result.append(item)
    return result


class FeedbackStore:
    """SQLite schema 1; no foreign keys or writes to the generated catalog."""

    def __init__(self, catalog_path):
        self.path = Path(catalog_path).with_suffix(".feedback.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        # Windows uses the directory's inherited ACLs, not POSIX permission bits.
        if os.name == "posix":
            self.path.chmod(0o600)
        self.connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        try:
            with self._transaction(write=True):
                version = self.connection.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, 1):
                    raise RuntimeError(f"Unsupported feedback schema {version}; upgrade StoryLens")
                if version == 0:
                    self.connection.execute("CREATE TABLE media_feedback (media_id TEXT PRIMARY KEY, values_json TEXT NOT NULL, updated_at TEXT NOT NULL)")
                    self.connection.execute("CREATE TABLE manual_events (id TEXT PRIMARY KEY, title TEXT NOT NULL, updated_at TEXT NOT NULL)")
                    self.connection.execute("CREATE TABLE preferences (id INTEGER PRIMARY KEY CHECK(id=1), values_json TEXT NOT NULL, updated_at TEXT NOT NULL)")
                    self.connection.execute("PRAGMA user_version=1")
        except BaseException:
            self.connection.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.connection.close()

    @contextmanager
    def _transaction(self, *, write=False):
        self.connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            yield
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def get(self, media_id):
        row = self.connection.execute("SELECT values_json, updated_at FROM media_feedback WHERE media_id=?", (media_id,)).fetchone()
        return {"schema_version": "1.0", "media_id": media_id,
                "values": json.loads(row["values_json"]) if row else {},
                "updated_at": row["updated_at"] if row else None}

    def _save(self, media_id, values):
        self.connection.execute("""INSERT INTO media_feedback VALUES(?,?,?)
            ON CONFLICT(media_id) DO UPDATE SET values_json=excluded.values_json, updated_at=excluded.updated_at""",
            (media_id, json.dumps(values, allow_nan=False), now()))
        return self.get(media_id)

    def update(self, media_id, patch, media):
        if not isinstance(patch, dict) or patch.keys() - FIELDS:
            raise ValueError("feedback accepts only selection, capture_time, gps, event_id and segments")
        validated = {}
        for key, value in patch.items():
            if key == "selection" and value not in ("neutral", "include", "exclude"):
                raise ValueError("selection must be neutral, include or exclude")
            if key == "capture_time" and value is not None:
                if not isinstance(value, str) or len(value) > 64 or not _CAPTURE_TIME.fullmatch(value):
                    raise ValueError("capture_time must be an ISO8601 date-time or null")
                try:
                    datetime.fromisoformat(value)
                except ValueError as error:
                    raise ValueError("capture_time must be an ISO8601 date-time or null") from error
            if key == "gps" and value is not None:
                if not isinstance(value, dict) or value.keys() != {"latitude", "longitude"}:
                    raise ValueError("gps must contain latitude and longitude, or be null")
                latitude = _number(value["latitude"], "latitude")
                longitude = _number(value["longitude"], "longitude")
                if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                    raise ValueError("gps coordinates are out of range")
                value = {"latitude": latitude, "longitude": longitude}
            if key == "event_id" and value is not None and not isinstance(value, str):
                raise ValueError("event_id must be a known manual event ID or null")
            if key == "segments":
                value = _segments(value, media)
            validated[key] = value
        with self._transaction(write=True):
            if validated.get("event_id") is not None:
                self._event(validated["event_id"])
            values = self.get(media_id)["values"]
            values.update(validated)
            return self._save(media_id, values)

    def reset(self, media_id, fields=None):
        if fields is not None and (not isinstance(fields, (list, tuple))
                                   or any(not isinstance(key, str) or key not in FIELDS for key in fields)):
            raise ValueError("reset fields must be a list of supported feedback keys")
        with self._transaction(write=True):
            values = self.get(media_id)["values"]
            if fields is None:
                values.clear()
            else:
                for key in fields:
                    values.pop(key, None)
            return self._save(media_id, values)

    @staticmethod
    def _descriptor(row):
        return {"schema_version": "1.0", **dict(row)}

    def _event(self, event_id):
        row = self.connection.execute("SELECT id,title,updated_at FROM manual_events WHERE id=?", (event_id,)).fetchone()
        if row is None:
            raise KeyError("Unknown manual event ID: " + event_id)
        return self._descriptor(row)

    def create_event(self, title):
        _text(title, "event title", nonempty=True)
        event_id = "manual:" + uuid.uuid4().hex
        with self._transaction(write=True):
            self.connection.execute("INSERT INTO manual_events VALUES(?,?,?)", (event_id, title, now()))
            return self._event(event_id)

    def rename_event(self, event_id, title):
        _text(title, "event title", nonempty=True)
        with self._transaction(write=True):
            self._event(event_id)
            self.connection.execute("UPDATE manual_events SET title=?,updated_at=? WHERE id=?", (title, now(), event_id))
            return self._event(event_id)

    def manual_events(self):
        return [self._descriptor(row) for row in self.connection.execute("SELECT id,title,updated_at FROM manual_events ORDER BY id")]

    def members(self, event_id):
        with self._transaction():
            self._event(event_id)
            return [row["media_id"] for row in self.connection.execute("SELECT media_id,values_json FROM media_feedback ORDER BY media_id")
                    if json.loads(row["values_json"]).get("event_id") == event_id]

    def _stored_preferences(self):
        row = self.connection.execute("SELECT values_json,updated_at FROM preferences WHERE id=1").fetchone()
        return (json.loads(row["values_json"]), row["updated_at"]) if row else ({}, None)

    def preferences(self):
        from .events import normalize_settings

        values, updated_at = self._stored_preferences()
        return {"schema_version": "1.0", "values": normalize_settings(values), "updated_at": updated_at}

    def set_preferences(self, patch):
        from .events import normalize_settings

        if not isinstance(patch, dict):
            raise ValueError("preferences patch must be an object")
        with self._transaction(write=True):
            values, _ = self._stored_preferences()
            values.update(patch)
            normalize_settings(values)
            self.connection.execute("""INSERT INTO preferences VALUES(1,?,?)
                ON CONFLICT(id) DO UPDATE SET values_json=excluded.values_json,updated_at=excluded.updated_at""",
                (json.dumps(values, allow_nan=False), now()))
            return self.preferences()

    def snapshot(self):
        with self._transaction():
            return {"media": {row["media_id"]: json.loads(row["values_json"])
                              for row in self.connection.execute("SELECT media_id,values_json FROM media_feedback ORDER BY media_id")},
                    "events": self.manual_events(), "preferences": self.preferences()["values"]}
