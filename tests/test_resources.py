"""Cross-process CUDA scheduling contracts without models or GPU hardware."""

from contextlib import contextmanager
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from storylens.audio_providers import AudioModelExtractor
from storylens.resources import _cuda_lock_path, cuda_inference_lock


@contextmanager
def _cache(directory):
    # Cover each platform's actual cache selection, including macOS's home cache.
    with patch.dict(os.environ, {"XDG_CACHE_HOME": directory, "LOCALAPPDATA": directory}), \
            patch("storylens.resources.Path.home", return_value=Path(directory)):
        yield


def _owner(directory, channel, release, timeout=5):
    try:
        with _cache(directory):
            channel.send(("waiting", time.monotonic()))
            with cuda_inference_lock(timeout):
                channel.send(("entered", time.monotonic()))
                if not release.wait(15):
                    raise RuntimeError("Test owner did not receive release")
            channel.send(("exited", time.monotonic()))
    except Exception as error:
        channel.send(("error", str(error)))
    finally:
        channel.close()


class CudaResourceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.context = multiprocessing.get_context("spawn")
        self.processes = []
        self.addCleanup(self.stop_processes)

    def stop_processes(self):
        for process, release in self.processes:
            # A terminated process can die while holding Event's condition lock.
            # Never touch its synchronization primitive after joining that owner.
            if process.is_alive():
                release.set()
        for process, _ in self.processes:
            process.join(10)
            if process.is_alive():
                process.terminate()
                process.join(5)
            self.assertFalse(process.is_alive())
            process.close()

    def owner(self, timeout=5):
        receive, send = self.context.Pipe(duplex=False)
        self.addCleanup(receive.close)
        release = self.context.Event()
        process = self.context.Process(target=_owner,
            args=(self.directory.name, send, release, timeout))
        process.start()
        send.close()
        self.processes.append((process, release))
        self.assertEqual(self.message(receive)[0], "waiting")
        return process, receive, release

    def message(self, channel):
        self.assertTrue(channel.poll(15), "Resource worker did not respond")
        return channel.recv()

    def test_distinct_processes_cannot_overlap_critical_sections(self):
        _, first, release_first = self.owner()
        self.assertEqual(self.message(first)[0], "entered")
        _, second, release_second = self.owner()
        self.assertFalse(second.poll(0.2), "Second process entered an owned CUDA resource")
        release_first.set()
        self.assertEqual(self.message(second)[0], "entered")
        self.assertEqual(self.message(first)[0], "exited")
        release_second.set()
        self.assertEqual(self.message(second)[0], "exited")

    def test_terminated_owner_releases_os_lock_without_stale_file_cleanup(self):
        owner, first, _ = self.owner()
        self.assertEqual(self.message(first)[0], "entered")
        owner.terminate()
        owner.join(5)
        self.assertFalse(owner.is_alive())
        _, successor, release = self.owner()
        self.assertEqual(self.message(successor)[0], "entered")
        release.set()
        self.assertEqual(self.message(successor)[0], "exited")
        with _cache(self.directory.name):
            path = _cuda_lock_path()
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(), b"")

    def test_wait_timeout_is_bounded_and_does_not_steal_resource(self):
        _, first, release = self.owner()
        self.assertEqual(self.message(first)[0], "entered")
        contender, second, _ = self.owner(timeout=0.1)
        message = self.message(second)
        self.assertEqual(message[0], "error")
        self.assertIn("configured timeout", message[1])
        contender.join(5)
        self.assertEqual(contender.exitcode, 0)
        self.assertFalse(first.poll(0.1), "Owner exited while its resource was contested")
        release.set()
        self.assertEqual(self.message(first)[0], "exited")

    def test_private_persistent_global_key_and_reacquisition(self):
        with _cache(self.directory.name):
            with cuda_inference_lock(1):
                path = _cuda_lock_path()
                self.assertTrue(path.is_file())
                if os.name != "nt":
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            with patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": "7"}):
                with cuda_inference_lock(1):
                    self.assertEqual(_cuda_lock_path(), path)
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(), b"")

    def test_cpu_worker_runs_while_cuda_worker_times_out_without_launch(self):
        marker = Path(self.directory.name) / "inference-started"
        real_popen = subprocess.Popen
        def spawn(command, **kwargs):
            return real_popen([sys.executable, "-c",
                "from pathlib import Path; import sys; "
                "Path(sys.argv[1]).write_text('ran'); print('{}')", str(marker)], **kwargs)
        with _cache(self.directory.name), cuda_inference_lock(2), \
                patch("storylens.audio_providers.subprocess.Popen", side_effect=spawn):
            cuda = AudioModelExtractor("sound", self.directory.name, device="cuda", timeout=0.1)
            with self.assertRaisesRegex(ValueError, "resource wait exceeded configured timeout"):
                cuda._run_worker(Path("unused.wav"))
            self.assertFalse(marker.exists(), "CUDA inference started without owning the resource")
            cpu = AudioModelExtractor("sound", self.directory.name, device="cpu", timeout=5)
            self.assertEqual(cpu._run_worker(Path("unused.wav")), {})
            self.assertEqual(marker.read_text(), "ran")

    def test_exceptions_release_lock_and_invalid_timeouts_do_not_create_state(self):
        with _cache(self.directory.name):
            for timeout in (0, -1, True, float("inf"), float("nan"), "1"):
                with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                    with cuda_inference_lock(timeout):
                        self.fail("Invalid timeout acquired the lock")
            self.assertFalse((Path(self.directory.name) / "storylens").exists())
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                with cuda_inference_lock(1):
                    raise RuntimeError("cancelled")
            with cuda_inference_lock(1):
                pass


if __name__ == "__main__":
    unittest.main()
