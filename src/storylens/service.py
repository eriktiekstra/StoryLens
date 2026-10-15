"""Shared application service, independent of CLI and editor transports."""

from pathlib import Path

from .config import Config
from .ingest import discover, fingerprint, kind, signature, snapshot
from .metadata import MetadataExtractor
from .storage import Catalog


class Analyzer:
    def __init__(self, config: Config):
        self.config = config
        self.catalog = Catalog(config.database)
        self.extractor = MetadataExtractor(config.ffprobe, config.timeout)
        self.artifact_root = config.database.resolve().with_suffix(".artifacts")
        self._image_extractor = None
        self._video_extractor = None
        self._embeddings = None
        self._vision = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.catalog.close()

    def _technical(self, media_kind):
        if media_kind == "image":
            if self._image_extractor is None:
                from .image_analysis import ImageTechnicalExtractor
                self._image_extractor = ImageTechnicalExtractor()
            return "image_technical", self._image_extractor
        if media_kind == "video":
            if self._video_extractor is None:
                from .video_analysis import VideoTechnicalExtractor
                self._video_extractor = VideoTechnicalExtractor(
                    ffmpeg=self.config.ffmpeg, ffprobe=self.config.ffprobe,
                    timeout=self.config.timeout, scene_threshold=self.config.scene_threshold,
                    minimum_scene_seconds=self.config.minimum_scene_seconds, max_scenes=self.config.max_scenes)
            return "video_technical", self._video_extractor
        return None

    @staticmethod
    def _artifacts_available(stage, result):
        if stage != "video_technical":
            return True
        return all(Path(scene["representative_frame"]["path"]).is_file()
                   for scene in result["scenes"])

    def scan(self, root: Path, *, analyze=False, force=False, stage="all", depth=None):
        if stage not in {"metadata", "technical", "semantic", "all"}:
            raise ValueError("stage must be metadata, technical, semantic or all")
        from .semantics import DEPTHS
        if depth is not None and depth not in DEPTHS:
            raise ValueError("depth must be fast, standard or deep")
        if depth is not None and stage not in {"semantic", "all"}:
            raise ValueError("depth applies only to semantic or all stages")
        if stage == "semantic" and depth is None:
            depth = "standard"
        root = root.expanduser().resolve(strict=True)
        summary = {"schema_version": "1.0", "root": str(root), "discovered": 0,
                   "indexed": 0, "analyzed": 0, "cache_hits": 0, "failed": 0,
                   "errors": [], "errors_truncated": False, "stages": {}}

        def failure(path, error, failed_stage):
            if len(summary["errors"]) < 100:
                summary["errors"].append({"path": str(path), "stage": failed_stage,
                                          "error": str(error)[:2000]})
            else:
                summary["errors_truncated"] = True

        for entry in discover(root, self.config.exclusions):
            if isinstance(entry, OSError):
                summary["failed"] += 1
                failure(getattr(entry, "filename", root), entry, "ingestion")
                continue
            if entry.is_relative_to(self.artifact_root):
                continue
            summary["discovered"] += 1
            try:
                before = snapshot(entry)
                known = self.catalog.known_path(entry)
                if self.config.trust_stat and known and known["size"] == before.st_size and known["mtime_ns"] == before.st_mtime_ns:
                    media_id = known["media_id"]
                else:
                    media_id = fingerprint(entry, before)
                self.catalog.observe(media_id, entry, kind(entry), before)
            except (OSError, ValueError) as error:
                summary["failed"] += 1
                failure(entry, error, "ingestion")
                continue
            summary["indexed"] += 1
            if not analyze:
                continue
            selected = []
            if stage in {"metadata", "all"}:
                selected.append(("metadata", self.extractor))
            if stage in {"technical", "all"} or (stage == "semantic" and kind(entry) == "video"):
                technical = self._technical(kind(entry))
                if technical is not None:
                    selected.append(technical)
            if depth is not None and kind(entry) in {"image", "video"}:
                selected.append(("semantic_embeddings", None))
                if depth != "fast":
                    selected.append(("semantic_vision", None))
            file_failed, computed, hits = False, False, 0
            failed_stages = set()
            for stage_name, extractor in selected:
                counts = summary["stages"].setdefault(stage_name, {"analyzed": 0, "cache_hits": 0, "failed": 0})
                try:
                    if stage_name.startswith("semantic_"):
                        from .semantics import SemanticExtractor
                        video, video_key = None, None
                        if kind(entry) == "video":
                            if "video_technical" in failed_stages:
                                raise ValueError("Current video technical stage failed; stale frames cannot support semantics")
                            _, technical = self._technical("video")
                            video_key = technical.cache_key()
                            video = self.catalog.stage_result(media_id, "video_technical", video_key)
                            if video is None or not self._artifacts_available("video_technical", video):
                                raise ValueError("Video semantics requires successful current video technical analysis")
                        task = stage_name.removeprefix("semantic_")
                        extractor = SemanticExtractor(self._semantic_provider(task), depth, task, video, video_key)
                    if signature(snapshot(entry)) != signature(before):
                        raise ValueError("Source changed before stage execution")
                    key = extractor.cache_key(kind(entry)) if stage_name == "metadata" else extractor.cache_key()
                    cached = self.catalog.stage_result(media_id, stage_name, key)
                    if not force and cached is not None and self._artifacts_available(stage_name, cached):
                        counts["cache_hits"] += 1
                        hits += 1
                        continue
                    if stage_name == "metadata":
                        result = extractor.extract(entry, kind(entry))
                    elif stage_name == "video_technical":
                        result = extractor.extract(entry, self.artifact_root / media_id[7:] / key)
                    else:
                        result = extractor.extract(entry)
                    if stage_name.startswith("semantic_"):
                        from .semantics import digest
                        if digest(result["provenance"]) != key:
                            raise RuntimeError("Semantic cache identity changed during extraction")
                    if signature(snapshot(entry)) != signature(before):
                        raise ValueError("Source changed during " + stage_name)
                except (OSError, ValueError, TypeError, SyntaxError, EOFError, RuntimeError, Warning) as error:
                    failed_stages.add(stage_name)
                    self.catalog.fail(media_id, stage_name, str(error)[:2000])
                    counts["failed"] += 1
                    file_failed = True
                    failure(entry, error, stage_name)
                    continue
                # Storage failures escape rather than being mistaken for bad media.
                self.catalog.save(media_id, stage_name, key, result)
                counts["analyzed"] += 1
                computed = True
            summary["failed"] += int(file_failed)
            summary["analyzed"] += int(computed)
            summary["cache_hits"] += int(bool(selected) and hits == len(selected))
        return summary

    def _semantic_provider(self, task):
        from .providers import LocalEmbeddings, OllamaVision
        if task == "embeddings":
            if self._embeddings is None:
                self._embeddings = LocalEmbeddings(self.config.embedding_model)
            return self._embeddings
        if self._vision is None:
            self._vision = OllamaVision(self.config.ollama_endpoint, self.config.vision_model, self.config.semantic_timeout)
        return self._vision

    def search(self, *, text=None, image=None, depth="standard", limit=20):
        from .semantics import DEPTHS, digest, search_results
        if (text is None) == (image is None):
            raise ValueError("Provide exactly one text or image query")
        if depth not in DEPTHS or type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("Invalid search depth or limit (1..500)")
        provider = self._semantic_provider("embeddings")
        identity = provider.identity()
        query = provider.text(text) if text is not None else provider.image(Path(image))
        if identity != provider.identity():
            raise RuntimeError("Embedding provider changed during query")
        space = digest(identity)
        items = search_results(self.catalog.semantic_results(), query, space=space, depth=depth, limit=limit)
        return {"schema_version": "1.0", "space": space, "depth": depth, "metric": "cosine",
                "items": items, "limit": limit, "ranking": "maximum selected-frame similarity per media; ties by media ID"}

    def duplicates(self, *, mode="exact", max_distance=6, limit=50, after=""):
        if mode == "exact":
            page = self.catalog.exact_duplicates(limit, after)
        elif mode == "near":
            from .duplicates import find_near_duplicates
            _, extractor = self._technical("image")
            page = find_near_duplicates(self.catalog.image_results(extractor.cache_key()),
                                        max_distance=max_distance, limit=limit, after=after)
        else:
            raise ValueError("duplicate mode must be exact or near")
        return {"schema_version": "1.0", "mode": mode, **page}

    def scenes(self, media_id, *, limit=50, after=""):
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        start = int(after) if after else 0
        if start < 0:
            raise ValueError("scene cursor must be nonnegative")
        stage = self.catalog.get(media_id)["analysis"].get("video_technical")
        if stage is None:
            raise ValueError("No video technical analysis; run analyze first")
        scenes = stage["result"]["scenes"]
        end = start + limit
        return {"schema_version": "1.0", "media_id": media_id, "items": scenes[start:end],
                "next_cursor": str(end) if end < len(scenes) else None,
                "provenance": stage["result"]["provenance"]}
