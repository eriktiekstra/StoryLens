"""Generated media only: no private or externally licensed fixture assets."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import wave

from PIL import Image

from storylens.config import Config, load_config
from storylens.ingest import fingerprint, snapshot
from storylens.metadata import MetadataExtractor
from storylens.service import Analyzer


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.media = self.base / "sources"
        self.media.mkdir()
        self.config = Config(self.base / "catalog.sqlite3")

    def image(self, name="photo.jpg", color="red"):
        path = self.media / name
        exif = Image.Exif()
        exif[274] = 6
        exif[271] = "Synthetic camera"
        exif[306] = "2025:07:08 12:30:00"
        Image.new("RGB", (32, 24), color).save(path, exif=exif)
        return path

    def test_metadata_preserves_source_and_naive_timestamp(self):
        source = self.image()
        before = source.read_bytes()
        with Analyzer(self.config) as app:
            result = app.scan(self.media, analyze=True, stage="metadata")
            item = app.catalog.get(app.catalog.page()["items"][0]["id"])
        self.assertEqual(result["analyzed"], 1)
        metadata = item["analysis"]["metadata"]["result"]
        self.assertEqual((metadata["width"], metadata["height"], metadata["orientation"]), (32, 24, 6))
        self.assertEqual(metadata["exif"]["DateTime"], "2025:07:08 12:30:00")
        self.assertEqual(metadata["exif"]["Make"], "Synthetic camera")
        self.assertEqual(source.read_bytes(), before)

    def test_move_and_duplicates_reuse_analysis_after_restart(self):
        original = self.image()
        with Analyzer(self.config) as app:
            result = app.scan(self.media, analyze=True, stage="metadata")
            self.assertEqual(result["failed"], 0, result)
            identity = app.catalog.page()["items"][0]["id"]
        moved = self.media / "moved.jpg"
        original.rename(moved)
        shutil.copyfile(moved, self.media / "copy.jpg")
        with Analyzer(self.config) as app:
            result = app.scan(self.media, analyze=True, stage="metadata")
            self.assertEqual(result["cache_hits"], 2, result)
            self.assertEqual(app.catalog.page()["items"][0]["id"], identity)
            self.assertEqual({p["path"] for p in app.catalog.get(identity)["paths"]},
                             {str(original.resolve()), str(moved.resolve()),
                              str((self.media / "copy.jpg").resolve())})

    def test_full_hash_detects_same_size_mtime_content_replacement(self):
        source = self.image("photo.bmp")
        before = source.stat()
        with Analyzer(self.config) as app:
            result = app.scan(self.media, analyze=True, stage="metadata")
            self.assertEqual(result["failed"], 0, result)
            identity = app.catalog.known_path(source)["media_id"]
            self.image("photo.bmp", "blue")
            self.assertEqual(source.stat().st_size, before.st_size)
            os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
            result = app.scan(self.media, analyze=True, stage="metadata")
            self.assertNotEqual(app.catalog.known_path(source)["media_id"], identity)
            self.assertEqual(result["analyzed"], 1)

    def test_hash_handles_distinct_ctime_semantics_without_missing_changes(self):
        source = self.image()
        before = snapshot(source)
        fields = {name: getattr(before, name) for name in
                  ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")}
        opened = SimpleNamespace(**{**fields, "st_ctime_ns": fields["st_ctime_ns"] + 100})
        with patch("storylens.ingest.os.fstat", return_value=opened):
            self.assertEqual(fingerprint(source, before),
                             "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest())
        changed = SimpleNamespace(**{**vars(opened), "st_ctime_ns": opened.st_ctime_ns + 1})
        with patch("storylens.ingest.os.fstat", side_effect=[opened, changed]):
            with self.assertRaisesRegex(ValueError, "Source changed during hashing"):
                fingerprint(source, before)
        for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns"):
            replaced = SimpleNamespace(**{**vars(opened), field: getattr(opened, field) + 1})
            with self.subTest(field=field), patch("storylens.ingest.os.fstat", return_value=replaced):
                with self.assertRaisesRegex(ValueError, "Source changed before hashing"):
                    fingerprint(source, before)

    def test_stage_version_and_force_invalidation(self):
        self.image()
        with Analyzer(self.config) as app:
            app.scan(self.media, analyze=True, stage="metadata")
            self.assertEqual(app.scan(self.media, analyze=True, stage="metadata")["cache_hits"], 1)
            with patch("storylens.metadata.ALGORITHM_VERSION", "metadata-next"):
                self.assertEqual(app.scan(self.media, analyze=True, stage="metadata")["analyzed"], 1)
            self.assertEqual(app.scan(self.media, analyze=True, stage="metadata", force=True)["analyzed"], 1)

    def test_corrupt_media_isolated_and_retryable(self):
        self.image()
        corrupt = self.media / "broken.png"
        corrupt.write_bytes(b"not a PNG")
        with Analyzer(self.config) as app:
            first = app.scan(self.media, analyze=True, stage="metadata")
            second = app.scan(self.media, analyze=True, stage="metadata")
            failed_id = app.catalog.known_path(corrupt)["media_id"]
            self.assertEqual(first["failed"], 1, first)
            self.assertEqual(first["analyzed"], 1, first)
            self.assertEqual(second["failed"], 1, second)
            self.assertEqual(second["cache_hits"], 1, second)
            self.assertEqual(app.catalog.get(failed_id)["analysis"], {})
            self.assertIn("error", app.catalog.get(failed_id)["failures"][0])

    def test_exclusions_prune_directories_and_symlinks(self):
        self.image()
        hidden = self.media / "private"
        hidden.mkdir()
        shutil.copyfile(self.media / "photo.jpg", hidden / "other.jpg")
        try:
            (self.media / "loop").symlink_to(self.media, target_is_directory=True)
        except OSError:
            pass  # Windows may require privileges; directory exclusion remains exercised.
        with Analyzer(Config(self.config.database, exclusions=("private",))) as app:
            self.assertEqual(app.scan(self.media)["discovered"], 1)

    def test_pagination_has_no_gaps_and_validates_boundaries(self):
        for index, color in enumerate(("red", "green", "blue")):
            self.image(f"{index}.jpg", color)
        with Analyzer(self.config) as app:
            app.scan(self.media)
            first = app.catalog.page(2)
            second = app.catalog.page(2, first["next_cursor"])
            ids = [x["id"] for x in first["items"] + second["items"]]
            self.assertEqual(ids, sorted({x["id"] for x in app.catalog.page()["items"]}))
            self.assertIsNone(second["next_cursor"])
            with self.assertRaises(ValueError):
                app.catalog.page(501)

    def test_configuration_precedence_and_invalid_values(self):
        self.assertEqual(Config(self.base / "defaults.sqlite3").timeout, 600)
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.base)}, clear=True):
            self.assertEqual(load_config().timeout, 600)
        config = self.base / "config.toml"
        config.write_text('[storylens]\ndatabase="' + (self.base / "catalog.sqlite3").as_posix() +
                         '"\ntimeout=5\nexclusions=["private"]\n')
        with patch.dict(os.environ, {"STORYLENS_TIMEOUT": "10"}, clear=True):
            self.assertEqual(load_config(config, timeout=20).timeout, 20)
            self.assertEqual(load_config(config).timeout, 10)
        for value in ('unknown=true', 'timeout=-1', 'timeout=nan', 'trust_stat="false"', 'exclusions="private"'):
            config.write_text("[storylens]\n" + value)
            with self.subTest(value=value), patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
                load_config(config)

    def test_newer_catalog_rejected_without_reset(self):
        connection = sqlite3.connect(self.config.database)
        connection.execute("PRAGMA user_version=99")
        connection.close()
        with self.assertRaisesRegex(ValueError, "schema 99"):
            Analyzer(self.config)
        connection = sqlite3.connect(self.config.database)
        self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 99)
        connection.close()

    def test_missing_probe_does_not_block_image_metadata(self):
        self.image()
        (self.media / "sound.wav").write_bytes(b"RIFF")
        with Analyzer(Config(self.config.database, ffprobe="nonexistent-storylens-probe")) as app:
            result = app.scan(self.media, analyze=True, stage="metadata")
        self.assertEqual(result["analyzed"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertIn("unavailable", result["errors"][0]["error"])

    def test_probe_timeout_reports_failure(self):
        extractor = MetadataExtractor(timeout=0.01)
        with patch("storylens.metadata.subprocess.run", side_effect=subprocess.TimeoutExpired("ffprobe", 0.01)):
            with self.assertRaisesRegex(ValueError, "timeout"):
                extractor.extract(self.media / "sound.wav", "audio")

    def test_storage_failure_not_misreported_as_media_failure(self):
        self.image()
        with Analyzer(self.config) as app:
            with patch.object(app.catalog, "save", side_effect=sqlite3.OperationalError("database or disk is full")):
                with self.assertRaises(sqlite3.OperationalError):
                    app.scan(self.media, analyze=True, stage="metadata")

    def test_cli_json_partial_exit_and_export(self):
        self.image()
        (self.media / "broken.jpg").write_bytes(b"broken")
        command = [sys.executable, "-m", "storylens", "--database", str(self.config.database), "--json"]
        result = subprocess.run([*command, "analyze", str(self.media), "--stage", "metadata"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["failed"], 1, result.stdout)
        exported = subprocess.run([*command, "export", "--limit", "1"], capture_output=True, text=True, check=True)
        page = json.loads(exported.stdout)
        self.assertEqual(page["schema_version"], "1.0")
        self.assertIsNotNone(page["next_cursor"])
        self.assertIn("paths", page["items"][0])

    @unittest.skipUnless(shutil.which("ffprobe") and shutil.which("ffmpeg"), "FFmpeg integration requires system binaries")
    def test_real_audio_and_video_stream_metadata(self):
        audio = self.media / "sound.wav"
        with wave.open(str(audio), "wb") as target:
            target.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            target.writeframes(b"\0\0" * 16000)
        video = self.media / "clip.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x48:r=12",
                        "-t", "1", "-c:v", "mpeg4", str(video)], check=True)
        with Analyzer(self.config) as app:
            result = app.scan(self.media, analyze=True, stage="metadata")
            self.assertEqual(result["analyzed"], 2, result)
            records = [app.catalog.get(row["id"]) for row in app.catalog.page()["items"]]
        for item in records:
            metadata = item["analysis"]["metadata"]["result"]
            self.assertAlmostEqual(float(metadata["duration_seconds"]), 1, places=2)
            self.assertEqual(metadata["provenance"]["tool"], "ffprobe")
            if item["kind"] == "video":
                self.assertEqual(metadata["streams"][0]["avg_frame_rate"], "12/1")
                self.assertEqual(metadata["streams"][0]["width"], 64)
            else:
                self.assertEqual(metadata["streams"][0]["sample_rate"], "16000")

    def test_png_exif_metadata_and_pixel_limit_failure(self):
        self.image("photo.png")
        with Analyzer(self.config) as app:
            result = app.scan(self.media, analyze=True, stage="metadata")
            self.assertEqual(result["analyzed"], 1, result)
            with patch("PIL.Image.MAX_IMAGE_PIXELS", 1):
                limited = app.scan(self.media, analyze=True, stage="metadata", force=True)
            self.assertEqual(limited["failed"], 1)
            self.assertIn("pixel limit", limited["errors"][0]["error"])

    def test_gps_coordinates_validate_hemispheres_and_ranges(self):
        self.assertEqual(MetadataExtractor._gps({1: "S", 2: (10, 30, 0), 3: "E", 4: (20, 0, 0)}),
                         {"latitude": -10.5, "longitude": 20.0})
        for values in ({1: "E", 2: (10, 0, 0)}, {1: "N", 2: (10, 61, 0)}, {1: "N", 2: (91, 0, 0)}):
            self.assertIsNone(MetadataExtractor._gps(values)["latitude"])

if __name__ == "__main__":
    unittest.main()
