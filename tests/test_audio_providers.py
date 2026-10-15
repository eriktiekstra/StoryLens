"""Behavioral tests for local audio model adapters without requiring local models."""

from importlib.metadata import PackageNotFoundError
from pathlib import Path
from contextlib import nullcontext
import importlib.util
import shutil
import sys
from types import SimpleNamespace
import wave
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

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
               "segments": [{"start_seconds": 0, "end_seconds": 1, "text": "hi", "words": [
                   {"start_seconds": 0.2, "end_seconds": 0.8, "text": "hi", "probability": 0.42}]}],
               "speech_activity": {"enabled": True, "method": "Silero VAD", "intervals": [
                   {"start_seconds": 0.1, "end_seconds": 0.9}]}}
        timing = {"origin_seconds": 5.0, "end_seconds": 6.0, "duration_seconds": 1.0,
                  "stream_index": 0, "sample_rate_hz": 16000}
        result = extractor._result(raw, timing, extractor.provenance())
        self.assertEqual(result["segments"][0]["start_seconds"], 5.0)
        self.assertEqual(result["segments"][0]["words"][0]["start_seconds"], 5.2)
        self.assertEqual(result["segments"][0]["words"][0]["probability"], 0.42)
        self.assertEqual(result["speech_activity"]["intervals"], [{"start_seconds": 5.1, "end_seconds": 5.9}])

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
                                   {"origin_seconds": 10, "end_seconds": 15, "duration_seconds": 5,
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

    def test_sound_rejects_coverage_holes(self):
        extractor = self._make("sound")
        raw = {"windows": [{"start_seconds": 0, "end_seconds": 1,
                            "scores": [{"label": "beep", "score": 0.2}]}], "score_activation": "softmax"}
        with self.assertRaisesRegex(ValueError, "complete decoded source"):
            extractor._result(raw, {"origin_seconds": 0, "end_seconds": 2, "duration_seconds": 2}, {})


class WorkerBoundaryTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def run_program(self, program, *, timeout=1):
        real_popen = subprocess.Popen
        self.processes = []
        def spawn(command, **kwargs):
            process = real_popen([sys.executable, "-c", program], **kwargs)
            self.processes.append(process)
            return process
        extractor = AudioModelExtractor("transcription", self.tmpdir, timeout=timeout)
        return extractor, patch("storylens.audio_providers.subprocess.Popen", side_effect=spawn)

    def test_worker_timeout_kills_and_reaps_actual_child(self):
        extractor, spawn = self.run_program("import time; time.sleep(20)", timeout=0.05)
        with spawn, self.assertRaisesRegex(ValueError, "timeout"):
            extractor._run_worker(Path("unused.wav"))
        self.assertIsNotNone(self.processes[0].poll())

    def test_worker_bounds_actual_output(self):
        extractor, spawn = self.run_program("import os; os.write(1, b'x' * 10000)")
        with spawn, patch("storylens.audio_providers.MAX_RESPONSE_BYTES", 100), self.assertRaisesRegex(ValueError, "bounded output"):
            extractor._run_worker(Path("unused.wav"))
        self.assertIsNotNone(self.processes[0].poll())

    def test_worker_returns_explicit_model_errors(self):
        extractor, spawn = self.run_program("import json; print(json.dumps(dict(error='CUDA unavailable')))")
        with spawn, self.assertRaisesRegex(ValueError, "CUDA unavailable"):
            extractor._run_worker(Path("unused.wav"))

    def test_cancellation_kills_and_reaps_actual_child(self):
        extractor, spawn = self.run_program("import time; time.sleep(20)")
        real_popen = subprocess.Popen
        processes = []
        def interrupt(command, **kwargs):
            process = real_popen([sys.executable, "-c", "import time; time.sleep(20)"], **kwargs)
            wait = process.wait
            count = 0
            def cancelled(*args, **kwargs):
                nonlocal count
                count += 1
                if count == 1:
                    raise KeyboardInterrupt()
                return wait(*args, **kwargs)
            process.wait = cancelled
            processes.append(process)
            return process
        with patch("storylens.audio_providers.subprocess.Popen", side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            extractor._run_worker(Path("unused.wav"))
        self.assertIsNotNone(processes[0].poll())

    def test_audit_hook_denies_real_loopback_connect_and_resolution(self):
        for action in ("socket.socket().connect(('127.0.0.1', 80))", "socket.getaddrinfo('example.com', 80)"):
            with self.subTest(action=action):
                program = "from storylens.audio_worker import _offline; _offline(); import socket; " + action
                process = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=5)
                self.assertNotEqual(process.returncode, 0)
                self.assertIn("network access denied", process.stderr)

    def test_missing_dependencies_fail_before_decoding(self):
        extractor = AudioModelExtractor("sound", self.tmpdir)
        with patch.object(extractor, "provenance", return_value={"libraries": {"torch": None}}), self.assertRaisesRegex(ValueError, "Missing optional.*torch"):
            extractor.extract(Path("does-not-exist.wav"))

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "optional numpy not installed")
    def test_pcm_reader_skips_wav_header_and_preserves_sample_count(self):
        from storylens.audio_worker import _read_pcm16
        path = Path(self.tmpdir) / "samples.wav"
        with wave.open(str(path), "wb") as stream:
            stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            stream.writeframes(b'\x00\x00\xff\x7f\x00\x80')
        samples = _read_pcm16(path)
        self.assertEqual(len(samples), 3)
        self.assertEqual(samples[0], 0)
        self.assertAlmostEqual(samples[1], 32767 / 32768)
        self.assertEqual(samples[2], -1)


