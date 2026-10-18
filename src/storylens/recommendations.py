"""Deterministic, inference-free selections from existing catalog evidence."""

from collections import Counter
import math

from .feedback import _coverage
from .storyboard import conversation_blocks, effective_constraints, merge_intervals


STRATEGY_WEIGHTS = {
    "balanced": {"technical": 0.4, "relevance": 0.25, "story": 0.35},
    "technical": {"technical": 0.8, "relevance": 0.1, "story": 0.1},
    "story": {"technical": 0.15, "relevance": 0.15, "story": 0.7},
}
MAX_CANDIDATES = 100000
_CAVEATS = [
    "Technical scores are stored signal proxies, not aesthetic quality: image sharpness and unclipped exposure; video sampled Laplacian variance/(variance+100) and unclipped luma; audio one minus source-wide clipping fraction. Available proxies are averaged; missing evidence is null.",
    "Relevance is supplied same-space cosine mapped from [-1,1] to [0,1]; absent retrieval evidence is null, not a negative judgment.",
    "Story is a timing/event heuristic: half inverse eligible event membership plus half conversation coverage. It is not emotional value, semantic comprehension or confidence; event rarity does not guarantee diverse selection.",
    "Conversation preservation uses stored transcript timestamps merged across gaps of at most two seconds. Imported evidence takes precedence; timestamps do not prove speech or semantic completeness.",
    "Totals renormalize configured weights over available components; no available positive-weight component yields zero. Greedy whole-candidate packing is deterministic, not an optimal knapsack solution.",
]


def _finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _positive(value, name):
    if not _finite(value) or value <= 0:
        raise ValueError(name + " must be a finite positive number")
    return value


def _unit(value):
    return value if _finite(value) and 0 <= value <= 1 else None


def _mean(values):
    values = [value for value in values if value is not None]
    return math.fsum(values) / len(values) if values else None


def _result(item, stage):
    return item.get("analysis", {}).get(stage, {}).get("result", {})


def _exposure(metrics, dark, bright):
    low, high = _unit(metrics.get(dark)), _unit(metrics.get(bright))
    return max(0.0, 1 - low - high) if low is not None and high is not None else None


def _technical(item, start, end):
    kind = item["kind"]
    if kind == "image":
        metrics = _result(item, "image_technical").get("metrics", {})
        return _mean([_unit(metrics.get("sharpness")),
                      _exposure(metrics.get("exposure", {}), "clipped_shadows_fraction", "clipped_highlights_fraction")])
    measurements = _result(item, kind + "_technical").get("measurements", {})
    if kind == "audio":
        clipped = _unit(measurements.get("clipping_sample_fraction"))
        return 1 - clipped if clipped is not None else None
    frame_scores = []
    for sample in measurements.get("frames", {}).get("samples", []):
        timestamp = sample.get("source_timestamp_seconds")
        if not _finite(timestamp) or not start <= timestamp < end:
            continue
        variance = sample.get("laplacian_variance_blur_proxy")
        sharpness = variance / (variance + 100) if _finite(variance) and variance >= 0 else None
        frame_scores.append(_mean([sharpness, _exposure(sample, "dark_pixel_fraction", "bright_pixel_fraction")]))
    clipped = _unit(measurements.get("audio", {}).get("clipping_sample_fraction"))
    return _mean([_mean(frame_scores), 1 - clipped if clipped is not None else None])


def _overlaps(first, second):
    return max(first[0], second[0]) < min(first[1], second[1])


def _subtract(intervals, removed):
    """Subtract a merged half-open union without moving source timestamps."""
    result = []
    for start, end in intervals:
        cursor = start
        for left, right in removed:
            if right <= cursor:
                continue
            if left >= end:
                break
            if cursor < left:
                result.append((cursor, left))
            cursor = max(cursor, right)
            if cursor >= end:
                break
        if cursor < end:
            result.append((cursor, end))
    return result


def _expand(intervals, blocks):
    expanded = []
    for start, end in intervals:
        for left, right in blocks:
            if _overlaps((start, end), (left, right)):
                start, end = min(start, left), max(end, right)
        expanded.append((start, end))
    return merge_intervals(expanded)


def _scene_intervals(item, intervals):
    """Split nonspeech gaps at stored scene boundaries, never conversation blocks."""
    boundaries = sorted({scene[key] for scene in _result(item, "video_technical").get("scenes", [])
                         for key in ("start_seconds", "end_seconds") if _finite(scene.get(key))})
    for left, right in intervals:
        points = [left] + [point for point in boundaries if left < point < right] + [right]
        yield from zip(points, points[1:])


