"""Content-addressed portable selections fail closed when required sources disappear."""

from pathlib import Path
import tempfile
import unittest

from PIL import Image

from storylens.config import Config
from storylens.service import Analyzer


class StoryboardSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.media = self.root / "media"
        self.media.mkdir()
        self.source = self.media / "photo.png"
        Image.new("RGB", (8, 8), (20, 30, 40)).save(self.source)
        self.config = Config(self.root / "catalog.sqlite3")
        with Analyzer(self.config) as app:
            app.scan(self.media)
            self.media_id = app.catalog.known_path(self.source)["media_id"]
            app.set_feedback(self.media_id, {"selection": "include"})

    def test_missing_required_source_is_not_silently_dropped(self):
        self.source.unlink()
        with Analyzer(self.config) as app:
            with self.assertRaisesRegex(ValueError, "Required media.*source"):
                app.recommend()

    def test_portable_storyboard_survives_rescanned_move_but_not_new_exclusion(self):
        with Analyzer(self.config) as app:
            document = app.recommend(duration_seconds=4)
        self.source.rename(self.media / "moved.png")
        with Analyzer(self.config) as app:
            app.scan(self.media)
            validated = app.validate_storyboard(document)
            self.assertEqual([entry["media_id"] for entry in validated["items"]], [self.media_id])
            app.set_feedback(self.media_id, {"selection": "exclude"})
            with self.assertRaises(ValueError):
                app.validate_storyboard(document)