class AdapterInferenceTests(unittest.TestCase):
    def test_whisper_consumes_generator_and_preserves_raw_probabilities_and_vad(self):
        from storylens.audio_worker import _transcribe, _Budget
        word = SimpleNamespace(start=0.2, end=0.7, word=" hello", probability=0.42)
        segment = SimpleNamespace(start=0, end=1, text=" hello", words=[word])
        model = Mock()
        consumed = []
        def segments():
            consumed.append(True)
            yield segment
        model.transcribe.return_value = (segments(), SimpleNamespace(language="en", language_probability=0.63))
        whisper = SimpleNamespace(WhisperModel=Mock(return_value=model))
        vad = SimpleNamespace(VadOptions=lambda: None,
                              get_speech_timestamps=lambda audio, options: [{"start": 1600, "end": 12800}])
        with tempfile.TemporaryDirectory() as directory:
            for name in ("model.bin", "tokenizer.json", "config.json"):
                (Path(directory) / name).touch()
            modules = {"ctranslate2": SimpleNamespace(), "faster_whisper": whisper, "faster_whisper.vad": vad}
            with patch.dict(sys.modules, modules):
                result = _transcribe({"model_path": directory, "device": "cpu", "language": ""}, [0] * 16000, _Budget())
        self.assertTrue(consumed)
        self.assertEqual(result["language_probability"], 0.63)
        self.assertEqual(result["segments"][0]["words"][0]["probability"], 0.42)
        self.assertEqual(result["speech_activity"]["intervals"], [{"start_seconds": 0.1, "end_seconds": 0.8}])
        self.assertEqual(whisper.WhisperModel.call_args.kwargs["compute_type"], "int8")

    def test_whisper_missing_tokenizer_fails_before_native_loader(self):
        from storylens.audio_worker import _transcribe, _Budget
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "model.bin").touch()
            with self.assertRaisesRegex(ValueError, "tokenizer.json"):
                _transcribe({"model_path": directory}, [], _Budget())

    def test_worker_segment_budget_fails_without_partial_success(self):
        from storylens.audio_worker import _Budget
        budget, values = _Budget(), []
        with patch("storylens.audio_worker.MAX_SEGMENTS", 1):
            budget.append(values, {})
            with self.assertRaisesRegex(ValueError, "bounded payload"):
                budget.append(values, {})

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "optional numpy not installed")
    def test_sound_resampling_labels_scores_and_tiny_final_window(self):
        import numpy as np
        from storylens.audio_worker import _sound, _Budget
        tensor = Mock()
        tensor.to.return_value = tensor
        calls = []
        class Features:
            sampling_rate = 8000
            def __call__(self, audio, **kwargs):
                calls.append(len(audio))
                return {"input_values": tensor}
        scores = Mock()
        scores.numel.return_value = 2
        scores.sigmoid.return_value = scores
        scores.topk.return_value = SimpleNamespace(values=SimpleNamespace(tolist=lambda: [0.75, 0.25]),
                                                   indices=SimpleNamespace(tolist=lambda: [1, 0]))
        model = Mock()
        model.to.return_value = model
        model.eval.return_value = model
        model.config = SimpleNamespace(problem_type="multi_label_classification", id2label={0: "silence", 1: "bird"})
        model.return_value = SimpleNamespace(logits=[scores])
        models = SimpleNamespace(AutoFeatureExtractor=SimpleNamespace(from_pretrained=lambda *a, **k: Features()),
                                 AutoModelForAudioClassification=SimpleNamespace(from_pretrained=lambda *a, **k: model))
        torch = SimpleNamespace(float32="float32", inference_mode=nullcontext)
        with patch.dict(sys.modules, {"transformers": models}), patch("storylens.audio_worker._torch_device", return_value=torch):
            result = _sound({"model_path": "local", "device": "cpu"}, np.zeros(80001, dtype=np.float32), _Budget())
        self.assertEqual(calls, [40000, 800])
        self.assertEqual(result["score_activation"], "sigmoid")
        self.assertEqual(result["windows"][0]["scores"], [{"label": "bird", "score": 0.75}, {"label": "silence", "score": 0.25}])
        self.assertEqual(result["windows"][1]["end_seconds"], 80001 / 16000)

    def test_pyannote4_preserves_overlapping_annotation_not_exclusive(self):
        from storylens.audio_worker import _diarize, _Budget
        turns = [(SimpleNamespace(start=0, end=2), "track0", "A"),
                 (SimpleNamespace(start=1, end=3), "track1", "B")]
        annotation = Mock()
        annotation.itertracks.return_value = iter(turns)
        pipeline = Mock(return_value=SimpleNamespace(speaker_diarization=annotation,
                                                    exclusive_speaker_diarization=Mock()))
        modules = {"pyannote.audio": SimpleNamespace(Pipeline=SimpleNamespace(from_pretrained=lambda *a: pipeline)),
                   "yaml": SimpleNamespace(safe_dump=lambda config: "local-config")}
        tensor = Mock()
        torch = SimpleNamespace(device=lambda device: device, from_numpy=lambda audio: tensor)
        with patch.dict(sys.modules, modules), patch("storylens.audio_worker._torch_device", return_value=torch), patch("storylens.audio_worker._local_diarization_config", return_value={"pipeline": {"params": {"plda": "local"}}}), patch("importlib.metadata.version", return_value="4.0.7"):
            result = _diarize({"model_path": "local", "device": "cpu"}, [], _Budget())
        self.assertEqual(len(result["turns"]), 2)
        self.assertLess(result["turns"][1]["start_seconds"], result["turns"][0]["end_seconds"])
        annotation.itertracks.assert_called_once_with(yield_label=True)


    @unittest.skipUnless(importlib.util.find_spec("yaml"), "optional PyYAML not installed")
    def test_diarization_rejects_hub_refs_and_external_checkpoint_paths(self):
        import yaml
        from storylens.audio_worker import _local_diarization_config
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "embedding.bin").touch()
            for reference in ("pyannote/segmentation-3.0", "/etc/passwd", "../outside.bin"):
                config = {"pipeline": {"name": "pyannote.audio.pipelines.SpeakerDiarization", "params": {
                    "segmentation": reference, "embedding": "embedding.bin"}}}
                (root / "config.yaml").write_text(yaml.safe_dump(config))
                with self.subTest(reference=reference), self.assertRaisesRegex(ValueError, "not installed"):
                    _local_diarization_config(root)


if __name__ == "__main__":
    unittest.main()