def _rank(entry):
    return (-entry["scores"]["total"], entry["media_id"],
            entry["start_seconds"] if entry["start_seconds"] is not None else 0)


def recommend(items, *, duration_seconds=60, strategy="balanced", weights=None,
              required=None, excluded=None, relevance=None, image_seconds=4, clip_seconds=10):
    """Return a portable storyboard; never load media or perform model inference.

    Required segment unions are indivisible after conversation expansion. Other
    candidates are complete conversation blocks and clip-sized nonspeech gaps,
    respecting stored video scene boundaries.
    Whole-media requirements reserve one feasible candidate per identity before
    optional score-ranked packing. Playback order is media identity/source time.
    """
    _positive(duration_seconds, "duration_seconds")
    _positive(image_seconds, "image_seconds")
    _positive(clip_seconds, "clip_seconds")
    if not isinstance(strategy, str) or strategy not in STRATEGY_WEIGHTS:
        raise ValueError("strategy must be balanced, technical or story")
    configured = STRATEGY_WEIGHTS[strategy] if weights is None else weights
    if (not isinstance(configured, dict) or configured.keys() != {"technical", "relevance", "story"}
            or any(not _finite(value) or value < 0 for value in configured.values())
            or not any(value > 0 for value in configured.values())):
        raise ValueError("weights require exactly technical, relevance and story with finite nonnegative values and a positive total")
    # Scaling first also supports finite weights whose unscaled sum overflows.
    scale = max(configured.values())
    denominator = math.fsum(value / scale for value in configured.values())
    normalized = {name: value / scale / denominator for name, value in configured.items()}
    if isinstance(items, (str, bytes, dict)):
        raise ValueError("items must be an iterable of catalog media")
    media = {}
    for item in items:
        if (not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]
                or item.get("kind") not in ("image", "audio", "video")):
            raise ValueError("items require a media id and image, audio or video kind")
        if item["id"] in media:
            raise ValueError("Duplicate media ID: " + item["id"])
        media[item["id"]] = item
    required_ids, excluded_ids, feedback_segments = effective_constraints(media, required, excluded)
    if relevance is None:
        relevance = {}
    if (not isinstance(relevance, dict) or any(not isinstance(key, str) or not _finite(value)
            or not -1 <= value <= 1 for key, value in relevance.items())):
        raise ValueError("relevance must map media IDs to finite cosine scores in [-1,1]")
    events = Counter(item["event_id"] for identifier, item in media.items()
                     if identifier not in excluded_ids and isinstance(item.get("event_id"), str) and item["event_id"])
    warnings = []
    mandatory = []
    candidates = []
    atomic_candidates = set()
    candidate_count = 0

    def entry(item, interval, blocks, mandated=False):
        nonlocal candidate_count
        candidate_count += 1
        if candidate_count > MAX_CANDIDATES:
            raise ValueError("Recommendations exceed 100000 candidates; use longer clips or a smaller catalog")
        identifier = item["id"]
        start, end = interval if interval is not None else (None, None)
        duration = end - start if interval is not None else image_seconds
        technical = _technical(item, start, end)
        related = (relevance[identifier] + 1) / 2 if identifier in relevance else None
        representation = 1 / events[item["event_id"]] if item.get("event_id") in events else 0
        conversation = (math.fsum(max(0, min(end, right) - max(start, left)) for left, right in blocks) / duration
                        if interval is not None else 0)
        story = min(1.0, 0.5 * representation + 0.5 * conversation)
        components = {"technical": technical, "relevance": related, "story": story}
        available = math.fsum(normalized[name] for name, value in components.items() if value is not None)
        total = (math.fsum(value * normalized[name] for name, value in components.items() if value is not None) / available
                 if available else 0.0)
        reason = ("Mandatory feedback interval" if mandated else "Required media selection" if identifier in required_ids
                  else "Optional stored-evidence candidate")
        reason += "; technical=" + ("unavailable" if technical is None else f"{technical:.3f}")
        reason += ", relevance=" + ("unavailable" if related is None else f"{related:.3f}")
        reason += f", story heuristic={story:.3f}; " + ("atomic conversation timing preserved" if conversation else "no intersecting conversation block")
        return {"media_id": identifier, "kind": item["kind"], "start_seconds": start,
                "end_seconds": end, "duration_seconds": duration,
                "required": mandated or identifier in required_ids,
                "scores": {**components, "total": min(1.0, max(0.0, total))}, "reason": reason}

    for identifier in sorted(media):
        if identifier in excluded_ids:
            continue
        item = media[identifier]
        segments = feedback_segments[identifier]
        included = merge_intervals([(part["start_seconds"], part["end_seconds"]) for part in segments if part["selection"] == "include"])
        forbidden = merge_intervals([(part["start_seconds"], part["end_seconds"]) for part in segments if part["selection"] == "exclude"])
        if item["kind"] == "image":
            candidates.append(entry(item, None, []))
            continue
        try:
            origin, source_end = _coverage(item)
            blocks = conversation_blocks(item)
            if any(not origin <= left < right <= source_end for left, right in blocks):
                raise ValueError("Conversation timing lies outside source coverage")
        except ValueError as error:
            if identifier in required_ids or included:
                raise ValueError("Required media " + identifier + ": " + str(error)) from error
            warnings.append("Skipped " + identifier + ": source/conversation timing unavailable or invalid")
            continue
        interrupted = [block for block in blocks if any(_overlaps(block, span) for span in forbidden)]
        expanded = _expand(included, blocks)
        if any(_overlaps(span, blocked) for span in expanded for blocked in forbidden + interrupted):
            raise ValueError("Mandatory segment intersects an excluded conversation block: " + identifier)
        if interrupted:
            warnings.append("Skipped excluded-interrupted conversation blocks for " + identifier)
        mandatory.extend(entry(item, span, blocks, True) for span in expanded)
        removed = merge_intervals(forbidden + interrupted + expanded)
        available_blocks = [block for block in blocks if not any(_overlaps(block, span) for span in removed)]
        candidates.extend(entry(item, block, blocks) for block in available_blocks)
        atomic_candidates.update((identifier, left, right) for left, right in available_blocks)
        gaps = _subtract([(origin, source_end)], merge_intervals(removed + blocks))
        for left, right in _scene_intervals(item, gaps):
            if (right - left) / clip_seconds > MAX_CANDIDATES - candidate_count:
                raise ValueError("Recommendations exceed 100000 candidates; use longer clips or a smaller catalog")
            start = left
            while start < right:
                end = min(right, start + clip_seconds)
                if end <= start:
                    raise ValueError("clip_seconds is below source timestamp precision")
                candidates.append(entry(item, (start, end), blocks))
                start = end

    selected = list(mandatory)
    used = math.fsum(part["duration_seconds"] for part in selected)
    if used > duration_seconds:
        raise ValueError("Mandatory intervals exceed duration budget")
    satisfied = {part["media_id"] for part in selected}
    groups = {identifier: [] for identifier in sorted(required_ids - satisfied)}
    for candidate in candidates:
        if candidate["media_id"] in groups:
            groups[candidate["media_id"]].append(candidate)
    minima = {}
    for identifier, group in groups.items():
        if not group:
            raise ValueError("No admissible selection for required media: " + identifier)
        minima[identifier] = min(part["duration_seconds"] for part in group)
    if used + math.fsum(minima.values()) > duration_seconds:
        raise ValueError("Required selections exceed duration budget")
    pending_minima = dict(minima)
    selected_candidates = set()
    for identifier, group in groups.items():
        del pending_minima[identifier]
        reserved = math.fsum(pending_minima.values())
        chosen = next((part for part in sorted(group, key=_rank)
                       if math.fsum([used, part["duration_seconds"], reserved]) <= duration_seconds), None)
        if chosen is None:
            raise ValueError("Required selections exceed duration budget")
        selected.append(chosen)
        selected_candidates.add(id(chosen))
        used = math.fsum(part["duration_seconds"] for part in selected)
    for candidate in sorted(candidates, key=_rank):
        if id(candidate) in selected_candidates:
            continue
        if math.fsum([used, candidate["duration_seconds"]]) <= duration_seconds:
            selected.append(candidate)
            used = math.fsum(part["duration_seconds"] for part in selected)
        elif (candidate["media_id"], candidate["start_seconds"], candidate["end_seconds"]) in atomic_candidates:
            warnings.append("Skipped atomic conversation block for " + candidate["media_id"]
                            + ": does not fit remaining duration budget")
        if len(selected) > 500:
            raise ValueError("Storyboard exceeds maximum of 500 entries")
    if len(selected) > 500:
        raise ValueError("Storyboard exceeds maximum of 500 entries")
    selected.sort(key=lambda part: (part["media_id"], part["start_seconds"] if part["start_seconds"] is not None else 0))
    return {"schema_version": "1.0", "kind": "storylens_storyboard", "title": "StoryLens storyboard",
            "duration_seconds": duration_seconds, "total_seconds": math.fsum(part["duration_seconds"] for part in selected),
            "strategy": strategy, "weights": normalized, "required": sorted(required_ids), "excluded": sorted(excluded_ids),
            "items": selected, "warnings": list(dict.fromkeys(warnings)),
            "provenance": {"algorithm": "recommendations-1", "caveats": list(_CAVEATS)}}
