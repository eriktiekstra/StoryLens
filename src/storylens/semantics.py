"""Evidence-linked semantic stages and exact same-space cosine retrieval."""

import hashlib
import heapq
import json
import math
from pathlib import Path


ALGORITHM = "semantics-v1"
DEPTHS = ("fast", "standard", "deep")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def normalized(vector):
    if not isinstance(vector, (list, tuple)) or not vector or len(vector) > 65536:
        raise ValueError("Embedding must contain 1..65536 finite numbers")
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in vector):
        raise ValueError("Embedding contains nonfinite or nonnumeric values")
    norm = math.hypot(*vector)
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("Embedding has zero or nonfinite norm")
    return [x / norm for x in vector]


class SemanticExtractor:
    def __init__(self, provider, depth, task, video=None, video_key=None):
        if depth not in DEPTHS or task not in {"embeddings", "vision"}:
            raise ValueError("Invalid semantic depth or task")
        self.provider, self.depth, self.task = provider, depth, task
        self.video, self.video_key = video, video_key

    def provenance(self):
        identity = self.provider.identity()
        return {"algorithm": ALGORITHM, "provider": identity, "space": digest(identity),
                "depth": self.depth, "task": self.task, "video_cache_key": self.video_key,
                "selection": "uniform scene representatives: fast=1, standard<=8, deep=all; still image=1"}

    def cache_key(self):
        return digest(self.provenance())

    def extract(self, path):
        provenance = self.provenance()
        if self.video is None:
            frames = [(Path(path), {"kind": "image"})]
        else:
            scenes = self.video["scenes"]
            if not scenes:
                raise ValueError("Video has no representative frames")
            count = min(len(scenes), {"fast": 1, "standard": 8, "deep": len(scenes)}[self.depth])
            indices = [len(scenes) // 2] if count == 1 else [i * (len(scenes) - 1) // (count - 1) for i in range(count)]
            frames = []
            for index in indices:
                scene = scenes[index]
                frame = scene["representative_frame"]
                frames.append((Path(frame["path"]), {"kind": "video_frame", "scene_index": scene["index"],
                    "start_seconds": scene["start_seconds"], "end_seconds": scene["end_seconds"],
                    "representative_frame": frame}))
        items = []
        for frame, evidence in frames:
            if not frame.is_file() or frame.is_symlink():
                raise ValueError("Selected evidence frame is unavailable")
            if self.task == "embeddings":
                items.append({"evidence": evidence, "vector": normalized(self.provider.image(frame))})
            else:
                caption = self.provider.describe(frame)
                if not isinstance(caption, str) or not caption.strip():
                    raise ValueError("Vision model returned an empty description")
                items.append({"evidence": evidence, "description": caption, "interpretation": "model_hypothesis",
                              "confidence": None})
        if self.provenance() != provenance:
            raise RuntimeError("Semantic provider changed during extraction")
        return {"schema_version": "1.0", "provenance": provenance, "items": items,
                "coverage": {"selected_frames": len(items), "available_frames": len(self.video["scenes"]) if self.video else 1},
                "caveats": ["Similarity is not a factual observation or a calibrated probability; captions may hallucinate.",
                            "Only selected still frames are analyzed; no motion, speech, identity or event inference.",
                            "Video frames inherit M2 8-bit conversion without HDR tone mapping."]}


def search_results(rows, query, *, space, depth, limit):
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    if depth not in DEPTHS:
        raise ValueError("Invalid semantic depth")
    query = normalized(query)
    seen = set()

    def candidates():
        for row in rows:
            result = row["result"]
            provenance = result["provenance"]
            if (provenance.get("space") != space or provenance.get("depth") != depth
                    or provenance.get("algorithm") != ALGORITHM or row["id"] in seen):
                continue
            seen.add(row["id"])
            best = None
            for item in result["items"]:
                vector = normalized(item["vector"])
                if len(vector) != len(query):
                    raise ValueError("Stored embedding dimension does not match query in the same space")
                score = max(-1.0, min(1.0, math.fsum(a * b for a, b in zip(query, vector))))
                if best is None or score > best["score"]:
                    best = {"media_id": row["id"], "score": score, "evidence": item["evidence"]}
            if best is not None:
                yield best

    return heapq.nsmallest(limit, candidates(), key=lambda item: (-item["score"], item["media_id"]))
