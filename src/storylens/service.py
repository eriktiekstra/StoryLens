"""Shared application service, independent of CLI and editor transports."""

import base64
import hashlib
import json
from pathlib import Path

from .audio_analysis import AudioTechnicalExtractor
from .audio_providers import AudioModelExtractor
from .config import Config
from .ingest import discover, fingerprint, kind, signature, snapshot
from .metadata import MetadataExtractor
from .storage import Catalog
from .transcripts import load_transcript, serialize


class Analyzer:
    def __init__(self, config: Config):
        self.config = config
        self.catalog = Catalog(config.database)
        self.extractor = MetadataExtractor(config.ffprobe, config.timeout)
        self.artifact_root = config.database.resolve().with_suffix(".artifacts")
        self._audio_extractor = None
        self._image_extractor = None
        self._video_extractor = None
        self._embeddings = None
        self._vision = None
        self._feedback = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        try:
            if self._feedback is not None:
                self._feedback.close()
        finally:
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
        if media_kind == "audio":
            if self._audio_extractor is None:
                self._audio_extractor = AudioTechnicalExtractor(
                    ffmpeg=self.config.ffmpeg, ffprobe=self.config.ffprobe,
                    timeout=self.config.timeout, max_audio_seconds=self.config.max_audio_seconds)
            return "audio_technical", self._audio_extractor
        return None

    @staticmethod
    def _artifacts_available(stage, result):
        if stage != "video_technical":
            return True
        return all(Path(scene["representative_frame"]["path"]).is_file()
                   for scene in result["scenes"])

    def scan(self, root: Path, *, analyze=False, force=False, stage="all", depth=None, checkpoint=None):
        if stage not in {"metadata", "technical", "semantic", "transcript", "all"}:
            raise ValueError("stage must be metadata, technical, semantic, transcript or all")
        if stage == "transcript" and not self.config.audio_transcription_model:
            raise ValueError("audio_transcription_model is required for transcript stage")
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

        if checkpoint is not None:
            checkpoint(summary)
        for entry in discover(root, self.config.exclusions):
            if checkpoint is not None:
                checkpoint(summary)
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
                if checkpoint is not None:
                    checkpoint(summary)
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
            if stage in {"technical", "all"} or (stage == "semantic" and kind(entry) in {"video", "audio"}):
                technical = self._technical(kind(entry))
                if technical is not None:
                    selected.append(technical)
            if depth is not None and kind(entry) in {"image", "video"}:
                selected.append(("semantic_embeddings", None))
                if depth != "fast":
                    selected.append(("semantic_vision", None))
            if kind(entry) == "audio" and self.config.audio_transcription_model and stage in {"transcript", "all"}:
                selected.append(("audio_transcription", None))
            file_failed, computed, hits = False, False, 0
            failed_stages = set()
            for stage_name, extractor in selected:
                if checkpoint is not None:
                    checkpoint(summary)
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
                    if stage_name == "audio_transcription":
                        extractor = AudioModelExtractor(
                            "transcription", self.config.audio_transcription_model,
                            ffmpeg=self.config.ffmpeg, ffprobe=self.config.ffprobe,
                            timeout=self.config.timeout,
                            max_audio_seconds=self.config.max_audio_seconds,
                            device=self.config.audio_transcription_device,
                            language=self.config.audio_transcription_language)
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
                    if checkpoint is not None:
                        checkpoint(summary)
                    self.catalog.fail(media_id, stage_name, str(error)[:2000])
                    counts["failed"] += 1
                    file_failed = True
                    failure(entry, error, stage_name)
                    continue
                # Storage failures escape rather than being mistaken for bad media.
                if checkpoint is not None:
                    checkpoint(summary)
                self.catalog.save(media_id, stage_name, key, result)
                counts["analyzed"] += 1
                computed = True
                if checkpoint is not None:
                    checkpoint(summary)
            summary["failed"] += int(file_failed)
            summary["analyzed"] += int(computed)
            summary["cache_hits"] += int(bool(selected) and hits == len(selected))
        if checkpoint is not None:
            checkpoint(summary)
        return summary

    @staticmethod
    def _transcript_cache_key(result):
        provenance = result["provenance"]
        return hashlib.sha256(json.dumps({
            "sha256": provenance["source_sha256"],
            "format": provenance["format"],
            "offset": provenance["offset_seconds"],
            "language": result.get("language")}, sort_keys=True).encode()).hexdigest()

    def import_transcript(self, media_id, path, format=None, offset=0, language=None):
        # Ensure the media identity exists before attaching imported evidence.
        self.catalog.get(media_id)
        result = load_transcript(Path(path), format=format, offset=offset, language=language)
        key = self._transcript_cache_key(result)
        self.catalog.save(media_id, "transcript", key, result)
        self.catalog.save_transcript(media_id, result)
        return result

    def _user_state(self):
        if self._feedback is None:
            from .feedback import FeedbackStore
            self._feedback = FeedbackStore(self.config.database)
        return self._feedback

    def feedback(self, media_id):
        self.catalog.get(media_id)
        return self._user_state().get(media_id)

    def set_feedback(self, media_id, patch):
        media = self.catalog.get(media_id)
        return self._user_state().update(media_id, patch, media)

    def reset_feedback(self, media_id, fields=None):
        self.catalog.get(media_id)
        return self._user_state().reset(media_id, fields)

    def preferences(self):
        return self._user_state().preferences()

    def set_preferences(self, patch):
        return self._user_state().set_preferences(patch)

    def create_event(self, title):
        return self._user_state().create_event(title)

    def rename_event(self, event_id, title):
        return self._user_state().rename_event(event_id, title)

    def _event_snapshot(self):
        from .events import group_media
        state = self._user_state().snapshot()

        def inputs():
            after, count = "", 0
            while True:
                page = self.catalog.page(100, after)
                for row in page["items"]:
                    media = self.catalog.get(row["id"])
                    if not any(Path(p["path"]).is_file() and not Path(p["path"]).is_symlink()
                               for p in media["paths"]):
                        continue
                    count += 1
                    if count > 10000:
                        raise ValueError("Event grouping supports at most 10000 referenced media per catalog")
                    media["feedback"] = state["media"].get(media["id"], {})
                    yield media
                after = page["next_cursor"]
                if after is None:
                    break

        result = group_media(inputs(), settings=state["preferences"], manual_events=state["events"])
        snapshot = hashlib.sha256(json.dumps({"evidence": result["provenance"], "feedback": state},
                                 sort_keys=True, allow_nan=False).encode()).hexdigest()
        return result, snapshot

    @staticmethod
    def _event_offset(after, snapshot, scope, limit):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if not after:
            return 0
        try:
            if not isinstance(after, str) or len(after) > 512:
                raise ValueError()
            token = json.loads(base64.b64decode(after, altchars=b"-_", validate=True))
            if not isinstance(token, list) or len(token) != 3:
                raise ValueError()
            if token[0] != snapshot or token[1] != scope:
                raise ValueError("Event cursor is stale or belongs to another query; restart pagination")
            if type(token[2]) is not int or not 0 <= token[2] <= 10000:
                raise ValueError()
            return token[2]
        except (ValueError, TypeError, UnicodeError) as error:
            raise ValueError("Invalid or stale event cursor; restart pagination") from error

    @staticmethod
    def _event_cursor(snapshot, scope, offset):
        return base64.urlsafe_b64encode(json.dumps([snapshot, scope, offset]).encode()).decode()

    def events(self, *, limit=50, after=""):
        result, snapshot = self._event_snapshot()
        start = self._event_offset(after, snapshot, "events", limit)
        end = start + limit
        items = [{key: value for key, value in event.items() if key != "members"}
                 for event in result["events"][start:end]]
        return {"schema_version": "1.0", "items": items, "snapshot": snapshot,
                "next_cursor": self._event_cursor(snapshot, "events", end) if end < len(result["events"]) else None,
                "provenance": result["provenance"]}

    def event(self, event_id, *, limit=50, after=""):
        result, snapshot = self._event_snapshot()
        start = self._event_offset(after, snapshot, event_id, limit)
        event = next((item for item in result["events"] if item["id"] == event_id), None)
        if event is None:
            raise ValueError("Unknown event ID; refresh event candidates")
        end = start + limit
        return {"schema_version": "1.0", "event": {key: value for key, value in event.items() if key != "members"},
                "items": event["members"][start:end], "snapshot": snapshot,
                "next_cursor": self._event_cursor(snapshot, event_id, end) if end < len(event["members"]) else None,
                "provenance": result["provenance"]}

    def _storyboard_media(self):
        from .events import group_media
        state = self._user_state().snapshot()
        items, after = [], ""
        while True:
            page = self.catalog.page(100, after)
            for row in page["items"]:
                media = self.catalog.get(row["id"])
                media["feedback"] = state["media"].get(media["id"], {})
                if not any(Path(p["path"]).is_file() and not Path(p["path"]).is_symlink()
                           for p in media["paths"]):
                    values = media["feedback"]
                    if values.get("selection") == "include" or any(
                            segment["selection"] == "include" for segment in values.get("segments", [])):
                        raise ValueError("Required media has no current source reference; rescan or clear its selection")
                    continue
                if len(items) >= 10000:
                    raise ValueError("Recommendations support at most 10000 referenced media")
                media["transcript"] = self.catalog.transcript(media["id"])
                items.append(media)
            after = page["next_cursor"]
            if after is None:
                break
        groups = group_media(items, settings=state["preferences"], manual_events=state["events"])
        membership = {member["media_id"]: event["id"] for event in groups["events"]
                      for member in event["members"] if event["kind"] != "undated"}
        for media in items:
            media["event_id"] = membership.get(media["id"])
        return items

    def recommend(self, *, duration_seconds=60, strategy="balanced", weights=None,
                  required=None, excluded=None, image_seconds=4, clip_seconds=10,
                  text=None, depth="standard"):
        from .recommendations import recommend
        from .storyboard import validate_storyboard
        items = self._storyboard_media()
        relevance = None
        if text is not None:
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Recommendation text must be nonempty")
            matches = self.search(text=text, depth=depth, limit=500)
            relevance = {item["media_id"]: item["score"] for item in matches["items"]}
        document = recommend(items, duration_seconds=duration_seconds, strategy=strategy,
                             weights=weights, required=required, excluded=excluded,
                             relevance=relevance, image_seconds=image_seconds, clip_seconds=clip_seconds)
        if text is not None and len(items) > 500:
            document["warnings"].append("Query relevance is available only for the top 500 same-space search matches.")
        return validate_storyboard(document, {item["id"]: item for item in items})

    def validate_storyboard(self, document):
        from .storyboard import validate_storyboard
        items = self._storyboard_media()
        return validate_storyboard(document, {item["id"]: item for item in items})

    def show(self, media_id):
        return {"schema_version": "1.0", "media": self.catalog.get(media_id)}

    def transcript(self, media_id, *, format="json"):
        stored = self.catalog.transcript(media_id)
        if stored is None:
            raise ValueError("No transcript imported for media ID: " + media_id)
        if format == "json":
            return stored
        return serialize(stored, format)

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
