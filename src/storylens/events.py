"""Pure, bounded event candidates from stored evidence and independent user corrections.

No source access, model loading, filesystem clocks, or local-timezone conversion occurs.
"""

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import re


ALGORITHM = "events-1"
MAX_ITEMS = 10000
DEFAULT_SETTINGS = {
    "gap_seconds": 7200,
    "distance_km": 50,
    "semantic_gap_seconds": 21600,
    "semantic_similarity": 0.85,
    "max_event_seconds": 43200,
    "semantic_depth": "standard",
}


def normalize_settings(values=None):
    """Return a fresh complete settings mapping, rejecting ambiguous configuration."""
    if values is None:
        values = {}
    if not isinstance(values, Mapping):
        raise ValueError("Event settings must be an object")
    unknown = set(values) - DEFAULT_SETTINGS.keys()
    if unknown:
        raise ValueError("Unknown event settings: " + ", ".join(sorted(map(str, unknown))))
    result = {**DEFAULT_SETTINGS, **values}
    for key, upper in (("gap_seconds", 604800), ("semantic_gap_seconds", 604800),
                       ("max_event_seconds", 31536000), ("distance_km", 20040)):
        value = result[key]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not 0 < value <= upper or not math.isfinite(value)):
            raise ValueError(f"{key} must be finite and between 0 (exclusive) and {upper}")
    value = result["semantic_similarity"]
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not 0 <= value <= 1 or not math.isfinite(value)):
        raise ValueError("semantic_similarity must be between 0 and 1")
    if result["semantic_depth"] not in ("fast", "standard", "deep"):
        raise ValueError("semantic_depth must be fast, standard or deep")
    if not result["gap_seconds"] <= result["semantic_gap_seconds"] <= result["max_event_seconds"]:
        raise ValueError("Event settings require gap_seconds <= semantic_gap_seconds <= max_event_seconds")
    return result


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def _stage(item, name, depth=None):
    """Catalog.get supplies one current result; accept explicit stage history too."""
    stages = item.get("analysis", {}).get(name)
    if stages is None:
        return {}
    candidates = stages if isinstance(stages, list) else [stages]
    candidates = [stage for stage in candidates if isinstance(stage, dict)
                  and isinstance(stage.get("result"), dict)
                  and (depth is None or stage["result"].get("provenance", {}).get("depth") == depth)]
    return max(candidates, key=lambda stage: (stage.get("analyzed_at", ""),
                                            stage.get("cache_key", "")), default={})


def _parse_time(value, *, exif=False, offset=None):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if exif:
        text = re.sub(r"^(\d{4}):(\d{2}):(\d{2}) ", r"\1-\2-\3T", text)
        if offset is not None:
            if (not isinstance(offset, str) or not re.fullmatch(r"[+-]\d{2}:\d{2}", offset)
                    or int(offset[1:3]) >= 24 or int(offset[4:6]) >= 60):
                return None
            text += offset
    # A date alone is not evidence of a capture time. Do not infer midnight.
    if not re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", text):
        return None
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if result.tzinfo is not None:
            return result.astimezone(timezone.utc)
        return result
    except (ValueError, OverflowError):
        return None


def _time_evidence(value, source, cache_key=None, *, exif=False, offset=None):
    parsed = _parse_time(value, exif=exif, offset=offset)
    status = ("unresolved" if parsed is None else
              "explicit_offset" if parsed.tzinfo is not None else "timezone_ambiguous")
    return parsed, {"source": source, "cache_key": cache_key,
                    "source_timestamp": value if isinstance(value, str) else None,
                    "source_offset": offset, "value": parsed.isoformat() if parsed else None,
                    "timezone_status": status}


