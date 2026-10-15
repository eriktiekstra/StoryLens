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

    def scan(self, root: Path, *, analyze=False, force=False, stage="all"):
        if stage not in {"metadata", "technical", "all"}:
            raise ValueError("stage must be metadata, technical or all")
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
            if stage in {"technical", "all"}:
                technical = self._technical(kind(entry))
                if technical is not None:
                    selected.append(technical)
            file_failed, computed, hits = False, False, 0
            for stage_name, extractor in selected:
                counts = summary["stages"].setdefault(stage_name, {"analyzed": 0, "cache_hits": 0, "failed": 0})
                try:
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
                    if signature(snapshot(entry)) != signature(before):
                        raise ValueError("Source changed during " + stage_name)
                except (OSError, ValueError, TypeError, SyntaxError, EOFError, RuntimeError, Warning) as error:
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
