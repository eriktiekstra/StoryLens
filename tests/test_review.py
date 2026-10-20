"""Local contact sheets preserve source orientation, privacy and prior artifacts."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from storylens.config import Config
from storylens.review import contact_sheets, _thumbnail_source
from storylens.workflow import Project


class ContactSheetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.sources = self.root / "media"
        self.sources.mkdir()
        self.source = self.sources / "portrait.jpg"
        exif = Image.Exif()
        exif[274] = 6
        exif[305] = "private source metadata"
        with Image.new("RGB", (120, 240), "red") as image:
            image.paste((0, 200, 0), (0, 120, 120, 240))
            image.save(self.source, exif=exif)
        self.original = self.source.read_bytes()
        self.project = Project.create(self.root / "project", self.sources,
                                      Config(self.root / "unrelated.sqlite3"))
        self.project.analyze()
        self.document = self.project.recommend(duration_seconds=4)

    def test_oriented_visual_pixels_are_rendered_without_source_metadata_or_overwrite(self):
        first, = contact_sheets(self.project, self.document)
        original_sheet = first.read_bytes()
        with Image.open(first) as sheet:
            self.assertEqual(sheet.format, "JPEG")
            self.assertEqual(dict(sheet.getexif()), {})
            # EXIF orientation6 rotates the lower green half to the left.
            left, right = sheet.getpixel((40, 100)), sheet.getpixel((200, 100))
            self.assertGreater(left[1], 150)
            self.assertLess(left[0], 60)
            self.assertGreater(right[0], 180)
            self.assertLess(right[1], 60)
        second, = contact_sheets(self.project, self.document)
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), original_sheet)
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertFalse((self.root / "unrelated.sqlite3").exists())

    def test_stale_review_document_is_rejected_before_rendering(self):
        existing, = contact_sheets(self.project, self.document)
        previous = existing.read_bytes()
        old = deepcopy(self.document)
        changed = deepcopy(self.document)
        changed["title"] = "Changed after review"
        self.project.save_storyboard(changed)
        with self.assertRaises(ValueError):
            contact_sheets(self.project, old)
        self.assertEqual(existing.read_bytes(), previous)
        self.assertEqual(list(existing.parent.iterdir()), [existing])


    def test_foreign_video_artifacts_are_not_accepted_as_preview_sources(self):
        private = self.root / "private.jpg"
        Image.new("RGB", (20, 20), "blue").save(private)
        media = {"analysis": {"video_technical": {"result": {"scenes": [{
            "start_seconds": 0, "end_seconds": 1,
            "representative_frame": {"path": str(private), "source_timestamp_seconds": 0.5},
        }]}}}}
        path, _ = _thumbnail_source(self.project, media, {
            "kind": "video", "start_seconds": 0, "end_seconds": 1})
        self.assertIsNone(path, "A preview must not read foreign artifacts outside its project")

    def test_symlinked_review_output_cannot_write_outside_project(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.project.directory / "review").rmdir()
        try:
            (self.project.directory / "review").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Directory symlink privilege unavailable")
        with self.assertRaises(ValueError):
            contact_sheets(self.project, self.document)
        self.assertEqual(list(outside.iterdir()), [])