def _capture_time(item, stage):
    metadata = stage.get("result", {})
    exif = metadata.get("exif", {})
    candidates = []
    for tags, source in ((exif.get("ExifIFD", {}), "metadata.exif.ExifIFD"),
                         (exif, "metadata.exif")):
        if tags.get("DateTimeOriginal") is not None:
            candidates.append((tags["DateTimeOriginal"], source + ".DateTimeOriginal",
                               True, tags.get("OffsetTimeOriginal")))
    raw = metadata.get("raw", {})
    tags = metadata.get("tags", raw.get("format", {}).get("tags", {}))
    if tags.get("creation_time") is not None:
        candidates.append((tags["creation_time"], "metadata.ffprobe.format.tags.creation_time", False, None))
    streams = metadata.get("streams", raw.get("streams", []))
    # Stream index takes precedence over supplied array order.
    streams = sorted(enumerate(streams), key=lambda pair: (pair[1].get("index", pair[0]), pair[0]))
    for position, stream in streams:
        value = stream.get("tags", {}).get("creation_time")
        if value is not None:
            source = f"metadata.ffprobe.streams[{stream.get('index', position)}].tags.creation_time"
            candidates.append((value, source, False, None))
    generated = (None, {"source": "metadata.capture_time_absent", "cache_key": stage.get("cache_key"),
                        "source_timestamp": None, "source_offset": None, "value": None,
                        "timezone_status": "unresolved"})
    invalid_sources = []
    for value, source, is_exif, offset in candidates:
        evidence = _time_evidence(value, source, stage.get("cache_key"), exif=is_exif, offset=offset)
        generated = evidence
        if evidence[0] is not None:
            break
        invalid_sources.append(source)
    if invalid_sources:
        generated[1]["invalid_sources"] = invalid_sources
    feedback = item.get("feedback", {})
    if "capture_time" not in feedback:
        return generated
    corrected = _time_evidence(feedback["capture_time"], "feedback.capture_time")
    corrected[1]["original"] = generated[1]
    corrected[1]["explicit_unknown"] = feedback["capture_time"] is None
    return corrected


def _gps_value(value):
    if not isinstance(value, dict):
        return None
    coordinates = []
    for key, bound in (("latitude", 90), ("longitude", 180)):
        number = value.get(key)
        if (isinstance(number, bool) or not isinstance(number, (int, float))
                or not math.isfinite(number) or not -bound <= number <= bound):
            return None
        coordinates.append(number)
    return dict(zip(("latitude", "longitude"), coordinates))


def _gps(item, stage):
    generated = {"source": "metadata.gps", "cache_key": stage.get("cache_key"),
                 "value": _gps_value(stage.get("result", {}).get("gps"))}
    feedback = item.get("feedback", {})
    if "gps" not in feedback:
        return generated
    return {"source": "feedback.gps", "cache_key": None, "value": _gps_value(feedback["gps"]),
            "explicit_unknown": feedback["gps"] is None, "original": generated}


def _embedding(item, depth):
    stage = _stage(item, "semantic_embeddings", depth)
    result = stage.get("result", {})
    provenance = result.get("provenance", {})
    if (not provenance.get("space") or not provenance.get("algorithm")
            or not isinstance(provenance.get("provider"), dict)):
        return None
    vectors = []
    dimension = None
    for frame in result.get("items", []):
        vector = frame.get("vector")
        if (not isinstance(vector, (list, tuple)) or not 1 <= len(vector) <= 65536
                or any(isinstance(x, bool) or not isinstance(x, (int, float))
                       or not math.isfinite(x) for x in vector)):
            return None
        norm = math.hypot(*vector)
        if not math.isfinite(norm) or not math.isclose(norm, 1, rel_tol=1e-5, abs_tol=1e-5):
            return None
        if dimension is not None and dimension != len(vector):
            return None
        dimension = len(vector)
        vectors.append(vector)
    if not vectors:
        return None
    # A single aggregate per media avoids frame-pair and media-pair quadratic work.
    mean = [math.fsum(vector[i] for vector in vectors) / len(vectors) for i in range(dimension)]
    norm = math.hypot(*mean)
    if not norm:
        return None
    vector = [value / norm for value in mean]
    identity = {"space": provenance["space"], "depth": depth, "algorithm": provenance["algorithm"],
                "model_digest": _digest(provenance["provider"]), "dimension": dimension}
    return {"identity": identity, "vector": vector,
            "evidence": {**identity, "cache_key": stage.get("cache_key"),
                         "aggregation": "normalized_mean_of_stored_vectors", "vector_count": len(vectors),
                         "content_digest": _digest(result.get("items", []))}}


