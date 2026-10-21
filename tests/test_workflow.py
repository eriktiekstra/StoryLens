"""Real generated media exercise project isolation, recovery and storyboard state."""

from concurrent.futures import CancelledError
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

from PIL import Image

from storylens.config import Config
from storylens.ingest import fingerprint, snapshot
from storylens.service import Analyzer
from storylens.workflow import MAX_JSON_BYTES, Project, preflight


class FixtureModels:
    """Deterministic providers exercise real evidence and cache orchestration."""
    def __init__(self, task):
        self.task = task
        self.fail = False
        self.calls = 0
        self.cancel = None

    def identity(self):
        return {"provider": "fixture", "task": self.task, "revision": "one"}

    def pixels(self, path):
        self.calls += 1
        if self.fail:
            raise RuntimeError("Fixture model unavailable")
        with Image.open(path) as image:
            pixel = image.convert("RGB").getpixel((0, 0))
        if self.cancel is not None:
            self.cancel()
        return pixel

    def image(self, path):
        return [value + 1 for value in self.pixels(path)]

    def describe(self, path):
        return "Fixture pixel RGB " + str(self.pixels(path))


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "media"
        self.source.mkdir()
        self.photo = self.source / "first.png"
        self.other = self.source / "second.png"
        Image.new("RGB", (48, 32), (20, 40, 100)).save(self.photo)
        Image.new("RGB", (48, 32), (120, 80, 20)).save(self.other)
        self.config = Config(self.root / "ambient.sqlite3", ffmpeg="storylens-missing-ffmpeg",
                             ffprobe="storylens-missing-ffprobe")
        self.project = Project.create(self.root / "project", self.source, self.config)

    def manifest(self):
        return json.loads((self.project.directory / "project.json").read_text())

    def write_manifest(self, document):
        (self.project.directory / "project.json").write_text(json.dumps(document))

    def analyzed(self):
        self.project.analyze()
        return self.project.recommend(duration_seconds=8)

    def model(self):
        directory = self.root / "fixture-model"
        directory.mkdir(exist_ok=True)
        (directory / "config.json").write_text("{}")
        return str(directory)

    def test_add_vision_later_without_embeddings_preserves_basic_evidence(self):
        self.project.analyze(operation="technical")
        basic = self.project.analysis_status("technical")
        before = {item["id"]: item["analysis"] for item in self.project.list_media()}
        self.project.configure(vision_model="fixture-local")
        self.assertEqual(self.project.mode, "local-ai")
        self.assertEqual(self.project.depth, "standard")
        self.assertFalse(self.project.config.embedding_model)
        self.assertEqual(self.project.analysis_status("technical"), basic)
        self.assertEqual(self.project.analysis_status("ai")["status"], "not_started")
        vision = FixtureModels("vision")
        with patch.object(Analyzer, "_semantic_provider", side_effect=lambda task: vision):
            summary = self.project.analyze(operation="ai")
        self.assertEqual(summary["failed"], 0, summary)
        self.assertNotIn("semantic_embeddings", summary["stages"])
        self.assertEqual(summary["stages"]["semantic_vision"]["analyzed"], 2)
        self.assertEqual(summary["stages"]["metadata"]["cache_hits"], 2)
        self.assertEqual(summary["stages"]["image_technical"]["cache_hits"], 2)
        for item in self.project.list_media():
            for stage in ("metadata", "image_technical"):
                self.assertEqual(item["analysis"][stage], before[item["id"]][stage])
            self.assertEqual(item["analysis"]["semantic_vision"]["result"]["items"][0]["evidence"], {"kind": "image"})
        reopened = Project.open(self.project.directory)
        self.assertEqual(reopened.analysis_status("technical"), basic)
        self.assertEqual(reopened.analysis_status("ai")["status"], "completed")
        self.assertEqual(reopened.analysis_status("ai")["summary"], summary)

    def test_model_failures_are_independent_retry_only_failed_stages(self):
        self.project.analyze(operation="technical")
        basic = self.project.analysis_status("technical")
        self.project.configure(embedding_model=self.model(), vision_model="fixture-local")
        embeddings, vision = FixtureModels("embeddings"), FixtureModels("vision")
        embeddings.fail = True
        providers = {"embeddings": embeddings, "vision": vision}
        with patch.object(Analyzer, "_semantic_provider", side_effect=providers.__getitem__):
            failed = self.project.analyze(operation="ai")
        self.assertEqual(failed["stages"]["semantic_embeddings"]["failed"], 2)
        self.assertEqual(failed["stages"]["semantic_vision"]["analyzed"], 2)
        self.assertEqual(self.project.analysis_status("technical"), basic)
        reopened = Project.open(self.project.directory)
        self.assertEqual(reopened.analysis_status("ai")["status"], "partial")
        self.assertEqual(reopened.analysis_status("technical")["status"], "completed")
        embeddings.fail = False
        with patch.object(Analyzer, "_semantic_provider", side_effect=providers.__getitem__):
            repaired = reopened.analyze(operation="ai")
        self.assertEqual(repaired["failed"], 0, repaired)
        self.assertEqual(repaired["stages"]["semantic_embeddings"]["analyzed"], 2)
        self.assertEqual(repaired["stages"]["semantic_vision"]["cache_hits"], 2)
        self.assertEqual(vision.calls, 2)
        for item in reopened.list_media():
            self.assertEqual(item["failures"], [])

    def test_technical_operation_never_constructs_configured_models(self):
        self.project.configure(embedding_model=self.model(), vision_model="fixture-local",
                               audio_transcription_model=self.model())
        with patch.object(Analyzer, "_semantic_provider", side_effect=AssertionError("Models must remain off")), \
                patch("storylens.service.AudioModelExtractor", side_effect=AssertionError("Speech must remain off")):
            summary = self.project.analyze(operation="technical")
        self.assertEqual(set(summary["stages"]), {"metadata", "image_technical"})
        self.assertEqual(self.project.analysis_status("technical")["status"], "completed")
        self.assertEqual(self.project.analysis_status("ai")["status"], "not_started")
        self.assertTrue(Project.open(self.project.directory).config.vision_model)

    def test_configuration_validation_and_failed_save_roll_back_atomically(self):
        self.project.analyze(operation="technical")
        original_config, original_state = self.project.config, self.project.state
        original_bytes = (self.project.directory / "project.json").read_bytes()
        invalid = ({"timeout": 0}, {"semantic_timeout": float("nan")}, {"database": "/foreign"},
                   {"mode": "remote"}, {"vision_model": "fixture", "depth": "fast"},
                   {"ollama_endpoint": "https://user:secret@127.0.0.1/?token=secret"},
                   {"audio_transcription_device": "automatic"}, {"embedding_model": []})
        for settings in invalid:
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                self.project.configure(**settings)
            self.assertEqual(self.project.config, original_config)
            self.assertEqual(self.project.state, original_state)
            self.assertEqual((self.project.directory / "project.json").read_bytes(), original_bytes)
        with patch("storylens.workflow.os.replace", side_effect=OSError("No space")), self.assertRaises(OSError):
            self.project.configure(timeout=900, vision_model="fixture")
        self.assertEqual(self.project.config, original_config)
        self.assertEqual(self.project.state, original_state)
        self.assertEqual((self.project.directory / "project.json").read_bytes(), original_bytes)
        stale = Project.open(self.project.directory)
        self.project.configure(timeout=900)
        with self.assertRaises(ValueError):
            stale.configure(timeout=1200)
        self.assertEqual(stale.config, original_config)
        self.assertEqual(Project.open(self.project.directory).config.timeout, 900)

    def test_cancellation_saves_extracted_ai_stage_before_stopping(self):
        self.project.analyze(operation="technical")
        basic = self.project.analysis_status("technical")
        self.project.configure(vision_model="fixture")
        vision = FixtureModels("vision")
        stopped = False

        def stop():
            nonlocal stopped
            stopped = True

        vision.cancel = stop
        with patch.object(Analyzer, "_semantic_provider", return_value=vision), self.assertRaises(CancelledError):
            self.project.analyze(operation="ai", cancelled=lambda: stopped)
        reopened = Project.open(self.project.directory)
        self.assertEqual(reopened.analysis_status("technical"), basic)
        self.assertEqual(reopened.analysis_status("ai")["status"], "interrupted")
        self.assertEqual(reopened.analysis_status("ai")["summary"]["stages"]["semantic_vision"]["analyzed"], 1)
        stopped, vision.cancel = False, None
        with patch.object(Analyzer, "_semantic_provider", return_value=vision):
            resumed = reopened.analyze(operation="ai", cancelled=lambda: stopped)
        self.assertEqual(resumed["stages"]["semantic_vision"]["cache_hits"], 1)
        self.assertEqual(resumed["stages"]["semantic_vision"]["analyzed"], 1)
        self.assertEqual(vision.calls, 2)
        self.assertEqual(resumed["failed"], 0)

    def test_cancel_before_start_does_not_touch_media_and_recovery_remains_possible(self):
        with patch("storylens.service.fingerprint", side_effect=AssertionError("No media read")), self.assertRaises(CancelledError):
            self.project.analyze(operation="technical", cancelled=lambda: True)
        reopened = Project.open(self.project.directory)
        self.assertEqual(reopened.analysis_status("technical")["status"], "interrupted")
        self.assertEqual(reopened.analyze(operation="technical")["failed"], 0)

    def test_v2_basic_status_migration_preserves_catalog_and_summary(self):
        summary = self.project.analyze(operation="technical")
        manifest = self.manifest()
        manifest["schema_version"] = "2.0"
        manifest["state"] = {key: manifest["state"][key] for key in ("analysis", "storyboard")}
        self.write_manifest(manifest)
        reopened = Project.open(self.project.directory)
        self.assertEqual(reopened.analysis_status("technical")["summary"], summary)
        self.assertEqual(reopened.analysis_status("technical")["status"], "completed")
        self.assertEqual(reopened.analysis_status("ai")["status"], "not_started")
        self.assertEqual(reopened.analyze(operation="technical")["cache_hits"], 2)
        self.assertEqual(self.manifest()["schema_version"], "3.0")

    def test_v2_model_failure_does_not_erase_successful_basic_migration(self):
        self.project.configure(embedding_model=self.model(), vision_model="fixture")
        embeddings, vision = FixtureModels("embeddings"), FixtureModels("vision")
        embeddings.fail = True
        with patch.object(Analyzer, "_semantic_provider", side_effect={"embeddings": embeddings, "vision": vision}.__getitem__):
            summary = self.project.analyze()
        manifest = self.manifest()
        manifest["schema_version"] = "2.0"
        manifest["state"] = {key: manifest["state"][key] for key in ("analysis", "storyboard")}
        self.write_manifest(manifest)
        reopened = Project.open(self.project.directory)
        self.assertEqual(reopened.analysis_status("ai")["status"], "partial")
        self.assertEqual(reopened.analysis_status("ai")["summary"], summary)
        self.assertEqual(reopened.analysis_status("technical")["status"], "completed")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_speech_only_enrichment_reuses_basic_audio_without_embeddings(self):
        audio = self.source / "silence.wav"
        with wave.open(str(audio), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(16000)
            stream.writeframes(b"\0\0" * 16000)
        self.project.configure(ffmpeg="ffmpeg", ffprobe="ffprobe", audio_transcription_model=self.model())
        self.project.analyze(operation="technical")
        basic = self.project.analysis_status("technical")
        decoded = []

        def silence_worker(path):
            with wave.open(str(path)) as stream:
                self.assertEqual(stream.getframerate(), 16000)
                self.assertEqual(set(stream.readframes(stream.getnframes())), {0})
                decoded.append(stream.getnframes())
            return {"language": "", "language_probability": None, "segments": [],
                    "speech_activity": {"enabled": True, "method": "Silero VAD", "intervals": []}}

        with patch("storylens.audio_providers._libraries", return_value={"fixture": "one"}), \
                patch("storylens.audio_providers.AudioModelExtractor._run_worker", side_effect=silence_worker):
            enriched = self.project.analyze(operation="ai")
            cached = self.project.analyze(operation="ai")
        self.assertEqual(enriched["failed"], 0, enriched)
        self.assertEqual(enriched["stages"]["audio_transcription"]["analyzed"], 1)
        self.assertEqual(cached["stages"]["audio_transcription"]["cache_hits"], 1)
        self.assertNotIn("semantic_embeddings", enriched["stages"])
        self.assertNotIn("semantic_vision", enriched["stages"])
        self.assertEqual(decoded, [16000])
        self.assertEqual(self.project.analysis_status("technical"), basic)

    def video(self, name, *, audio=True, silent_audio=False, offset=0):
        path = self.source / name
        command = ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                   "color=blue:s=80x48:r=10:d=1"]
        if audio:
            signal = "anullsrc=r=16000:cl=mono:d=1" if silent_audio else "sine=frequency=440:sample_rate=16000:duration=1"
            command += ["-f", "lavfi", "-i", signal,
                        "-map", "0:v:0", "-map", "1:a:0", "-c:a", "pcm_s16le"]
        command += ["-c:v", "mpeg4", "-output_ts_offset", str(offset), str(path)]
        subprocess.run(command, capture_output=True, check=True)
        return path

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_video_speech_and_no_audio_skip_preserve_source_timestamps_and_caches(self):
        video = self.video("with-audio.mkv", offset=5)
        silent = self.video("without-audio.mkv", audio=False, offset=5)
        self.project.configure(ffmpeg="ffmpeg", ffprobe="ffprobe", audio_transcription_model=self.model())
        with patch("storylens.service.AudioModelExtractor", side_effect=AssertionError("Basic mode must not load speech")):
            basic_summary = self.project.analyze(operation="technical")
        self.assertEqual(basic_summary["failed"], 0, basic_summary)
        basic = self.project.analysis_status("technical")
        before = {item["id"]: item["analysis"] for item in self.project.list_media()}
        decoded = []

        def fixture_worker(path):
            with wave.open(str(path)) as stream:
                decoded.append(stream.getnframes())
                self.assertEqual(stream.getframerate(), 16000)
            return {"language": "en", "language_probability": 0.9,
                    "segments": [{"start_seconds": 0.1, "end_seconds": 0.5, "text": "Fixture hypothesis",
                                  "words": [{"start_seconds": 0.1, "end_seconds": 0.4, "text": "Fixture",
                                             "probability": 0.9}]}],
                    "speech_activity": {"enabled": True, "method": "Silero VAD",
                                        "intervals": [{"start_seconds": 0.1, "end_seconds": 0.5}]}}

        with patch("storylens.audio_providers._libraries", return_value={"fixture": "one"}), \
                patch("storylens.audio_providers.AudioModelExtractor._run_worker", side_effect=fixture_worker):
            enriched = self.project.analyze(operation="ai")
        self.assertEqual(enriched["failed"], 0, enriched)
        self.assertEqual(enriched["stages"]["audio_transcription"]["analyzed"], 1)
        self.assertNotIn("semantic_embeddings", enriched["stages"])
        self.assertNotIn("semantic_vision", enriched["stages"])
        self.assertEqual(decoded, [16000])
        with patch("storylens.audio_providers._libraries", return_value={"fixture": "one"}), \
                patch("storylens.audio_providers.AudioModelExtractor._run_worker", side_effect=AssertionError("Reuse transcript")):
            for stage in ("all", "transcript"):
                with self.subTest(stage=stage), Analyzer(self.project.config) as app:
                    cached = app.scan(self.source, analyze=True, stage=stage)
                    self.assertEqual(cached["failed"], 0, cached)
                    self.assertEqual(cached["stages"]["audio_transcription"]["cache_hits"], 1)
        by_path = {item["paths"][0]["path"]: item for item in self.project.list_media()}
        with_audio, no_audio = by_path[str(video)], by_path[str(silent)]
        result = with_audio["analysis"]["audio_transcription"]["result"]
        timing = result["source_timing"]
        self.assertAlmostEqual(timing["origin_seconds"], 5, places=3)
        segment = result["segments"][0]
        self.assertAlmostEqual(segment["start_seconds"], timing["origin_seconds"] + 0.1)
        self.assertAlmostEqual(segment["end_seconds"], timing["origin_seconds"] + 0.5)
        self.assertAlmostEqual(segment["words"][0]["start_seconds"], timing["origin_seconds"] + 0.1)
        self.assertLessEqual(segment["end_seconds"], timing["end_seconds"])
        self.assertEqual(no_audio["analysis"]["video_technical"]["result"]["measurements"]["audio"]["status"], "no_audio")
        self.assertNotIn("audio_transcription", no_audio["analysis"])
        for item in (with_audio, no_audio):
            self.assertEqual(item["analysis"]["video_technical"], before[item["id"]]["video_technical"])
        self.assertEqual(self.project.analysis_status("technical"), basic)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_no_audio_video_skips_unavailable_speech_model(self):
        self.video("silent.mkv", audio=False)
        self.video("silent-track.mkv", silent_audio=True)
        self.project.configure(ffmpeg="ffmpeg", ffprobe="ffprobe", audio_transcription_model="/missing/speech-model")
        with patch("storylens.service.AudioModelExtractor", side_effect=AssertionError("No model needed without audio")):
            summary = self.project.analyze(operation="ai")
        self.assertEqual(summary["failed"], 0, summary)
        self.assertEqual(summary["stages"]["audio_transcription"], {"analyzed": 0, "cache_hits": 0, "failed": 0})
        self.assertEqual(self.project.analysis_status("technical")["status"], "completed")
        self.assertEqual(self.project.analysis_status("ai")["status"], "completed")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_failed_current_video_dependency_never_uses_cached_audio_presence(self):
        self.video("with-audio.mkv")
        self.project.configure(ffmpeg="ffmpeg", ffprobe="ffprobe", audio_transcription_model=self.model())
        self.project.analyze(operation="technical")
        with Analyzer(self.project.config) as app:
            _, extractor = app._technical("video")
            with patch.object(extractor, "extract", side_effect=ValueError("Current decode failed")), \
                    patch("storylens.service.AudioModelExtractor", side_effect=AssertionError("No stale track guessing")):
                failed = app.scan(self.source, analyze=True, stage="transcript", force=True)
        self.assertEqual(failed["stages"]["video_technical"]["failed"], 1)
        self.assertEqual(failed["stages"]["audio_transcription"]["failed"], 1)
        self.assertEqual(failed["failed"], 1)

    def test_ai_scan_refreshes_changed_basic_status_without_a_second_scan(self):
        self.project.analyze(operation="technical")
        self.project.configure(vision_model="fixture")
        Image.new("RGB", (48, 32), "green").save(self.source / "new.png")
        self.other.write_bytes(b"broken image")
        vision = FixtureModels("vision")
        scan = Analyzer.scan
        with patch.object(Analyzer, "_semantic_provider", return_value=vision), \
                patch.object(Analyzer, "scan", autospec=True, side_effect=scan) as scans:
            summary = self.project.analyze(operation="ai")
        self.assertEqual(scans.call_count, 1)
        self.assertEqual(summary["failed"], 1)
        basic = self.project.analysis_status("technical")
        self.assertEqual(basic["status"], "partial")
        self.assertEqual(basic["summary"]["discovered"], 3)
        self.assertEqual(basic["summary"]["indexed"], 3)
        self.assertEqual(basic["summary"]["failed"], 1)
        self.assertEqual(basic["summary"]["analyzed"], 1)
        self.assertEqual(basic["summary"]["cache_hits"], 1)
        self.assertEqual(basic["summary"]["stages"]["image_technical"]["analyzed"], 1)
        self.assertTrue(basic["summary"]["errors"])
        self.assertTrue(all(error["stage"] in {"metadata", "image_technical"} for error in basic["summary"]["errors"]))
        reopened = Project.open(self.project.directory)
        self.assertEqual(reopened.analysis_status("technical"), basic)
        self.assertEqual(reopened.analysis_status("ai")["status"], "partial")

    def test_diagnostics_are_private_local_and_do_not_mutate_analysis(self):
        self.project.analyze(operation="technical")
        state = self.project.state
        exported = self.project.diagnostics()
        path = Path(exported["path"])
        self.assertEqual(path.parent, self.project.directory)
        self.assertEqual(json.loads(path.read_text()), exported["report"])
        self.assertEqual(exported["report"]["technical"], state["technical"])
        self.assertEqual(self.project.state, state)
        if os.name == "posix":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_default_technical_mode_strips_ambient_inference_and_credentials(self):
        ambient = Config(self.root / "secret.sqlite3", embedding_model="/missing/model",
                         vision_model="ambient", audio_transcription_model="/missing/whisper",
                         ollama_endpoint="https://user:password@example.invalid/?token=secret")
        project = Project.create(self.root / "safe", self.source, ambient)
        self.assertEqual(project.mode, "technical")
        self.assertFalse(project.config.embedding_model)
        self.assertFalse(project.config.vision_model)
        self.assertFalse(project.config.audio_transcription_model)
        self.assertEqual(project.config.ollama_endpoint, "http://127.0.0.1:11434")
        manifest = (project.directory / "project.json").read_text()
        self.assertNotIn("password", manifest)
        self.assertNotIn("token=", manifest)
        self.assertFalse(ambient.database.exists())
        self.assertEqual(project.analyze()["failed"], 0)

    def test_preflight_counts_scope_and_requires_tools_only_for_audio_video(self):
        result = preflight(self.source, self.config)
        self.assertEqual(result["counts"], {"image": 2, "video": 0, "audio": 0})
        self.assertFalse(result["errors"])
        (self.source / "clip.mp4").write_bytes(b"recognized extension, not decoded in preflight")
        result = preflight(self.source, self.config)
        self.assertEqual(result["counts"]["video"], 1)
        self.assertTrue(any("ffmpeg" in error for error in result["errors"]))
        self.assertTrue(any("ffprobe" in error for error in result["errors"]))
        result = preflight(self.source, Config(self.config.database, exclusions=("*.mp4",)))
        self.assertEqual(result["counts"]["video"], 0)
        self.assertFalse(result["errors"])

    def test_preflight_discloses_missing_heif_decoder_and_saved_video_timeout(self):
        (self.source / "phone.heic").write_bytes(b"decoder not needed for preflight")
        (self.source / "large.mov").write_bytes(b"codec not needed for preflight")
        config = Config(self.config.database, ffmpeg="ffmpeg", ffprobe="ffprobe", timeout=180)
        with patch("storylens.workflow.importlib.util.find_spec", return_value=None):
            result = preflight(self.source, config)
        warnings = "\n".join(result["warnings"])
        self.assertIn("1 HEIC/HEIF files detected", warnings)
        self.assertIn("python -m pip install 'pillow-heif>=1,<2'", warnings)
        self.assertIn("180s", warnings)
        self.assertIn("Existing projects retain their saved timeout", warnings)

    def test_preflight_rejects_empty_missing_traversal_and_oversized_collections(self):
        empty = self.root / "empty"
        empty.mkdir()
        for source in (empty, self.root / "missing", self.photo):
            with self.subTest(source=source):
                self.assertTrue(preflight(source, self.config)["errors"])
        with patch("storylens.workflow.discover", return_value=iter([PermissionError("unreadable directory")])):
            self.assertTrue(any("traversal" in error for error in preflight(self.source, self.config)["errors"]))
        with patch("storylens.workflow.discover", return_value=iter([self.photo] * 10001)):
            self.assertTrue(any("10000" in error for error in preflight(self.source, self.config)["errors"]))
        destination = self.root / "not-created" / "project"
        with self.assertRaises(ValueError):
            Project.create(destination, empty, self.config)
        self.assertFalse(destination.parent.exists())

    def test_explicit_local_ai_checks_installed_configuration_without_creating_output(self):
        result = preflight(self.source, self.config, mode="local-ai")
        self.assertTrue(result["errors"])
        with self.assertRaises(ValueError):
            Project.create(self.root / "ai", self.source, self.config, mode="local-ai")
        self.assertFalse((self.root / "ai").exists())
        model = self.root / "installed-model"
        model.mkdir()
        (model / "config.json").write_text('{"model_type":"clip"}')
        config = Config(self.config.database, embedding_model=str(model), vision_model="installed-local",
                        ollama_endpoint="https://user:token@127.0.0.1/?secret=1")
        self.assertTrue(preflight(self.source, config, mode="local-ai", depth="standard")["errors"])
        with self.assertRaises(ValueError):
            Project.create(self.root / "secret-ai", self.source, config, mode="local-ai", depth="standard")
        self.assertFalse((self.root / "secret-ai").exists())
        with patch("storylens.providers.OllamaVision", side_effect=AssertionError("fast mode has no vision provider")):
            fast = Project.create(self.root / "fast-ai", self.source, config, mode="local-ai")
            self.assertEqual(fast.config.vision_model, "")
            self.assertEqual(fast.config.ollama_endpoint, "http://127.0.0.1:11434")
            self.assertEqual(Project.open(fast.directory).depth, "fast")
        result = preflight(self.source, Config(self.config.database, embedding_model=str(model)),
                           mode="local-ai", depth="standard")
        self.assertFalse(result["errors"])

    def test_private_project_never_reuses_global_catalog_or_source_directory(self):
        private = self.root / "private"
        private.mkdir()
        secret = private / "secret.png"
        Image.new("RGB", (16, 16), "red").save(secret)
        with Analyzer(self.config) as app:
            app.scan(private)
        before = self.photo.read_bytes()
        self.project.analyze()
        self.assertEqual(len(self.project.list_media()), 2)
        self.assertEqual(self.project.config.database, self.project.directory / "catalog.sqlite3")
        self.assertNotIn(fingerprint(secret, snapshot(secret)), {item["id"] for item in self.project.list_media()})
        self.assertEqual(before, self.photo.read_bytes())
        if os.name == "posix":
            self.assertEqual(self.project.directory.stat().st_mode & 0o777, 0o700)
            self.assertEqual((self.project.directory / "project.json").stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            Project.create(self.source / "project", self.source, self.config)
        with self.assertRaises(ValueError):
            Project.create(self.project.directory, self.source, self.config)
        unrelated = self.root / "unrelated"
        unrelated.mkdir()
        (unrelated / "keep").write_bytes(b"keep")
        with self.assertRaises(ValueError):
            Project.create(unrelated, self.source, self.config)
        self.assertEqual((unrelated / "keep").read_bytes(), b"keep")

    def test_reopen_reuses_stage_caches_and_ignores_ambient_environment(self):
        first = self.project.analyze()
        self.assertEqual(first["analyzed"], 2)
        document = self.project.recommend(duration_seconds=8)
        path = self.project.document_path
        with patch.dict(os.environ, {"STORYLENS_DATABASE": str(self.root / "foreign.sqlite3"),
                                     "STORYLENS_EMBEDDING_MODEL": "/unexpected/model"}):
            resumed = Project.open(self.project.directory)
        self.assertEqual(resumed.document_path, path)
        self.assertEqual(resumed.load_storyboard(), document)
        self.assertFalse(resumed.config.embedding_model)
        second = resumed.analyze()
        self.assertEqual(second["cache_hits"], 2)
        self.assertEqual(second["analyzed"], 0)
        self.assertEqual(resumed.state["analysis"]["status"], "completed")
        self.assertEqual(resumed.document_path, path)
        state = resumed.state
        state["analysis"]["status"] = "forged"
        self.assertEqual(resumed.state["analysis"]["status"], "completed")

    def test_interruption_keeps_completed_stages_for_resume(self):
        def interrupt(summary):
            if summary["stages"].get("metadata", {}).get("analyzed", 0):
                raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            self.project.analyze(progress=interrupt)
        resumed = Project.open(self.project.directory)
        self.assertEqual(resumed.state["analysis"]["status"], "interrupted")
        self.assertGreaterEqual(resumed.state["analysis"]["summary"]["stages"]["metadata"]["analyzed"], 1)
        summary = resumed.analyze()
        self.assertGreaterEqual(summary["stages"]["metadata"]["cache_hits"], 1)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(resumed.state["analysis"]["status"], "completed")

    def test_partial_failures_remain_visible_across_reopen_and_repair(self):
        self.other.write_bytes(b"broken image")
        summary = self.project.analyze()
        self.assertEqual(summary["failed"], 1)
        self.assertTrue(summary["errors"])
        resumed = Project.open(self.project.directory)
        self.assertEqual(resumed.state["analysis"]["status"], "partial")
        self.assertEqual(resumed.state["analysis"]["summary"]["failed"], 1)
        Image.new("RGB", (48, 32), (120, 80, 20)).save(self.other)
        repaired = resumed.analyze()
        self.assertEqual(repaired["failed"], 0)
        self.assertGreaterEqual(repaired["cache_hits"], 1)

    def test_killed_running_state_is_recovered_honestly_on_open(self):
        manifest = self.manifest()
        manifest["state"]["analysis"]["status"] = "running"
        self.write_manifest(manifest)
        resumed = Project.open(self.project.directory)
        self.assertEqual(resumed.state["analysis"]["status"], "interrupted")
        self.assertIn("resum", resumed.state["analysis"]["error"])
        self.assertEqual(self.manifest()["state"], resumed.state)

    def test_storyboard_edits_are_versioned_without_overwrites(self):
        document = self.analyzed()
        first = self.project.document_path
        first_bytes = first.read_bytes()
        edited = deepcopy(document)
        edited["title"] = "Reviewed order"
        edited["items"].reverse()
        second = self.project.save_storyboard(edited)
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), first_bytes)
        self.assertEqual(self.project.load_storyboard(), edited)
        self.assertEqual(Project.open(self.project.directory).load_storyboard(), edited)
        self.assertEqual(set(self.project.state), {"analysis", "technical", "ai", "storyboard"})

    def test_another_session_cannot_overwrite_current_storyboard(self):
        document = self.analyzed()
        stale = Project.open(self.project.directory)
        changed = deepcopy(document)
        changed["title"] = "The current review"
        self.project.save_storyboard(changed)
        current_path = self.project.document_path
        manifest_before = (self.project.directory / "project.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "another session"):
            stale.save_storyboard(document)
        self.assertEqual((self.project.directory / "project.json").read_bytes(), manifest_before)
        reopened = Project.open(self.project.directory)
        self.assertEqual(reopened.document_path, current_path)
        self.assertEqual(reopened.load_storyboard(), changed)

    def test_external_storyboard_edits_and_current_feedback_are_validated(self):
        document = self.analyzed()
        changed = deepcopy(document)
        changed["title"] = "Externally edited"
        self.project.document_path.write_text(json.dumps(changed))
        self.assertEqual(self.project.load_storyboard(), changed)
        media_id = changed["items"][0]["media_id"]
        with Analyzer(self.project.config) as app:
            app.set_feedback(media_id, {"capture_time": "2025-01-02T03:04:05Z"})
        self.assertEqual(self.project.media(media_id)["feedback"]["capture_time"], "2025-01-02T03:04:05Z")
        self.project.set_selection(media_id, "exclude")
        with self.assertRaises(ValueError):
            self.project.load_storyboard()
        regenerated = self.project.recommend(duration_seconds=8)
        self.assertNotIn(media_id, {item["media_id"] for item in regenerated["items"]})
        self.assertIn(media_id, regenerated["excluded"])

    def test_changed_source_reanalysis_refreshes_identity_and_foreign_catalog_fails_closed(self):
        self.analyzed()
        old_id = fingerprint(self.photo, snapshot(self.photo))
        Image.new("RGB", (48, 32), "green").save(self.photo)
        # UI reads authorize references without rereading the whole collection.
        self.assertEqual(len(self.project.list_media()), 2)
        self.project.analyze()
        current_ids = {item["id"] for item in self.project.list_media()}
        self.assertNotIn(old_id, current_ids)
        self.assertIn(fingerprint(self.photo, snapshot(self.photo)), current_ids)
        private = self.root / "private"
        private.mkdir()
        Image.new("RGB", (20, 20), "red").save(private / "secret.png")
        with Analyzer(self.project.config) as app:
            app.scan(private)
        for action in (self.project.list_media, self.project.recommend, self.project.analyze):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, "outside.*source"):
                action()

    def test_ui_reads_do_not_hash_sources_or_group_all_media_per_entry(self):
        document = self.analyzed()
        with patch("storylens.ingest.fingerprint", side_effect=AssertionError("UI read must not hash media")):
            self.assertEqual(self.project.load_storyboard(), document)
            indexed = self.project.list_media()
            with patch.object(Analyzer, "_storyboard_media", side_effect=AssertionError("per-item read must not group catalog")):
                for row in indexed:
                    item = self.project.media(row["id"])
                    self.assertEqual(item["id"], row["id"])
                    self.assertEqual(item["paths"], row["paths"])
                    self.assertIn("analysis", item)
                    self.assertIn("feedback", item)

    def test_selection_applies_to_omitted_current_media_and_requires_regeneration(self):
        self.project.analyze()
        document = self.project.recommend(duration_seconds=4)
        omitted = next(item["id"] for item in self.project.list_media()
                       if item["id"] not in {entry["media_id"] for entry in document["items"]})
        self.project.set_selection(omitted, "include")
        self.assertEqual(self.project.media(omitted)["feedback"]["selection"], "include")
        with self.assertRaises(ValueError):
            self.project.load_storyboard()
        document = self.project.recommend(duration_seconds=8)
        self.assertIn(omitted, document["required"])
        self.assertTrue(next(item for item in document["items"] if item["media_id"] == omitted)["required"])
        self.project.set_selection(omitted, "neutral")
        self.assertEqual(self.project.media(omitted)["feedback"]["selection"], "neutral")
        document = self.project.recommend(duration_seconds=8)
        self.assertNotIn(omitted, document["required"])

    def test_invalid_manifests_are_rejected_without_following_external_references(self):
        original = self.manifest()
        variants = []
        for field, value in (("schema_version", "99.0"), ("unexpected", "field"), ("source", str(self.project.directory))):
            changed = deepcopy(original)
            changed[field] = value
            variants.append(changed)
        for key, value in (("database", "/tmp/foreign.sqlite3"), ("trust_stat", 1),
                           ("embedding_model", 1),
                           ("ollama_endpoint", "http://user:secret@127.0.0.1/?token=x")):
            changed = deepcopy(original)
            changed["config"][key] = value
            variants.append(changed)
        for key, value in (("storyboard", "../secret.json"), ("unexpected", None),
                           ("analysis", {"status": [], "summary": None, "error": None}),
                           ("analysis", {"status": "completed", "summary": {}, "error": None})):
            changed = deepcopy(original)
            changed["state"][key] = value
            variants.append(changed)
        for changed in variants:
            self.write_manifest(changed)
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                Project.open(self.project.directory)
        self.write_manifest(original)
        self.assertEqual(Project.open(self.project.directory).source, self.source)

    def test_bounded_duplicate_nonfinite_and_nonobject_json_are_rejected(self):
        path = self.project.directory / "project.json"
        original = path.read_bytes()
        for raw in (b"[]", b'{"kind":1,"kind":2}', b'{"x":NaN}', b" " * (MAX_JSON_BYTES + 1), b"{" * 2000):
            path.write_bytes(raw)
            with self.subTest(length=len(raw)), self.assertRaises(ValueError):
                Project.open(self.project.directory)
        path.write_bytes(original)

    def test_symlinked_state_and_unrelated_catalog_are_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.project.directory / "storyboards" / "secret"
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Unsafe project path"):
            Project.open(self.project.directory)
        link.unlink()
        self.project.config.database.write_bytes(b"not a sqlite database")
        with self.assertRaisesRegex(ValueError, "catalog"):
            Project.open(self.project.directory)

    def test_legacy_editor_state_is_dropped_without_deleting_project_files(self):
        document = self.analyzed()
        manifest = self.manifest()
        manifest["schema_version"] = "1.0"
        manifest["state"] = {key: manifest["state"][key] for key in ("analysis", "storyboard")}
        old_files = {
            "outputs/fixture.fcproj": b"legacy native project",
            "outputs/preview.png": b"legacy preview",
            "outputs/export.mp4": b"legacy export",
            "reports/create.json": b'{"status":"succeeded"}',
        }
        for reference, content in old_files.items():
            path = self.project.directory / reference
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(content)
        digest = "a" * 64
        manifest["state"].update(
            approved_hash=digest, approved_evidence=digest,
            editor={"path": "outputs/fixture.fcproj", "storyboard_hash": digest},
            preview={"path": "outputs/preview.png", "project_hash": digest},
            export="outputs/export.mp4", reports=["reports/create.json"],
            operation={"kind": "create", "status": "running", "report": "reports/create.json", "error": None},
        )
        self.write_manifest(manifest)
        reopened = Project.open(self.project.directory)
        self.assertEqual(set(reopened.state), {"analysis", "technical", "ai", "storyboard"})
        self.assertEqual(reopened.load_storyboard(), document)
        self.assertEqual(reopened.state["analysis"]["status"], "completed")
        self.assertEqual(self.manifest()["state"], reopened.state)
        self.assertEqual(self.manifest()["schema_version"], "3.0")
        for reference, content in old_files.items():
            self.assertEqual((reopened.directory / reference).read_bytes(), content)
        self.assertEqual(Project.open(reopened.directory).state, reopened.state)

    def test_legacy_editor_references_are_ignored_not_followed(self):
        manifest = self.manifest()
        manifest["schema_version"] = "1.0"
        manifest["state"] = {key: manifest["state"][key] for key in ("analysis", "storyboard")}
        outside = self.root / "private.json"
        outside.write_bytes(b"unrelated private data")
        manifest["state"].update(
            approved_hash="a" * 64, approved_evidence="b" * 64,
            editor={"path": str(outside), "storyboard_hash": "a" * 64},
            preview={"path": "../private.json", "project_hash": "b" * 64},
            export=str(outside), reports=[str(outside)],
            operation={"kind": "export", "status": "running", "report": str(outside), "error": None},
        )
        self.write_manifest(manifest)
        reopened = Project.open(self.project.directory)
        self.assertEqual(set(reopened.state), {"analysis", "technical", "ai", "storyboard"})
        self.assertEqual(outside.read_bytes(), b"unrelated private data")
        self.assertEqual(self.manifest()["state"], reopened.state)

    def test_invalid_storyboard_does_not_replace_current_version(self):
        document = self.analyzed()
        current = self.project.document_path
        original = current.read_bytes()
        manifest = (self.project.directory / "project.json").read_bytes()
        invalid = deepcopy(document)
        invalid["items"][0]["media_id"] = "f" * 64
        with self.assertRaises(ValueError):
            self.project.save_storyboard(invalid)
        self.assertEqual(self.project.document_path, current)
        self.assertEqual(current.read_bytes(), original)
        self.assertEqual((self.project.directory / "project.json").read_bytes(), manifest)
        self.assertEqual(Project.open(self.project.directory).load_storyboard(), document)
