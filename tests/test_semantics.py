"""Semantic cache isolation, evidence coverage and same-space retrieval boundaries."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from storylens.config import Config
from storylens.semantics import SemanticExtractor, normalized, search_results
from storylens.service import Analyzer


class FixtureEncoder:
    def __init__(self):
        self.revision = "one"

    def identity(self):
        return {"provider": "fixture", "revision": self.revision}

    def image(self, path):
        with Image.open(path) as image:
            r, g, b = image.convert("RGB").getpixel((0, 0))
        return [r + 1, g + 1, b + 1]

    def text(self, text):
        return {"red": [1, 0, 0], "blue": [0, 0, 1]}[text]


class FixtureVision:
    def __init__(self):
        self.revision = "one"
        self.fail = False

    def identity(self):
        return {"provider": "fixture-vision", "revision": self.revision}

    def describe(self, path):
        if self.fail:
            raise ValueError("model unavailable")
        return "A colored fixture; model hypothesis."


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.media = self.root / "media"
        self.media.mkdir()
        self.red = self.media / "red.png"
        self.blue = self.media / "blue.png"
        Image.new("RGB", (32, 24), "red").save(self.red)
        Image.new("RGB", (32, 24), "blue").save(self.blue)
        self.app = Analyzer(Config(self.root / "catalog.sqlite3",
                                   embedding_model="fixture-embeddings", vision_model="fixture-vision"))
        self.addCleanup(self.app.catalog.close)
        self.encoder, self.vision = FixtureEncoder(), FixtureVision()
        self.app._embeddings, self.app._vision = self.encoder, self.vision

    def test_cache_model_changes_are_independent_and_failures_retry(self):
        first = self.app.scan(self.media, analyze=True, depth="standard")
        self.assertEqual(first["failed"], 0, first)
        red_id = self.app.catalog.known_path(self.red)["media_id"]
        stored = self.app.catalog.get(red_id)["analysis"]
        self.encoder.revision = "two"
        second = self.app.scan(self.media, analyze=True, depth="standard")
        self.assertEqual(second["stages"]["semantic_embeddings"]["analyzed"], 2)
        self.assertEqual(second["stages"]["semantic_vision"]["cache_hits"], 2)
        refreshed = self.app.catalog.get(red_id)["analysis"]
        for name in ("metadata", "image_technical", "semantic_vision"):
            self.assertEqual(stored[name], refreshed[name])
        self.vision.revision = "two"
        self.vision.fail = True
        failed = self.app.scan(self.media, analyze=True, stage="semantic")
        self.assertEqual(failed["failed"], 2)
        self.assertEqual(failed["stages"]["semantic_embeddings"]["cache_hits"], 2)
        self.vision.fail = False
        retry = self.app.scan(self.media, analyze=True, stage="semantic")
        self.assertEqual(retry["stages"]["semantic_vision"]["analyzed"], 2)
        self.assertEqual(self.app.catalog.get(red_id)["failures"], [])

    def test_text_and_image_retrieval_share_space_and_reject_old_models(self):
        self.app.scan(self.media, analyze=True, stage="semantic", depth="fast")
        red_id = self.app.catalog.known_path(self.red)["media_id"]
        blue_id = self.app.catalog.known_path(self.blue)["media_id"]
        self.assertEqual(self.app.search(text="red", depth="fast")["items"][0]["media_id"], red_id)
        self.assertEqual(self.app.search(image=self.blue, depth="fast")["items"][0]["media_id"], blue_id)
        self.assertEqual(self.app.search(text="red", depth="standard")["items"], [])
        self.encoder.revision = "other-space"
        self.assertEqual(self.app.search(text="red", depth="fast")["items"], [])
        self.app.scan(self.media, analyze=True, stage="semantic", depth="fast")
        self.assertEqual(self.app.search(text="blue", depth="fast", limit=1)["items"][0]["media_id"], blue_id)

    def test_depth_coverage_and_caption_evidence_preserve_source_times(self):
        scenes = [{"index": i, "start_seconds": 7 + i, "end_seconds": 8 + i,
                   "representative_frame": {"path": str(self.red), "timestamp_seconds": 7.5 + i}}
                  for i in range(11)]
        for depth, indices in (("fast", [5]), ("standard", [0, 1, 2, 4, 5, 7, 8, 10]), ("deep", list(range(11)))):
            stage = SemanticExtractor(self.vision, depth, "vision", {"scenes": scenes}, "technical-key")
            result = stage.extract(self.red)
            self.assertEqual([item["evidence"]["scene_index"] for item in result["items"]], indices)
            self.assertEqual(result["coverage"], {"selected_frames": len(indices), "available_frames": 11})
            for item in result["items"]:
                self.assertEqual(item["evidence"]["start_seconds"], 7 + item["evidence"]["scene_index"])
                self.assertEqual(item["interpretation"], "model_hypothesis")
                self.assertIsNone(item["confidence"])

    def test_same_space_dimension_corruption_and_invalid_vectors_fail(self):
        for vector in ([0, 0], [float("nan"), 1], [True, 0], []):
            with self.assertRaises(ValueError):
                normalized(vector)
        row = {"id": "x", "result": {"provenance": {"space": "s", "depth": "fast", "algorithm": "semantics-v1"},
                                    "items": [{"vector": [1, 0, 0], "evidence": {"kind": "image"}}]}}
        with self.assertRaises(ValueError):
            search_results([row], [1, 0], space="s", depth="fast", limit=1)

    def test_ranking_uses_best_frame_unique_media_latest_compatible_and_stable_ties(self):
        def row(media, vectors, space="s"):
            return {"id": media, "result": {"provenance": {"space": space, "depth": "fast", "algorithm": "semantics-v1"},
                    "items": [{"vector": v, "evidence": {"kind": "image"}} for v in vectors]}}
        rows = [row("a", [[0, 1]], "other"), row("b", [[0, 1], [1, 0]]), row("a", [[1, 0]]), row("a", [[-1, 0]])]
        self.assertEqual([x["media_id"] for x in search_results(rows, [1, 0], space="s", depth="fast", limit=2)], ["a", "b"])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_real_video_technical_dependency_cache_and_failed_force(self):
        self.red.unlink()
        self.blue.unlink()
        video = self.media / "clip.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=blue:s=80x60:r=5", "-t", "1",
                        "-c:v", "mpeg4", str(video)], check=True)
        first = self.app.scan(self.media, analyze=True, stage="semantic", depth="deep")
        self.assertEqual(first["failed"], 0, first)
        mid = self.app.catalog.known_path(video)["media_id"]
        evidence = self.app.search(text="blue", depth="deep")["items"][0]["evidence"]
        self.assertEqual(evidence["kind"], "video_frame")
        self.assertEqual(evidence["scene_index"], 0)
        Path(evidence["representative_frame"]["path"]).unlink()
        regenerated = self.app.scan(self.media, analyze=True, stage="semantic", depth="deep")
        self.assertEqual(regenerated["stages"]["video_technical"]["analyzed"], 1)
        with patch.object(self.app._video_extractor, "extract", side_effect=ValueError("decode failed")):
            failed = self.app.scan(self.media, analyze=True, stage="semantic", depth="deep", force=True)
        self.assertEqual(failed["stages"]["semantic_embeddings"]["failed"], 1)
        self.assertEqual(failed["stages"]["semantic_vision"]["failed"], 1)


if __name__ == "__main__":
    unittest.main()
