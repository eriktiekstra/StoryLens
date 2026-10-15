"""Behavioral tests for local audio model adapters without requiring local models."""

from importlib.metadata import PackageNotFoundError
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from storylens.audio_providers import AudioModelExtractor, _json, _libraries, _number, _probability


class JsonNumberTests(unittest.TestCase):
    def test_json_rejects_invalid_constants(self):
        self.assertRaises(ValueError, _json, b'{"x": Infinity}')

    def test_json_non_dict_rejected(self):
        self.assertRaises(ValueError, _json, b'[]')

    def test_number_rejects_nan(self):
        self.assertRaises(ValueError, _number, float("nan"), "x")
        self.assertRaises(ValueError, _number, "a", "x")

    def test_probability_bounds(self):
        self.assertEqual(_probability(0.5), 0.5)
        self.assertIsNone(_probability(None))
        self.assertRaises(ValueError, _probability, 1.1)


class LibraryVersionTests(unittest.TestCase):
    def test_libraries_records_none_for_missing_dependency(self):
        with patch("storylens.audio_providers.version") as version:
            def missing(name):
                raise PackageNotFoundError(name)
            version.side_effect = missing
            result = _libraries("transcription")
            self.assertIsNone(result["faster-whisper"])
            version.assert_called()

    def test_libraries_returns_versions_or_none(self):
        result = _libraries("transcription")
        self.assertIn("faster-whisper", result)
        self.assertTrue(result["faster-whisper"] is None or isinstance(result["faster-whisper"], str))


class ConstructorTests(unittest.TestCase):
    def test_invalid_task(self):
        self.assertRaises(ValueError, AudioModelExtractor, "face", "/tmp/x")

    def test_empty_path(self):
        self.assertRaises(ValueError, AudioModelExtractor, "transcription", "")

    def test_invalid_timeout(self):
        self.assertRaises(ValueError, AudioModelExtractor, "transcription", "/tmp/x", timeout=0)
        self.assertRaises(ValueError, AudioModelExtractor, "transcription", "/tmp/x", timeout=float("nan"))

    def test_invalid_max_audio_seconds(self):
        self.assertRaises(ValueError, AudioModelExtractor, "transcription", "/tmp/x", max_audio_seconds=1801)
        self.assertRaises(ValueError, AudioModelExtractor, "transcription", "/tmp/x", max_audio_seconds=0)

    def test_invalid_device(self):
        self.assertRaises(ValueError, AudioModelExtractor, "transcription", "/tmp/x", device="auto")

    def test_missing_model_directory(self):
        extractor = AudioModelExtractor("transcription", "/tmp/nonexistent-audio-model")
        self.assertRaises(ValueError, extractor.provenance)

    def test_missing_required_config(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertRaises(ValueError, AudioModelExtractor("transcription", directory).provenance)


class CacheKeyTests(unittest.TestCase):
    def test_cache_key_changes_with_revision_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "config.json").write_text("{}")
            (path / "a.bin").write_bytes(b"x")
            extractor = AudioModelExtractor("transcription", directory)
            first = extractor.cache_key()
            (path / "a.bin").write_bytes(b"y")
            self.assertNotEqual(AudioModelExtractor("transcription", directory).cache_key(), first)


class ResultValidationTests(unittest.TestCase):
    def _make(self, task="transcription"):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory)
        (Path(directory) / ("config.yaml" if task == "diarization" else "config.json")).write_text("{}")
        return AudioModelExtractor(task, directory)

    def test_transcription_requires_vad_evidence(self):
        extractor = self._make("transcription")
        raw = {"language": "en", "language_probability": 0.9,
               "segments": [{"start_seconds": 0, "end_seconds": 1, "text": "hi", "words": []}],
               "speech_activity": {"enabled": False, "method": "Silero VAD", "intervals": []}}
        self.assertRaises(ValueError, extractor._result, raw,
                          {"origin_seconds": 0, "end_seconds": 1, "duration_seconds": 1,
                           "stream_index": 0, "sample_rate_hz": 16000}, extractor.provenance())

    def test_transcription_intervals_bounded(self):
        extractor = self._make("transcription")
        raw = {"language": "en", "language_probability": 0.9,
               "segments": [{"start_seconds": 0, "end_seconds": 1, "text": "hi", "words": []}],
               "speech_activity": {"enabled": True, "method": "Silero VAD", "intervals": []}}
        timing = {"origin_seconds": 5.0, "end_seconds": 6.0, "duration_seconds": 1.0,
                  "stream_index": 0, "sample_rate_hz": 16000}
        result = extractor._result(raw, timing, extractor.provenance())
        self.assertEqual(result["segments"][0]["start_seconds"], 5.0)
        self.assertEqual(result["speech_activity"]["intervals"], [])

    def test_diarization_turns_are_relabeled(self):
        extractor = self._make("diarization")
        raw = {"turns": [{"start_seconds": 0, "end_seconds": 1, "speaker": "A"},
                         {"start_seconds": 0.5, "end_seconds": 1.5, "speaker": "B"},
                         {"start_seconds": 2, "end_seconds": 3, "speaker": "A"}]}
        result = extractor._result(raw,
                                   {"origin_seconds": 0, "end_seconds": 3, "duration_seconds": 3,
                                    "stream_index": 0, "sample_rate_hz": 16000}, extractor.provenance())
        self.assertEqual(len(result["turns"]), 3)
        self.assertEqual(result["turns"][2]["speaker"], result["turns"][0]["speaker"])
        self.assertNotEqual(result["turns"][1]["speaker"], result["turns"][0]["speaker"])
        self.assertEqual(result["overlap_policy"], "independent turns; overlaps preserved; no transcript assignment")

    def test_sound_scores_disclose_activation(self):
        extractor = self._make("sound")
        raw = {"windows": [{"start_seconds": 0, "end_seconds": 5, "scores": [{"label": "beep", "score": 0.9}]}],
               "score_activation": "sigmoid"}
        result = extractor._result(raw,
                                   {"origin_seconds": 10, "end_seconds": 20, "duration_seconds": 10,
                                    "stream_index": 0, "sample_rate_hz": 16000}, extractor.provenance())
        self.assertEqual(result["windows"][0]["index"], 0)
        self.assertEqual(result["windows"][0]["start_seconds"], 10.0)
        self.assertEqual(result["score_interpretation"], "Uncalibrated model scores, not verified event probabilities")

    def test_out_of_bounds_interval_rejected(self):
        extractor = self._make("diarization")
        raw = {"turns": [{"start_seconds": 0, "end_seconds": 2, "speaker": "A"}]}
        self.assertRaises(ValueError, extractor._result, raw,
                          {"origin_seconds": 0, "end_seconds": 1, "duration_seconds": 1,
                           "stream_index": 0, "sample_rate_hz": 16000}, extractor.provenance())


class WorkerBoundaryTests(unittest.TestCase):
    def _create_wav(self):
        path = Path(self.tmpdir) / "tone.wav"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=1000:duration=1",
                        "-ar", "16000", "-ac", "1", str(path)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return path

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_worker_timeout_kills_child(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "config.json").write_text("{}")
            extractor = AudioModelExtractor("transcription", directory, timeout=0.1)
            wav = self._create_wav()
            # Monkey-patch worker so it sleeps beyond timeout.
            from storylens.audio_worker import _load
            def slow_then_load(req):
                import time
                time.sleep(2)
                return _load(req)
            with patch("storylens.audio_worker._load", side_effect=slow_then_load):
                self.assertRaises(ValueError, extractor._run_worker, wav)


if __name__ == "__main__":
    unittest.main()
