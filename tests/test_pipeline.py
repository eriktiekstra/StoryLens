"""Consumer-visible stage isolation, artifact recovery and CLI behavior."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

from storylens.config import Config
from storylens.service import Analyzer


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.media = self.root / "sources"
        self.media.mkdir()
        self.config = Config(self.root / "catalog.sqlite3")

    def image(self):
        source = self.media / "photo.png"
        data = np.random.default_rng(42).integers(0, 256, (96, 128, 3), dtype=np.uint8)
        Image.fromarray(data).save(source)
        return source

    def test_default_analysis_and_metadata_force_are_independent(self):
        source = self.image()
        original = source.read_bytes()
        with Analyzer(self.config) as app:
            first = app.scan(self.media, analyze=True)
            self.assertEqual(first["failed"], 0, first)
            media_id = app.catalog.known_path(source)["media_id"]
            stored = app.catalog.get(media_id)["analysis"]
            self.assertEqual(set(stored), {"metadata", "image_technical"})
            self.assertEqual(first["stages"]["image_technical"]["analyzed"], 1)
            second = app.scan(self.media, analyze=True)
            self.assertEqual(second["cache_hits"], 1)
            self.assertEqual(second["stages"]["image_technical"]["cache_hits"], 1)
            app.scan(self.media, analyze=True, stage="metadata", force=True)
            refreshed = app.catalog.get(media_id)["analysis"]
            self.assertEqual(refreshed["image_technical"], stored["image_technical"])
        self.assertEqual(source.read_bytes(), original)

    def test_exact_duplicate_query_ignores_missing_historical_paths(self):
        original = self.image()
        moved = self.media / "moved.png"
        copy = self.media / "copy.png"
        with Analyzer(self.config) as app:
            app.scan(self.media)
            original.rename(moved)
            app.scan(self.media)
            self.assertEqual(app.duplicates()["items"], [])
            shutil.copyfile(moved, copy)
            result = app.scan(self.media)
            self.assertEqual(result["failed"], 0, result)
            duplicate = app.duplicates()["items"][0]
            self.assertEqual(set(duplicate["paths"]), {str(moved.resolve()), str(copy.resolve())})
            self.assertEqual(duplicate["media_id"], app.catalog.known_path(moved)["media_id"])

    def test_near_duplicates_require_computed_image_features(self):
        source = self.image()
        with Image.open(source) as image:
            image.save(self.media / "reencoded.jpg", quality=95, subsampling=0)
        with Analyzer(self.config) as app:
            app.scan(self.media)
            self.assertEqual(app.duplicates(mode="near")["items"], [])
            result = app.scan(self.media, analyze=True)
            self.assertEqual(result["failed"], 0, result)
            pairs = app.duplicates(mode="near")["items"]
            identities = {row["id"] for row in app.catalog.page()["items"]}
            self.assertEqual({pairs[0]["left_id"], pairs[0]["right_id"]}, identities)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg binaries required")
    def test_video_failure_does_not_erase_metadata_or_block_images(self):
        self.image()
        video = self.media / "clip.mp4"
        self.make_video(video)
        with Analyzer(Config(self.config.database, ffmpeg="nonexistent-storylens-ffmpeg")) as app:
            result = app.scan(self.media, analyze=True)
            stored = app.catalog.get(app.catalog.known_path(video)["media_id"])
            self.assertIn("metadata", stored["analysis"])
            self.assertNotIn("video_technical", stored["analysis"])
            self.assertEqual(stored["failures"][0]["stage"], "video_technical")
            self.assertEqual(result["stages"]["image_technical"]["analyzed"], 1)
            self.assertEqual(result["failed"], 1)

    @staticmethod
    def make_video(path):
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=80x60:r=10",
                        "-t", "1", "-c:v", "mpeg4", str(path)], check=True)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg binaries required")
    def test_missing_derived_frame_recomputes_only_video_stage(self):
        video = self.media / "clip.mp4"
        self.make_video(video)
        before = hashlib.sha256(video.read_bytes()).hexdigest()
        with Analyzer(self.config) as app:
            first = app.scan(self.media, analyze=True)
            self.assertEqual(first["failed"], 0, first)
            media_id = app.catalog.known_path(video)["media_id"]
            frame = Path(app.scenes(media_id)["items"][0]["representative_frame"]["path"])
            self.assertTrue(frame.is_relative_to(app.artifact_root))
            frame.unlink()
            second = app.scan(self.media, analyze=True)
            self.assertEqual(second["stages"]["metadata"]["cache_hits"], 1)
            self.assertEqual(second["stages"]["video_technical"]["analyzed"], 1)
            self.assertTrue(Path(app.scenes(media_id)["items"][0]["representative_frame"]["path"]).is_file())
        self.assertEqual(hashlib.sha256(video.read_bytes()).hexdigest(), before)

    def test_cli_analyze_and_duplicate_pages(self):
        source = self.image()
        shutil.copyfile(source, self.media / "copy.png")
        command = [sys.executable, "-m", "storylens", "--database", str(self.config.database), "--json"]
        result = subprocess.run([*command, "analyze", str(self.media)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["stages"]["image_technical"]["analyzed"], 1)
        duplicates = subprocess.run([*command, "duplicates", "--limit", "1"], text=True, capture_output=True, check=True)
        page = json.loads(duplicates.stdout)
        self.assertEqual(set(page["items"][0]["paths"]),
                         {str(source.resolve()), str((self.media / "copy.png").resolve())})
        invalid = subprocess.run([*command, "duplicates", "--mode", "near", "--distance", "-1"], text=True, capture_output=True)
        self.assertEqual(invalid.returncode, 1)
        self.assertIn("error", json.loads(invalid.stdout))


if __name__ == "__main__":
    unittest.main()