def _distance(left, right):
    lat1, lat2 = math.radians(left["latitude"]), math.radians(right["latitude"])
    dlat = lat2 - lat1
    dlon = math.radians(right["longitude"] - left["longitude"])
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(min(1, max(0, a))))


def _join(previous, current, first, gps_anchor, settings):
    gap = (current["time"] - previous["time"]).total_seconds()
    span = (current["time"] - first["time"]).total_seconds()
    distance = (_distance(gps_anchor["gps"]["value"], current["gps"]["value"])
                if gps_anchor and current["gps"]["value"] else None)
    gps_veto = distance is not None and distance > settings["distance_km"]
    left, right = previous["embedding"], current["embedding"]
    compatible = bool(left and right and left["identity"] == right["identity"])
    cosine = (max(-1, min(1, math.fsum(a * b for a, b in zip(left["vector"], right["vector"]))))
              if compatible else None)
    if span > settings["max_event_seconds"]:
        decision = "span_limit"
    elif gps_veto:
        decision = "gps_contradiction"
    elif gap <= settings["gap_seconds"]:
        decision = "close_time"
    elif (gap <= settings["semantic_gap_seconds"] and cosine is not None
          and cosine >= settings["semantic_similarity"]):
        decision = "semantic_bridge"
    else:
        decision = "time_gap"
    return {"decision": decision, "joined": decision in ("close_time", "semantic_bridge"),
            "previous_media_id": previous["id"], "gap_seconds": gap, "span_seconds": span,
            "distance_km": distance, "gps_reference_media_id": gps_anchor["id"] if gps_anchor else None,
            "gps_veto": gps_veto, "embedding_compatible": compatible, "cosine": cosine}


def _event(rows, kind, manual=None):
    times = [row["time"] for row in rows if row["time"] is not None]
    statuses = {row["capture"]["timezone_status"] for row in rows}
    status = next(iter(statuses)) if len(statuses) == 1 else "unresolved"
    comparable = status in ("explicit_offset", "timezone_ambiguous")
    start, end = (min(times), max(times)) if times and comparable else (None, None)
    ids = sorted(row["id"] for row in rows)
    identifier = manual["id"] if manual else kind + "-" + _digest(ids)
    warnings = []
    if status != "explicit_offset":
        warnings.append("Capture timezone is ambiguous or unresolved; no local timezone or UTC offset was inferred.")
    if kind == "undated":
        title = "Undated media — review required"
        title_provenance = {"kind": "review_bucket", "basis": "missing_or_invalid_capture_time"}
        warnings.append("This review bucket asserts no factual event or relationship between its members.")
    elif manual:
        title = manual["title"]
        title_provenance = {"kind": "user", "event_id": identifier}
        if not comparable:
            warnings.append("Manual membership includes incomparable time domains; no combined time range is asserted.")
    else:
        first_date, last_date = start.date().isoformat(), end.date().isoformat()
        title = first_date if first_date == last_date else first_date + " – " + last_date
        boundaries = [rows[0]] if len(rows) == 1 else [rows[0], rows[-1]]
        title_provenance = {"kind": "metadata_date", "basis": "effective_capture_time",
                            "timezone_status": status, "sources": [
                                {"media_id": row["id"], "source": row["capture"]["source"],
                                 "cache_key": row["capture"]["cache_key"]} for row in boundaries]}
    if any(row["join"].get("decision") == "semantic_bridge" for row in rows):
        warnings.append("Semantic bridges are similarity hypotheses, not factual event observations.")
    members = []
    for row in rows:
        evidence = {"capture_time": row["capture"], "gps": row["gps"],
                    "embedding": row["embedding"]["evidence"] if row["embedding"] else None,
                    "join": row["join"]}
        if "segments" in row["feedback"]:
            evidence["segments"] = {"source": "feedback.segments", "values": deepcopy(row["feedback"]["segments"])}
        members.append({"media_id": row["id"], "selection": row["feedback"].get("selection", "neutral"),
                        "evidence": evidence})
    return {"id": identifier, "title": title, "title_provenance": title_provenance, "kind": kind,
            "start_time": start.isoformat() if start else None, "end_time": end.isoformat() if end else None,
            "timezone_status": status, "members": members, "member_count": len(members), "warnings": warnings}


