"""Media-root authorization and cancellation at committed service boundaries."""

from pathlib import Path
import tempfile
import unittest

from PIL import Image

from storylens.access import PathPolicy
from storylens.config import Config
from storylens.service import Analyzer


class AccessTests(unittest.TestCase):
    def test_explicit_roots_reject_sibling_prefix_and_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            allowed, outside = base / "media", base / "media-private"
            allowed.mkdir()
            outside.mkdir()
            source = allowed / "frame.png"
            Image.new("RGB", (16, 16), "red").save(source)
            policy = PathPolicy([allowed])
            self.assertEqual(policy.authorize(source), source.resolve())
            for path in (outside, allowed / ".." / "media-private"):
                with self.subTest(path=path), self.assertRaisesRegex(ValueError, "outside approved"):
                    policy.authorize(path)
            with self.assertRaises(ValueError):
                PathPolicy([])
            with self.assertRaises(ValueError):
                PathPolicy([source])

    def test_symlink_cannot_escape_approved_root(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            allowed, outside = base / "media", base / "private"
            allowed.mkdir()
            outside.mkdir()
            link = allowed / "escape"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"Symlink creation unavailable: {error}")
            with self.assertRaisesRegex(ValueError, "outside approved"):
                PathPolicy([allowed]).authorize(link)

    def test_cancel_after_commit_keeps_completed_stage_and_resumes_from_cache(self):
        class Cancelled(BaseException):
            pass

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "frame.png"
            Image.new("RGB", (24, 24), "red").save(source)
            with Analyzer(Config(database=base / "catalog.sqlite3")) as analyzer:
                def checkpoint(summary):
                    if summary["stages"].get("metadata", {}).get("analyzed") == 1:
                        raise Cancelled()
                with self.assertRaises(Cancelled):
                    analyzer.scan(source, analyze=True, checkpoint=checkpoint)
                media_id = analyzer.catalog.page()["items"][0]["id"]
                stored = analyzer.catalog.get(media_id)
                self.assertEqual(set(stored["analysis"]), {"metadata"})
                resumed = analyzer.scan(source, analyze=True)
                self.assertEqual(resumed["stages"]["metadata"]["cache_hits"], 1)
                self.assertEqual(resumed["stages"]["image_technical"]["analyzed"], 1)
                self.assertEqual(resumed["failed"], 0)
