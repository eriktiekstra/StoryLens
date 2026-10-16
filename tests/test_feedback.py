"""Durable user annotations, including real generated-media reanalysis."""

from concurrent.futures import ThreadPoolExecutor
import copy
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import unittest
import wave

from PIL import Image

from storylens.audio_analysis import AudioTechnicalExtractor
from storylens.config import Config
from storylens.feedback import FeedbackStore
from storylens.metadata import MetadataExtractor
from storylens.service import Analyzer
from storylens.video_analysis import VideoTechnicalExtractor


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.catalog_path = self.base / "catalog.sqlite3"
        self.store = FeedbackStore(self.catalog_path)
        self.image = {"id": "image", "kind": "image", "analysis": {}}
        self.audio = {"id": "audio", "kind": "audio", "analysis": {
            "audio_technical": {"result": {"source_timing": {"origin_seconds": 7, "end_seconds": 10}}}}}

    def tearDown(self):
        self.store.close()

    @staticmethod
    def segment(start=7, end=8, selection="include", **extra):
        return {"start_seconds": start, "end_seconds": end, "selection": selection, **extra}

    def test_new_envelope_and_private_sidecar(self):
        self.assertEqual(self.store.get("image"), {
            "schema_version": "1.0", "media_id": "image", "values": {}, "updated_at": None})
        self.assertEqual(self.store.path, self.catalog_path.with_suffix(".feedback.sqlite3"))
        self.assertFalse(self.catalog_path.exists())
        if os.name == "posix":
            self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(self.store.path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_timezone_and_explicit_null_are_not_reset(self):
        for capture in ("2026-10-16T10:20:30", "2026-10-16T10:20:30+05:30", "2026-10-16T10:20:30Z"):
            result = self.store.update("image", {"capture_time": capture}, self.image)
            self.assertEqual(result["values"]["capture_time"], capture)
            self.assertIsNotNone(result["updated_at"])
        result = self.store.update("image", {"capture_time": None, "gps": None, "selection": "exclude"}, self.image)
        self.assertEqual(result["values"], {"capture_time": None, "gps": None, "selection": "exclude"})
        result = self.store.reset("image", ["capture_time"])
        self.assertNotIn("capture_time", result["values"])
        self.assertIn("gps", result["values"])
        with FeedbackStore(self.catalog_path) as reopened:
            self.assertEqual(reopened.get("image"), result)
        self.assertEqual(self.store.reset("image")["values"], {})

    def test_fields_are_strict_and_invalid_patch_is_atomic(self):
        before = self.store.update("image", {"selection": "include"}, self.image)
        for patch in ({"unknown": 1}, {"selection": None}, {"selection": "maybe"},
                      {"capture_time": "2026-10-16"}, {"capture_time": "2026-02-30T00:00:00"},
                      {"capture_time": 0}, {"gps": {}},
                      {"gps": {"latitude": True, "longitude": 1}},
                      {"gps": {"latitude": 91, "longitude": 1}},
                      {"gps": {"latitude": 1, "longitude": float("nan")}},
                      {"gps": {"latitude": 1, "longitude": 2, "altitude": 3}},
                      {"selection": "exclude", "capture_time": "invalid"}, {"event_id": 1}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.store.update("image", patch, self.image)
            self.assertEqual(self.store.get("image"), before)
        for fields in ("gps", ["unknown"], [None]):
            with self.assertRaises(ValueError):
                self.store.reset("image", fields)
        self.assertEqual(self.store.update("image", {"gps": {"latitude": -90, "longitude": 180}}, self.image)
                         ["values"]["gps"], {"latitude": -90, "longitude": 180})

    def test_concurrent_updates_preserve_unrelated_fields(self):
        barrier = threading.Barrier(2)

        def update_field(key, value):
            with FeedbackStore(self.catalog_path) as store:
                for _ in range(20):
                    barrier.wait(timeout=10)
                    store.update("image", {key: value}, self.image)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(update_field, "selection", "exclude"),
                       pool.submit(update_field, "capture_time", "2026-10-16T12:00:00")]
            for future in futures:
                future.result(timeout=30)
        self.assertEqual(self.store.get("image")["values"], {
            "selection": "exclude", "capture_time": "2026-10-16T12:00:00"})

    def test_segment_bounds_half_open_overlap_and_input_immutability(self):
        segments = [self.segment(7, 8, label="Keep this"), self.segment(8, 9, "exclude"),
                    self.segment(7.25, 7.75)]
        media_before, segments_before = copy.deepcopy(self.audio), copy.deepcopy(segments)
        result = self.store.update("audio", {"segments": segments}, self.audio)
        self.assertEqual(result["values"]["segments"], segments)
        self.assertEqual(self.audio, media_before)
        self.assertEqual(segments, segments_before)
        invalid = [[self.segment(0, 1)], [self.segment(7, 10.01)], [self.segment(8, 8)],
                   [self.segment(8, 7)], [self.segment(float("nan"), 8)],
                   [self.segment(7, float("inf"))], [self.segment(True, 8)],
                   [self.segment(selection="neutral")], [self.segment(label=None)],
                   [self.segment(label="x" * 257)], [self.segment(extra=1)],
                   [self.segment()] * 101, [{"start_seconds": 7}], None]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.update("audio", {"segments": value}, self.audio)
        with self.assertRaisesRegex(ValueError, "conflicting"):
            self.store.update("audio", {"segments": [self.segment(7, 9), self.segment(8, 10, "exclude")]}, self.audio)
        self.assertEqual(self.store.get("audio"), result)
        self.assertEqual(self.store.update("audio", {"segments": []}, self.audio)["values"]["segments"], [])
        with self.assertRaisesRegex(ValueError, "audio/video"):
            self.store.update("image", {"segments": []}, self.image)

    def test_metadata_coverage_uses_actual_stream_origin(self):
        media = {"kind": "video", "analysis": {"metadata": {"result": {
            "duration_seconds": "20", "streams": [{"codec_type": "video", "start_time": "7", "duration": "3"}]}}}}
        self.store.update("video", {"segments": [self.segment(7, 10)]}, media)
        with self.assertRaisesRegex(ValueError, "source coverage"):
            self.store.update("video", {"segments": [self.segment(0, 3)]}, media)
        stream = media["analysis"]["metadata"]["result"]["streams"][0]
        stream.pop("start_time")
        stream.update(start_pts=700, time_base="1/100", duration_ts=300)
        stream.pop("duration")
        self.store.update("video", {"segments": [self.segment(7, 10)]}, media)
        stream.pop("duration_ts")
        with self.assertRaisesRegex(ValueError, "without stream duration"):
            self.store.update("video", {"segments": [self.segment()]}, media)
        stream["start_pts"] = 0
        self.store.update("video", {"segments": [self.segment(0, 20)]}, media)
        stream.pop("start_pts")
        with self.assertRaisesRegex(ValueError, "unknown source origin"):
            self.store.update("video", {"segments": [self.segment(0, 1)]}, media)
        with self.assertRaisesRegex(ValueError, "timing evidence"):
            self.store.update("audio", {"segments": [self.segment()]}, {"kind": "audio"})

    def test_technical_coverage_takes_precedence_over_metadata(self):
        media = copy.deepcopy(self.audio)
        media["analysis"]["metadata"] = {"result": {"duration_seconds": "500"}}
        with self.assertRaisesRegex(ValueError, "source coverage"):
            self.store.update("audio", {"segments": [self.segment(7, 11)]}, media)
        media["analysis"]["audio_technical"]["result"]["source_timing"]["end_seconds"] = float("nan")
        with self.assertRaises(ValueError):
            self.store.update("audio", {"segments": [self.segment()]}, media)

    def test_manual_event_assign_rename_merge_split_clear(self):
        first = self.store.create_event("A user title")
        second = self.store.create_event("A different title")
        self.assertRegex(first["id"], r"^manual:[0-9a-f]{32}$")
        self.assertEqual(first["schema_version"], "1.0")
        self.store.update("image", {"event_id": first["id"], "selection": "exclude"}, self.image)
        self.store.update("other", {"event_id": second["id"]}, self.image)
        self.store.update("other", {"event_id": first["id"]}, self.image)
        self.assertEqual(self.store.members(first["id"]), ["image", "other"])
        self.assertEqual(self.store.members(second["id"]), [])
        renamed = self.store.rename_event(first["id"], "Renamed manually")
        self.assertEqual(renamed["title"], "Renamed manually")
        self.assertEqual(renamed["id"], first["id"])
        self.store.update("other", {"event_id": second["id"]}, self.image)
        self.store.update("image", {"event_id": None}, self.image)
        self.assertEqual(self.store.members(first["id"]), [])
        self.assertEqual(self.store.members(second["id"]), ["other"])
        self.assertEqual(self.store.get("image")["values"], {"selection": "exclude", "event_id": None})
        self.store.reset("image", ["event_id"])
        self.assertNotIn("event_id", self.store.get("image")["values"])
        self.assertIn(renamed, self.store.manual_events())
        for title in ("", "  ", "x" * 257, None):
            with self.assertRaises(ValueError):
                self.store.create_event(title)
            with self.assertRaises(ValueError):
                self.store.rename_event(first["id"], title)
        for action in (lambda: self.store.update("image", {"event_id": "auto:unknown"}, self.image),
                       lambda: self.store.rename_event("manual:missing", "Title"),
                       lambda: self.store.members("manual:missing")):
            with self.assertRaises(KeyError):
                action()

    def test_preferences_validate_merge_persist_and_snapshot(self):
        from storylens.events import normalize_settings

        self.assertEqual(self.store.preferences(), {
            "schema_version": "1.0", "values": normalize_settings(None), "updated_at": None})
        self.store.set_preferences({"distance_km": 12})
        result = self.store.set_preferences({"gap_seconds": 3600})
        self.assertEqual(result["values"]["distance_km"], 12)
        self.assertEqual(result["values"]["gap_seconds"], 3600)
        for patch in ({"extra": 1}, {"gap_seconds": -1}, {"distance_km": float("nan")},
                      {"semantic_depth": "unknown"}, {"gap_seconds": True},
                      {"max_event_seconds": 100}, []):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.store.set_preferences(patch)
            self.assertEqual(self.store.preferences(), result)
        with FeedbackStore(self.catalog_path) as reopened:
            self.assertEqual(reopened.preferences(), result)
        event = self.store.create_event("Preserved title")
        self.store.update("image", {"event_id": event["id"], "gps": None}, self.image)
        self.assertEqual(self.store.snapshot(), {
            "media": {"image": {"event_id": event["id"], "gps": None}},
            "events": [event], "preferences": result["values"]})

    def test_concurrent_preferences_merge_preserves_fields(self):
        barrier = threading.Barrier(2)

        def update(patch):
            with FeedbackStore(self.catalog_path) as store:
                barrier.wait(timeout=10)
                return store.set_preferences(patch)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(update, {"distance_km": 12}), pool.submit(update, {"gap_seconds": 3600})]
            for future in futures:
                future.result(timeout=30)
        values = self.store.preferences()["values"]
        self.assertEqual((values["distance_km"], values["gap_seconds"]), (12, 3600))

    def test_snapshot_and_manual_lists_have_no_hidden_limit(self):
        for index in range(105):
            event = self.store.create_event(f"Event {index}")
            self.store.update(str(index), {"event_id": event["id"]}, self.image)
        snapshot = self.store.snapshot()
        self.assertEqual(len(snapshot["media"]), 105)
        self.assertEqual(len(snapshot["events"]), 105)
        snapshot["media"]["0"].clear()
        self.assertIn("event_id", self.store.get("0")["values"])

    def test_newer_schema_and_sidecar_errors_escape(self):
        path = self.base / "future.sqlite3"
        sidecar = path.with_suffix(".feedback.sqlite3")
        with sqlite3.connect(sidecar) as connection:
            connection.execute("PRAGMA user_version=2")
        with self.assertRaisesRegex(RuntimeError, "Unsupported feedback schema 2"):
            FeedbackStore(path)
        with sqlite3.connect(sidecar) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
        self.store.connection.execute("DROP TABLE media_feedback")
        with self.assertRaises(sqlite3.DatabaseError):
            self.store.get("image")
        with self.assertRaises(sqlite3.DatabaseError):
            self.store.snapshot()
        # A failed read transaction must roll back, not leave the connection stuck.
        self.assertEqual(self.store.manual_events(), [])
        corrupt = self.base / "corrupt.feedback.sqlite3"
        corrupt.write_bytes(b"not a SQLite database")
        with self.assertRaises(sqlite3.DatabaseError):
            FeedbackStore(self.base / "corrupt.sqlite3")

    def test_real_generated_image_force_analysis_preserves_feedback_and_source(self):
        source = self.base / "photo.jpg"
        exif = Image.Exif()
        exif[36867] = "2024:01:02 03:04:05"
        Image.new("RGB", (48, 32), "blue").save(source, exif=exif)
        before = source.read_bytes()
        config = Config(self.catalog_path)
        with Analyzer(config) as app:
            initial = app.scan(source, analyze=True, stage="metadata")
            self.assertEqual(initial["failed"], 0, initial)
            media_id = app.catalog.page()["items"][0]["id"]
            media = app.catalog.get(media_id)
            ai_before = copy.deepcopy(media["analysis"])
            event = self.store.create_event("My own title")
            stored = self.store.update(media_id, {"capture_time": None, "gps": None,
                                      "selection": "exclude", "event_id": event["id"]}, media)
            self.store.set_preferences({"distance_km": 12})
            snapshot = self.store.snapshot()
            self.assertEqual(app.catalog.get(media_id)["analysis"], ai_before)
            forced = app.scan(source, analyze=True, force=True, stage="metadata")
            self.assertEqual(forced["failed"], 0, forced)
            self.assertEqual(forced["analyzed"], 1)
            self.assertEqual(self.store.get(media_id), stored)
            self.assertEqual(self.store.snapshot(), snapshot)
        with Analyzer(config) as app, FeedbackStore(self.catalog_path) as reopened:
            forced = app.scan(source, analyze=True, force=True, stage="technical")
            self.assertEqual(forced["failed"], 0, forced)
            self.assertEqual(reopened.get(media_id), stored)
            self.assertEqual(reopened.snapshot(), snapshot)
        self.assertEqual(source.read_bytes(), before)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_real_audio_video_absolute_bounds_and_force_reanalysis(self):
        audio_path = self.base / "tone.wav"
        with wave.open(str(audio_path), "wb") as output:
            output.setparams((1, 2, 16000, 16000, "NONE", "not compressed"))
            output.writeframes(b"\x00\x10" * 16000)
        original = audio_path.read_bytes()
        with Analyzer(Config(self.catalog_path)) as app:
            summary = app.scan(audio_path, analyze=True, stage="technical")
            self.assertEqual(summary["failed"], 0, summary)
            media_id = app.catalog.page()["items"][0]["id"]
            stored = self.store.update(media_id, {"segments": [self.segment(0.25, 0.75)]}, app.catalog.get(media_id))
            summary = app.scan(audio_path, analyze=True, force=True, stage="technical")
            self.assertEqual(summary["failed"], 0, summary)
            self.assertEqual(self.store.get(media_id), stored)
        self.assertEqual(audio_path.read_bytes(), original)
        shifted = self.base / "shifted.mka"
        video = self.base / "video.mp4"
        commands = [
            ["-i", str(audio_path), "-output_ts_offset", "7", "-c:a", "pcm_s16le", str(shifted)],
            ["-f", "lavfi", "-i", "color=blue:s=48x32:r=10:d=1", "-an", "-c:v", "mpeg4",
             "-output_ts_offset", "7", str(video)],
        ]
        for arguments in commands:
            subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", *arguments],
                           check=True, capture_output=True, timeout=30)
        for kind, source, extractor in (("audio", shifted, AudioTechnicalExtractor()),
                                         ("video", video, VideoTechnicalExtractor())):
            with self.subTest(kind=kind):
                before = source.read_bytes()
                technical = (extractor.extract(source, self.base / "artifacts") if kind == "video"
                             else extractor.extract(source))
                metadata = MetadataExtractor().extract(source, kind)
                media = {"kind": kind, "analysis": {kind + "_technical": {"result": technical},
                                                    "metadata": {"result": metadata}}}
                timing = technical["source_timing"]
                self.assertAlmostEqual(timing["origin_seconds"], 7)
                self.store.update(kind, {"segments": [self.segment(7, 7.5)]}, media)
                with self.assertRaisesRegex(ValueError, "source coverage"):
                    self.store.update(kind, {"segments": [self.segment(0, 0.5)]}, media)
                with self.assertRaisesRegex(ValueError, "source coverage"):
                    self.store.update(kind, {"segments": [self.segment(7, timing["end_seconds"] + .01)]}, media)
                self.assertEqual(source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
