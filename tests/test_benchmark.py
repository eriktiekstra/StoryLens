"""Measured cache transitions and source/output safety, without timing thresholds."""

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from PIL import Image

from storylens.benchmark import generate, run
from storylens.config import Config
from storylens.service import Analyzer


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "media"
        self.config = Config(self.root / "do-not-use.sqlite3")

    def fixture(self):
        return generate(self.source, images=3, videos=0, audio=0, image_size=16, seed=7)

    def test_seeded_generated_sources_are_distinct_and_reproducible_in_same_runtime(self):
        first = self.fixture()
        second = generate(self.root / "again", images=3, videos=0, audio=0, image_size=16, seed=7)
        hashes = [row["sha256"] for row in first["files"]]
        self.assertEqual(len(set(hashes)), 3)
        self.assertEqual(hashes, [row["sha256"] for row in second["files"]])
        for row in first["files"]:
            self.assertEqual(hashlib.sha256((self.source / row["path"]).read_bytes()).hexdigest(), row["sha256"])

    def test_fresh_catalog_then_warm_cache_preserves_sources_and_existing_catalog(self):
        manifest = self.fixture()
        self.config.database.write_bytes(b"private existing catalog must not be opened")
        result = run(self.config, self.source, workspace=self.root / "measurement", profile=True)
        self.assertEqual(result["status"], "succeeded")
        cold, warm = result["passes"]
        self.assertEqual(cold["summary"]["indexed"], 3)
        self.assertEqual(cold["summary"]["stages"]["image_technical"]["analyzed"], 3)
        self.assertEqual(warm["summary"]["cache_hits"], 3)
        self.assertEqual(warm["summary"]["stages"]["metadata"]["analyzed"], 0)
        self.assertEqual(warm["summary"]["stages"]["image_technical"]["cache_hits"], 3)
        self.assertEqual(self.config.database.read_bytes(), b"private existing catalog must not be opened")
        self.assertEqual(json.loads((self.root / "measurement/benchmark.json").read_text()), result)
        for row in manifest["files"]:
            self.assertEqual(hashlib.sha256((self.source / row["path"]).read_bytes()).hexdigest(), row["sha256"])

    def test_existing_or_in_source_workspace_is_never_reused(self):
        self.fixture()
        existing = self.root / "existing"
        existing.mkdir()
        marker = existing / "keep"
        marker.write_bytes(b"preserved")
        for workspace in (existing, self.source / "output"):
            with self.subTest(workspace=workspace), self.assertRaises(ValueError):
                run(self.config, self.source, workspace=workspace)
        self.assertEqual(marker.read_bytes(), b"preserved")
        self.assertFalse((self.source / "output").exists())
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.fixture()

    def test_corrupt_file_remains_explicit_failure_across_passes_without_hiding_good_cache(self):
        self.fixture()
        (self.source / "bad.png").write_bytes(b"invalid image")
        result = run(self.config, self.source, workspace=self.root / "measurement")
        self.assertEqual((result["status"], result["failed"]), ("failed", 2))
        for row in result["passes"]:
            self.assertEqual(row["summary"]["failed"], 1)
        self.assertEqual(result["passes"][1]["summary"]["cache_hits"], 3)
        self.assertEqual(result["passes"][1]["summary"]["errors"][0]["stage"], "metadata")

    def test_invalid_bounds_fail_before_creating_outputs(self):
        for args in ({"images": -1}, {"images": True}, {"images": 0, "videos": 0, "audio": 0},
                     {"image_size": 0}, {"seconds": 0}, {"seed": -1}):
            output = self.root / "invalid"
            with self.subTest(args=args), self.assertRaises(ValueError):
                generate(output, **args)
            self.assertFalse(output.exists())
        self.fixture()
        for passes in (1, 11, True):
            with self.subTest(passes=passes), self.assertRaises(ValueError):
                run(self.config, self.source, workspace=self.root / "invalid", passes=passes)
            self.assertFalse((self.root / "invalid").exists())


class StorageExhaustionTests(unittest.TestCase):
    def test_sqlite_full_preserves_prior_commit_and_never_caches_failed_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.png"
            Image.new("RGB", (16, 16), (10, 20, 30)).save(source)
            original = source.read_bytes()
            config = Config(root / "catalog.sqlite3")
            with Analyzer(config) as app:
                app.scan(source)
                media_id = app.catalog.known_path(source)["media_id"]
                app.catalog.save(media_id, "metadata", "committed", {"evidence": "preserved"})
                pages = app.catalog.connection.execute("PRAGMA page_count").fetchone()[0]
                app.catalog.connection.execute(f"PRAGMA max_page_count={pages}")
                with self.assertRaisesRegex(sqlite3.OperationalError, "full"):
                    app.catalog.save(media_id, "metadata", "failed", {"payload": "x" * 2_000_000})
                self.assertIsNone(app.catalog.stage_result(media_id, "metadata", "failed"))
            with Analyzer(config) as reopened:
                self.assertEqual(reopened.catalog.stage_result(media_id, "metadata", "committed"),
                                 {"evidence": "preserved"})
                self.assertIsNone(reopened.catalog.stage_result(media_id, "metadata", "failed"))
            self.assertEqual(source.read_bytes(), original)
