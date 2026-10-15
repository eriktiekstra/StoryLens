"""Conservative, bounded near-duplicate candidates, never semantic similarity.

Input records are {id: str, result: ImageTechnicalExtractor.extract output}.
The similarity contract is a 16-digit hexadecimal perceptual_hash, hash_bits=64,
64 normalized RGB histogram bins, positive aspect_ratio, and luminance_stddev.
Pairs are ordered lexically by (left_id, right_id), independently of input order.
Pagination assumes unchanged records and parameters, not a mutable snapshot.
"""

import base64
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from functools import lru_cache
import heapq
import json
import math

MAX_TREE_VISITS = 4096
MAX_CANDIDATES = 512
ASPECT_TOLERANCE = 0.03
COLOR_TOLERANCE = 0.18
MIN_INFORMATION = 0.02


@dataclass
class _Feature:
    identity: str
    hash: int
    histogram: tuple
    aspect: float


class _Tree:
    def __init__(self, hashes):
        self.nodes = []
        for value in hashes:
            if not self.nodes:
                self.nodes.append((value, {}))
                continue
            index = 0
            while True:
                existing, children = self.nodes[index]
                distance = (existing ^ value).bit_count()
                if distance in children:
                    index = children[distance]
                else:
                    children[distance] = len(self.nodes)
                    self.nodes.append((value, {}))
                    break

    def query(self, value, radius):
        pending = [0] if self.nodes else []
        # Preserve the indexed hash even if traversal hits its visit cap.
        matches = {value}
        visits = 0
        while pending and visits < MAX_TREE_VISITS:
            existing, children = self.nodes[pending.pop()]
            visits += 1
            distance = (value ^ existing).bit_count()
            if distance <= radius:
                matches.add(existing)
            pending.extend(children[edge] for edge in sorted(children, reverse=True)
                           if distance - radius <= edge <= distance + radius)
        return sorted(matches)


def _features(records):
    features = []
    identities = set()
    for record in records:
        identity = record["id"]
        if not isinstance(identity, str) or not identity:
            raise ValueError("Duplicate candidate IDs must be nonempty strings")
        if identity in identities:
            raise ValueError("Duplicate candidate IDs must be unique")
        identities.add(identity)
        similarity = record["result"].get("similarity")
        if similarity is None:
            continue
        try:
            text_hash = similarity["perceptual_hash"]
            if not isinstance(text_hash, str) or len(text_hash) != 16 or any(c not in "0123456789abcdef" for c in text_hash):
                raise ValueError
            if similarity["hash_bits"] != 64:
                raise ValueError
            histogram = tuple(float(x) for x in similarity["color_histogram"])
            aspect = float(similarity["aspect_ratio"])
            information = float(similarity["luminance_stddev"])
            if (len(histogram) != 64 or any(not math.isfinite(x) or x < 0 for x in histogram)
                    or not math.isclose(sum(histogram), 1, abs_tol=1e-6)
                    or not math.isfinite(aspect) or aspect <= 0
                    or not math.isfinite(information) or not 0 <= information <= 1):
                raise ValueError
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            raise ValueError("Invalid image similarity features") from error
        # Uniform and nearly uniform images have uninformative pHash collisions.
        if information >= MIN_INFORMATION:
            features.append(_Feature(identity, int(text_hash, 16), histogram, aspect))
    return sorted(features, key=lambda item: item.identity)


def _decode_cursor(after, max_distance):
    if not after:
        return None
    try:
        payload = json.loads(base64.b64decode(after, altchars=b"-_", validate=True))
        if (not isinstance(payload, list) or len(payload) != 4 or payload[0] != "duplicates-1"
                or payload[1] != max_distance or not all(isinstance(x, str) and x for x in payload[2:])
                or payload[2] >= payload[3]):
            raise ValueError
        return tuple(payload[2:])
    except (ValueError, TypeError, UnicodeDecodeError) as error:
        raise ValueError("Invalid near-duplicate cursor or changed max_distance") from error


def _cursor(pair, max_distance):
    data = ["duplicates-1", max_distance, pair["left_id"], pair["right_id"]]
    return base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()


def find_near_duplicates(records, *, max_distance=6, limit=50, after=""):
    """Return one bounded candidate page; conservative limits can omit true pairs.

    Each image considers at most the first 512 lexically later IDs returned by a
    BK-tree query capped at 4096 visited hash nodes. That fixed candidate universe
    is independent of pagination. Equal hashes share one tree node and their ID
    lists are merged lazily: even enormous collision groups are never expanded
    into a cross product. Memory is linear in records plus the requested page.
    """
    if type(max_distance) is not int or not 0 <= max_distance <= 64:
        raise ValueError("max_distance must be an integer between 0 and 64")
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("limit must be an integer between 1 and 500")
    cursor = _decode_cursor(after, max_distance)
    features = _features(records)
    groups = {}
    for index, feature in enumerate(features):
        groups.setdefault(feature.hash, []).append(index)
    tree = _Tree(sorted(groups))

    @lru_cache(maxsize=128)
    def matching_hashes(value):
        return tree.query(value, max_distance)

    items = []
    ids = [feature.identity for feature in features]
    start = bisect_left(ids, cursor[0]) if cursor else 0
    for left_index in range(start, len(features)):
        left = features[left_index]
        pending = []
        for value in matching_hashes(left.hash):
            positions = groups[value]
            position = bisect_right(positions, left_index)
            if position < len(positions):
                pending.append((positions[position], value, position))
        heapq.heapify(pending)
        considered = 0
        while pending and considered < MAX_CANDIDATES:
            right_index, value, position = heapq.heappop(pending)
            considered += 1
            positions = groups[value]
            if position + 1 < len(positions):
                heapq.heappush(pending, (positions[position + 1], value, position + 1))
            right = features[right_index]
            if cursor and (left.identity, right.identity) <= cursor:
                continue
            aspect_difference = abs(left.aspect - right.aspect) / max(left.aspect, right.aspect)
            if aspect_difference > ASPECT_TOLERANCE:
                continue
            color_distance = sum(abs(a - b) for a, b in zip(left.histogram, right.histogram)) / 2
            if color_distance > COLOR_TOLERANCE:
                continue
            items.append({"left_id": left.identity, "right_id": right.identity,
                          "hamming_distance": (left.hash ^ right.hash).bit_count(),
                          "color_distance": color_distance,
                          "aspect_ratio_difference": aspect_difference})
            if len(items) > limit:
                return {"items": items[:limit], "next_cursor": _cursor(items[limit - 1], max_distance),
                        "search": _search_parameters(max_distance)}
    return {"items": items, "next_cursor": None, "search": _search_parameters(max_distance)}


def _search_parameters(max_distance):
    return {"method": "bk_tree_bounded", "max_distance": max_distance,
            "max_tree_visits_per_image": MAX_TREE_VISITS,
            "max_candidates_per_image": MAX_CANDIDATES,
            "max_aspect_ratio_difference": ASPECT_TOLERANCE,
            "max_color_distance": COLOR_TOLERANCE,
            "min_luminance_stddev": MIN_INFORMATION,
            "caveat": "Conservative technical candidates, not identity or relevance; bounded search may omit true pairs. Pagination requires unchanged records."}
