"""Real media, durable curation, bounded pagination and CLI consumer behavior."""

import hashlib
import importlib.util
from importlib.resources import files
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

from storylens.config import Config
from storylens.service import Analyzer


class CurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.media = self.root / "media"
        self.media.mkdir()
        self.config = Config(self.root / "catalog.sqlite3")
        self.paths = []
        for index, capture in enumerate(("2025:06:01 12:00:00", "2025:06:01 12:15:00",
                                         "2025:06:02 12:00:00", None)):
            path = self.media / f"photo-{index}.jpg"
            exif = Image.Exif()
            if capture:
                exif[36867] = capture
            Image.new("RGB", (32, 24), (index * 60, 20, 80)).save(path, exif=exif)
            self.paths.append(path)
        self.original = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.paths}
        with Analyzer(self.config) as app:
            result = app.scan(self.media, analyze=True)
            self.assertEqual(result["failed"], 0, result)
            self.ids = [app.catalog.known_path(path)["media_id"] for path in self.paths]

    def test_coarse_candidates_member_pages_and_snapshot_invalidation(self):
        with Analyzer(self.config) as app:
            first = app.events(limit=1)
            summaries = list(first["items"])
            after = first["next_cursor"]
            while after:
                page = app.events(limit=1, after=after)
                self.assertEqual(page["snapshot"], first["snapshot"])
                summaries.extend(page["items"])
                after = page["next_cursor"]
            self.assertEqual(len(summaries), 3)
            burst = next(event for event in summaries if event["member_count"] == 2)
            self.assertEqual(burst["title"], "2025-06-01")
            self.assertEqual(burst["timezone_status"], "timezone_ambiguous")
            self.assertTrue(burst["warnings"])
            boundaries = burst["title_provenance"]["sources"]
            self.assertEqual({row["media_id"] for row in boundaries}, set(self.ids[:2]))
            members = app.event(burst["id"], limit=1)
            second = app.event(burst["id"], limit=1, after=members["next_cursor"])
            self.assertEqual({row["media_id"] for row in members["items"] + second["items"]}, set(self.ids[:2]))
            self.assertIsNone(second["next_cursor"])
            other = next(event for event in summaries if event["id"] != burst["id"])
            with self.assertRaisesRegex(ValueError, "cursor"):
                app.event(other["id"], after=members["next_cursor"])
            with self.assertRaisesRegex(ValueError, "cursor"):
                app.events(after=members["next_cursor"])
            app.set_feedback(self.ids[0], {"selection": "exclude"})
            with self.assertRaisesRegex(ValueError, "cursor"):
                app.events(limit=1, after=first["next_cursor"])
            with self.assertRaisesRegex(ValueError, "cursor"):
                app.event(burst["id"], limit=1, after=members["next_cursor"])
            retained = app.event(burst["id"])
            self.assertEqual(retained["event"]["member_count"], 2)
            excluded = next(row for row in retained["items"] if row["media_id"] == self.ids[0])
            self.assertEqual(excluded["selection"], "exclude")
            fresh = app.events(limit=1)
            app.set_preferences({"gap_seconds": 600})
            with self.assertRaisesRegex(ValueError, "cursor"):
                app.events(limit=1, after=fresh["next_cursor"])
            self.assertEqual(len(app.events()["items"]), 4)
            for limit in (0, 501, True):
                with self.subTest(limit=limit), self.assertRaises(ValueError):
                    app.events(limit=limit)

    def test_manual_merge_split_and_explicit_unknown_survive_reanalysis_and_moves(self):
        with Analyzer(self.config) as app:
            event = app.create_event("Reviewed family day")
            for media_id in (self.ids[0], self.ids[2]):
                app.set_feedback(media_id, {"event_id": event["id"], "selection": "include"})
            app.rename_event(event["id"], "User-owned title")
            app.set_feedback(self.ids[1], {"capture_time": None, "gps": None, "selection": "exclude"})
            preferences = app.set_preferences({"gap_seconds": 1800, "distance_km": 25})
            before = [app.feedback(media_id) for media_id in self.ids]
            result = app.scan(self.media, analyze=True, force=True)
            self.assertEqual(result["failed"], 0, result)
            self.assertEqual([app.feedback(media_id) for media_id in self.ids], before)
            self.assertEqual(app.preferences(), preferences)
            merged = app.event(event["id"])
            self.assertEqual(merged["event"]["title"], "User-owned title")
            self.assertEqual({row["media_id"] for row in merged["items"]}, {self.ids[0], self.ids[2]})
            self.assertEqual(merged["event"]["title_provenance"], {"kind": "user", "event_id": event["id"]})
        moved = self.media / "renamed.jpg"
        self.paths[1].rename(moved)
        with Analyzer(self.config) as app:
            app.scan(moved, analyze=True)
            self.assertEqual(app.catalog.known_path(moved)["media_id"], self.ids[1])
            self.assertEqual(app.feedback(self.ids[1]), before[1])
            undated = next(row for row in app.events()["items"] if row["kind"] == "undated")
            corrected = next(row for row in app.event(undated["id"])["items"] if row["media_id"] == self.ids[1])
            self.assertTrue(corrected["evidence"]["capture_time"]["explicit_unknown"])
            app.reset_feedback(self.ids[1], ["capture_time"])
            self.assertEqual(app.feedback(self.ids[1])["values"], {"gps": None, "selection": "exclude"})
            restored = next(row for event_row in app.events()["items"]
                            for row in app.event(event_row["id"])["items"] if row["media_id"] == self.ids[1])
            self.assertEqual(restored["evidence"]["capture_time"]["value"], "2025-06-01T12:15:00")
            split = app.create_event("Second reviewed day")
            app.set_feedback(self.ids[2], {"event_id": split["id"]})
            self.assertEqual(app.event(event["id"])["event"]["member_count"], 1)
            self.assertEqual(app.event(split["id"])["items"][0]["media_id"], self.ids[2])
            app.reset_feedback(self.ids[2], ["event_id"])
            with self.assertRaisesRegex(ValueError, "Unknown event"):
                app.event(split["id"])
            self.assertEqual(app.feedback(self.ids[2])["values"], {"selection": "include"})
        for path, digest in self.original.items():
            actual = moved if path == self.paths[1] else path
            self.assertEqual(hashlib.sha256(actual.read_bytes()).hexdigest(), digest)

    @unittest.skipUnless(importlib.util.find_spec("jsonschema"), "optional jsonschema required")
    def test_packaged_schema_validates_real_curation_envelopes(self):
        import jsonschema
        schema = json.loads(files("storylens").joinpath("schemas/curation-v1.schema.json").read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        with Analyzer(self.config) as app:
            page = app.events()
            responses = [page, app.preferences(), app.set_preferences({"semantic_similarity": 0.9}),
                         app.create_event("Manual title"), app.feedback(self.ids[0]),
                         app.set_feedback(self.ids[0], {"capture_time": None, "gps": None}),
                         app.reset_feedback(self.ids[0])]
            responses.extend(app.event(row["id"]) for row in page["items"])
            for response in responses:
                jsonschema.validate(response, schema)
            invalid = {**page, "items": [{**page["items"][0], "timezone_status": "guessed"}]}
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(invalid, schema)

    def cli(self, *arguments, success=True):
        result = subprocess.run([sys.executable, "-m", "storylens", "--database", str(self.config.database),
                                 *arguments], capture_output=True, text=True, timeout=30)
        if not success:
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertNotIn("Traceback", result.stderr)
            return result
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_cli_feedback_event_and_preference_workflow(self):
        page = self.cli("events", "--limit", "1")
        second = self.cli("events", "--limit", "1", "--after", page["next_cursor"])
        self.assertNotEqual(page["items"][0]["id"], second["items"][0]["id"])
        manual = self.cli("event-create", "Reviewed")
        changed = self.cli("feedback", self.ids[0], "--set", json.dumps({"event_id": manual["id"], "selection": "include"}))
        self.assertEqual(changed["values"]["selection"], "include")
        self.cli("event-rename", manual["id"], "Renamed by CLI")
        detail = self.cli("event", manual["id"])
        self.assertEqual(detail["event"]["title"], "Renamed by CLI")
        self.assertEqual(detail["items"][0]["media_id"], self.ids[0])
        self.cli("feedback", self.ids[0], "--set", '{"capture_time":null}')
        reset = self.cli("feedback", self.ids[0], "--clear", "capture_time")
        self.assertNotIn("capture_time", reset["values"])
        self.assertEqual(reset["values"]["event_id"], manual["id"])
        self.assertEqual(self.cli("feedback", self.ids[0], "--clear-all")["values"], {})
        self.cli("preferences", "--set", '{"gap_seconds":600}')
        self.assertEqual(self.cli("preferences")["values"]["gap_seconds"], 600)
        self.assertEqual(len(self.cli("events")["items"]), 4)
        self.cli("feedback", self.ids[0], "--set", '{"unknown":true}', success=False)
        self.cli("preferences", "--set", '{"gap_seconds":true}', success=False)
        self.cli("event", "unknown-event", success=False)
        for path, digest in self.original.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