def group_media(items, settings=None, manual_events=None):
    """Generate complete candidates in O(n log n + stored embedding size), n <= 10000.

    Manual descriptors are {id, title}; assignments live only in item.feedback.event_id.
    All membership evidence is retained for the service's independent pagination.
    """
    settings = normalize_settings(settings)
    rows, seen = [], set()
    for item in items:
        if len(rows) >= MAX_ITEMS:
            raise ValueError("Event grouping supports at most 10000 media items")
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise ValueError("Event media IDs must be nonempty and unique")
        seen.add(identifier)
        stage = _stage(item, "metadata")
        capture, evidence = _capture_time(item, stage)
        feedback = deepcopy(item.get("feedback", {}))
        if feedback.get("selection", "neutral") not in ("neutral", "include", "exclude"):
            raise ValueError("Invalid selection correction")
        rows.append({"id": identifier, "time": capture, "capture": evidence, "gps": _gps(item, stage),
                     "embedding": _embedding(item, settings["semantic_depth"]), "feedback": feedback,
                     "join": {"decision": "seed", "joined": False}})
    if manual_events is None:
        manual_events = []
    if isinstance(manual_events, Mapping):
        manual_events = [{**value, "id": key} for key, value in manual_events.items()]
    registry = {}
    for descriptor in manual_events:
        identifier, title = descriptor.get("id"), descriptor.get("title")
        if (not isinstance(identifier, str) or not identifier or identifier in registry
                or not isinstance(title, str) or not title.strip()):
            raise ValueError("Manual event descriptors require unique IDs and nonempty titles")
        registry[identifier] = {"id": identifier, "title": title}
    manual_groups, domains, undated = {}, {"explicit_offset": [], "timezone_ambiguous": []}, []
    for row in rows:
        assignment = row["feedback"].get("event_id")
        if assignment is not None:
            if assignment not in registry:
                raise ValueError("Unknown manual event ID: " + str(assignment))
            row["join"] = {"decision": "manual_assignment", "joined": True, "event_id": assignment}
            manual_groups.setdefault(assignment, []).append(row)
        elif row["time"] is None:
            row["join"] = {"decision": "undated_review", "joined": False}
            undated.append(row)
        else:
            domains[row["capture"]["timezone_status"]].append(row)
    events = []
    for identifier in sorted(manual_groups):
        events.append(_event(sorted(manual_groups[identifier], key=lambda row: row["id"]), "manual", registry[identifier]))
    for domain in domains.values():
        domain.sort(key=lambda row: (row["time"], row["id"]))
        current, gps_anchor = [], None
        for row in domain:
            if current:
                row["join"] = _join(current[-1], row, current[0], gps_anchor, settings)
                if not row["join"]["joined"]:
                    events.append(_event(current, "automatic"))
                    current, gps_anchor = [], None
            current.append(row)
            if row["gps"]["value"] is not None:
                gps_anchor = row
        if current:
            events.append(_event(current, "automatic"))
    if undated:
        events.append(_event(sorted(undated, key=lambda row: row["id"]), "undated"))
    # Digest only decision inputs, not paths, filesystem times, or unrelated stage payloads.
    digest_rows = []
    for row in sorted(rows, key=lambda row: row["id"]):
        digest_rows.append({"media_id": row["id"], "capture_time": row["capture"], "gps": row["gps"],
                            "embedding": row["embedding"]["evidence"] if row["embedding"] else None,
                            "feedback": row["feedback"]})
    assigned_descriptors = [registry[key] for key in sorted(manual_groups)]
    return {"schema_version": "1.0", "provenance": {
        "algorithm": ALGORITHM, "settings": settings,
        "input_digest": _digest({"media": digest_rows, "manual_events": assigned_descriptors}),
        "caveats": ["Automatic groups are reviewable candidates, not factual events or calibrated probabilities.",
                    "Only adjacent capture-time evidence is compared; known GPS contradictions veto joining.",
                    "Missing GPS is permissive; naive walltimes never mix with offset-aware capture times.",
                    "Scene cuts and visual dissimilarity never split close capture-time bursts.",
                    "Undated media form a review bucket without inferred relationships."]}, "events": events}
