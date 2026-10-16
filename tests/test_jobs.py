"""Durable job contracts exercised with generated media and real child processes."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image

from storylens.config import Config
from storylens.jobs import JobCancelled, JobManager, _summary, _worker_environment
from storylens.job_worker import _guard_catalog_writes
from storylens.storage import Catalog


TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.media = self.root / "media"
        self.media.mkdir()
        self.config = Config(self.root / "catalog.sqlite3")
        self.manager = JobManager(self.config, [self.media])
        self.addCleanup(self.stop_workers)

    def stop_workers(self):
        with self.manager.store.connect() as connection:
            rows = connection.execute("SELECT id FROM jobs WHERE status IN ('queued','running','cancel_requested')").fetchall()
        for row in rows:
            self.manager.cancel(row["id"])
        deadline = time.monotonic() + 10
        while rows and time.monotonic() < deadline:
            time.sleep(0.05)
            with self.manager.store.connect() as connection:
                rows = connection.execute("SELECT id FROM jobs WHERE status IN ('queued','running','cancel_requested')").fetchall()

    def images(self, count=1, size=(128, 96)):
        for index in range(count):
            path = self.media / f"photo-{index:04d}.png"
            Image.new("RGB", size, (index % 256, index // 256, 73)).save(path)
        return self.media / "photo-0000.png"

    def wait_for(self, job_id, predicate=None, timeout=45):
        predicate = predicate or (lambda job: job["status"] in TERMINAL)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.manager.status(job_id)
            if predicate(job):
                return job
            if job["status"] in TERMINAL:
                self.fail(f"Job ended before expected checkpoint: {job}")
            time.sleep(0.02)
        self.fail(f"Job did not reach expected state: {self.manager.status(job_id)}")

    def test_detached_job_survives_submitting_process_and_reuses_real_stages(self):
        source = self.images()
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        script = """import json,sys
from pathlib import Path
from storylens.config import Config
from storylens.jobs import JobManager
manager=JobManager(Config(Path(sys.argv[1])),[Path(sys.argv[2])])
print(json.dumps(manager.start(Path(sys.argv[2]))),flush=True)
"""
        # Captured pipes must close when the submitter exits, not when the worker exits.
        submitted = subprocess.run([sys.executable, "-c", script, str(self.config.database), str(self.media)],
                                   text=True, capture_output=True, check=True, timeout=10)
        started = json.loads(submitted.stdout)
        result = self.wait_for(started["id"])
        self.assertEqual(result["status"], "succeeded", result)
        self.assertEqual(result["summary"]["analyzed"], 1)
        self.assertEqual(result["summary"]["stages"]["image_technical"]["analyzed"], 1)
        independent = JobManager(self.config, [self.media])
        self.assertEqual(independent.result(result["id"]), result)
        self.assertEqual(result["progress"]["indexed"], 1)
        self.assertIsNotNone(result["finished_at"])
        cached = self.wait_for(independent.start(self.media)["id"])
        self.assertEqual(cached["summary"]["cache_hits"], 1)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
        if os.name != "nt":
            self.assertEqual(self.manager.store.path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            self.manager.resume(result["id"])

    def test_running_and_queued_cancellation_is_durable(self):
        self.images(100, (768, 512))
        started = self.manager.start(self.media, stage="technical")
        self.wait_for(started["id"], lambda job: job["status"] == "running" and job["progress"]["indexed"] >= 1)
        queued = self.manager.start(self.media, analyze=False)
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(self.manager.cancel(queued["id"])["status"], "cancelled")
        requested = self.manager.cancel(started["id"])
        self.assertIn(requested["status"], {"cancel_requested", "cancelled"})
        result = self.wait_for(started["id"])
        self.assertEqual(result["status"], "cancelled")
        self.assertIsNotNone(result["summary"])
        self.assertGreaterEqual(result["summary"]["indexed"], 1)
        self.assertEqual(JobManager(self.config, [self.media]).status(queued["id"])["status"], "cancelled")
        resumed = self.manager.resume(queued["id"])
        self.assertEqual(resumed["attempt"], 2)
        self.assertEqual(self.wait_for(queued["id"])["status"], "succeeded")

    @unittest.skipUnless(hasattr(signal, "SIGKILL"), "Hard worker-crash fixture requires SIGKILL")
    def test_crash_resume_fences_workers_and_preserves_committed_force_stages(self):
        self.images(100, (768, 512))
        started = self.manager.start(self.media, stage="technical", force=True)
        self.wait_for(started["id"], lambda job: job["status"] == "running" and
                      job["summary"] and job["summary"].get("stages", {}).get("image_technical", {}).get("analyzed", 0) >= 1)
        with self.manager.store.connect() as connection:
            pid = connection.execute("SELECT worker_pid FROM jobs WHERE id=?", (started["id"],)).fetchone()[0]
        # A duplicate launch cannot acquire an already running attempt.
        duplicate = subprocess.run([sys.executable, "-m", "storylens.job_worker",
            str(self.manager.store.path), started["id"], "1"], timeout=10)
        self.assertEqual(duplicate.returncode, 0)
        with self.assertRaises(ValueError):
            self.manager.resume(started["id"])
        os.kill(pid, signal.SIGKILL)
        # Move the persisted deadline rather than making a test wait a production lease.
        with self.manager.store.connect() as connection:
            connection.execute("UPDATE jobs SET lease_until=0 WHERE id=?", (started["id"],))
        interrupted = self.manager.status(started["id"])
        self.assertEqual(interrupted["status"], "interrupted")
        self.assertIn("lease", interrupted["error"])
        with sqlite3.connect(self.config.database) as connection:
            committed = connection.execute("SELECT media_id,analyzed_at FROM stages WHERE stage='image_technical'").fetchall()
        self.assertTrue(committed)

        def resume():
            try:
                return self.manager.resume(started["id"])["attempt"]
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts = list(pool.map(lambda _: resume(), range(2)))
        self.assertEqual(sorted(value for value in attempts if value is not None), [2])
        # The old attempt is fenced even if it is launched again after resume.
        stale = subprocess.run([sys.executable, "-m", "storylens.job_worker",
            str(self.manager.store.path), started["id"], "1"], timeout=10)
        self.assertEqual(stale.returncode, 0)
        result = self.wait_for(started["id"])
        self.assertEqual(result["status"], "succeeded", result)
        self.assertGreaterEqual(result["summary"]["stages"]["image_technical"]["cache_hits"], len(committed))
        with sqlite3.connect(self.config.database) as connection:
            for media_id, timestamp in committed:
                self.assertEqual(connection.execute("SELECT analyzed_at FROM stages WHERE media_id=? AND stage='image_technical'",
                                                    (media_id,)).fetchone()[0], timestamp)

    def test_root_policy_applies_to_every_operation_and_pages(self):
        self.images()
        outside = self.root / "outside"
        outside.mkdir()
        other = JobManager(self.config, [outside])
        with self.assertRaises(ValueError):
            JobManager(self.config, [])
        with self.assertRaises(ValueError):
            self.manager.start(outside)
        with self.assertRaises(ValueError):
            self.manager.start(self.media / ".." / "outside")
        if os.name != "nt":
            (self.media / "escape").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                self.manager.start(self.media / "escape")
        first = self.wait_for(self.manager.start(self.media, analyze=False)["id"])
        second = self.wait_for(self.manager.start(self.media, analyze=False)["id"])
        for operation in (other.status, other.result, other.cancel, other.resume):
            with self.assertRaises(ValueError):
                operation(first["id"])
        self.assertEqual(other.list()["items"], [])
        page = self.manager.list(limit=1)
        self.assertIsNotNone(page["next_cursor"])
        next_page = self.manager.list(limit=1, after=page["next_cursor"])
        self.assertEqual({page["items"][0]["id"], next_page["items"][0]["id"]}, {first["id"], second["id"]})
        self.assertIsNone(next_page["next_cursor"])
        for limit in (0, 201, True):
            with self.assertRaises(ValueError):
                self.manager.list(limit=limit)
        with self.assertRaises(ValueError):
            self.manager.list(after="not-a-cursor")
        with self.assertRaises(KeyError):
            self.manager.status("missing")

    def test_worker_reauthorizes_persisted_approved_roots(self):
        self.images()
        outside = self.root / "outside"
        outside.mkdir()
        with patch.object(self.manager, "_launch"):
            started = self.manager.start(self.media)
        with self.manager.store.connect() as connection:
            connection.execute("UPDATE jobs SET roots=? WHERE id=?", (json.dumps([str(outside)]), started["id"]))
        subprocess.run([sys.executable, "-m", "storylens.job_worker", str(self.manager.store.path),
                        started["id"], "1"], check=True, timeout=10)
        result = self.manager.result(started["id"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("outside approved", result["error"])
        self.assertFalse(self.config.database.exists())

    def test_stage_failures_and_fatal_errors_are_persisted(self):
        self.images()
        (self.media / "broken.png").write_bytes(b"not an image")
        result = self.wait_for(self.manager.start(self.media)["id"])
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["error"], "Analysis completed with 1 failed files")
        self.assertEqual(result["summary"]["failed"], 1)
        self.assertGreater(result["summary"]["analyzed"], 0)
        self.assertTrue(result["summary"]["errors"])
        self.assertTrue(result["summary"]["stages"]["image_technical"]["failed"])
        Image.new("RGB", (96, 64), (12, 34, 56)).save(self.media / "broken.png")
        resumed = self.manager.resume(result["id"])
        self.assertEqual(resumed["attempt"], 2)
        recovered = self.wait_for(result["id"])
        self.assertEqual(recovered["status"], "succeeded", recovered)
        self.assertIsNone(recovered["error"])
        self.assertEqual(recovered["summary"]["failed"], 0)
        self.assertGreaterEqual(recovered["summary"]["cache_hits"], 1)
        invalid_database = self.root / "invalid.sqlite3"
        invalid_database.mkdir()
        broken = JobManager(replace(self.config, database=invalid_database), [self.media])
        failed = broken.start(self.media)
        deadline = time.monotonic() + 15
        while failed["status"] not in TERMINAL and time.monotonic() < deadline:
            time.sleep(0.02)
            failed = broken.status(failed["id"])
        self.assertEqual(failed["status"], "failed", failed)
        self.assertIn("database", failed["error"])
        self.assertEqual(JobManager(replace(self.config, database=invalid_database), [self.media]).result(failed["id"]), failed)

    def test_summary_and_cancellation_exception_are_bounded(self):
        self.assertFalse(issubclass(JobCancelled, Exception))
        summary = _summary({"indexed": 10, "errors": [{"path": "p" * 10000,
            "stage": "s" * 10000, "error": "e" * 10000}] * 200, "stages": {}})
        self.assertLess(len(json.dumps(summary)), 256 * 1024)
        self.assertEqual(summary["indexed"], 10)
        self.assertEqual(len(summary["errors"]), 100)
        self.assertTrue(summary["errors_truncated"])

    def test_worker_environment_discards_credentials_and_telemetry(self):
        with patch.dict(os.environ, {"PATH": "/tools", "PYTHONPATH": "/source",
                "SystemRoot": "C:\\Windows", "LC_ALL": "C.UTF-8",
                "OPENAI_API_KEY": "secret", "HF_TOKEN": "secret",
                "AWS_SECRET_ACCESS_KEY": "secret", "OTEL_EXPORTER_OTLP_ENDPOINT": "http://exporter",
                "STORYLENS_DATABASE": "/wrong/catalog"}, clear=True):
            actual = {key.upper(): value for key, value in _worker_environment().items()}
            self.assertEqual(actual, {"PATH": "/tools", "PYTHONPATH": "/source",
                "SYSTEMROOT": r"C:\Windows", "LC_ALL": "C.UTF-8"})

    def test_queued_worker_executes_after_previous_job_finishes(self):
        self.images(50, (768, 512))
        first = self.manager.start(self.media, stage="technical")
        self.wait_for(first["id"], lambda job: job["status"] == "running")
        second = self.manager.start(self.media, analyze=False)
        self.assertEqual(second["status"], "queued")
        self.assertEqual(self.wait_for(first["id"])["status"], "succeeded")
        self.assertEqual(self.wait_for(second["id"])["status"], "succeeded")
        self.assertEqual(self.manager.result(second["id"])["summary"]["indexed"], 50)

    def test_stale_owner_cannot_commit_catalog_observations_stages_or_failures(self):
        source = self.images()
        complete = self.wait_for(self.manager.start(self.media)["id"])
        job_id = complete["id"]
        with self.manager.store.connect() as connection:
            connection.execute("UPDATE jobs SET status='running',token='old-owner',lease_until=? WHERE id=?",
                               (time.time() + 30, job_id))
        catalog = Catalog(self.config.database)
        self.addCleanup(catalog.close)
        media_id = catalog.known_path(source)["media_id"]
        lost = threading.Event()
        _guard_catalog_writes(catalog, self.manager.store, job_id, 1, "old-owner", lost)
        # Establish that the current owner can commit normally.
        catalog.save(media_id, "test_stage", "cache", {"owner": "old"})
        with self.manager.store.connect() as connection:
            connection.execute("UPDATE jobs SET token='new-owner',attempt=2 WHERE id=?", (job_id,))
        for operation in (
                lambda: catalog.observe("sha256:stale", source, "image", source.stat()),
                lambda: catalog.save(media_id, "test_stage", "cache", {"owner": "stale"}),
                lambda: catalog.fail(media_id, "test_stage", "stale failure")):
            with self.assertRaises(JobCancelled):
                operation()
        self.assertEqual(catalog.known_path(source)["media_id"], media_id)
        self.assertEqual(catalog.stage_result(media_id, "test_stage", "cache"), {"owner": "old"})
        self.assertEqual(catalog.get(media_id)["failures"], [])
        with self.manager.store.connect() as connection:
            connection.execute("UPDATE jobs SET status='cancelled',token=NULL WHERE id=?", (job_id,))

if __name__ == "__main__":
    unittest.main()
