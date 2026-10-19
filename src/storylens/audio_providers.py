"""Opt-in, isolated audio inference using explicitly installed local models only."""

import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading

from .resources import cuda_inference_lock

ALGORITHM_VERSION = "audio-models-2"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_ERROR_BYTES = 256 * 1024
MAX_SEGMENTS = 10_000
MAX_WORDS = 100_000
MAX_TEXT_CHARS = 1_000_000
SOUND_WINDOW_SECONDS = 5
SOUND_TOP_K = 5
LIBRARIES = {
    "transcription": ("faster-whisper", "ctranslate2", "numpy", "av", "tokenizers", "onnxruntime"),
    "diarization": ("pyannote.audio", "pyannote.core", "torch", "torchaudio", "numpy", "PyYAML"),
    "sound": ("transformers", "torch", "numpy", "tokenizers", "safetensors"),
}
OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1", "PYANNOTE_METRICS_ENABLED": "0",
    "DO_NOT_TRACK": "1", "TOKENIZERS_PARALLELISM": "false",
}


def _libraries(task):
    """Report versions of optional dependencies; missing packages become None.

    Provenance remains available without optional packages. Extraction explicitly
    checks these identities before decoding or starting the isolated worker.
    """
    result = {}
    for name in LIBRARIES[task]:
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = None
    return result


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Invalid audio model {label}")
    return float(value)


def _probability(value):
    if value is None:
        return None
    value = _number(value, "probability")
    if not 0 <= value <= 1:
        raise ValueError("Invalid audio model probability")
    return value


def _json(data):
    def invalid(value):
        raise ValueError(f"Invalid audio model JSON constant: {value}")
    try:
        value = json.loads(data, parse_constant=invalid)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid audio model worker response") from error
    if not isinstance(value, dict):
        raise ValueError("Audio model worker response must be an object")
    return value


class AudioModelExtractor:
    """No imports, downloads, or inference until an explicitly requested extraction."""

    def __init__(self, task, model_path, *, ffmpeg="ffmpeg", ffprobe="ffprobe", timeout=120,
                 max_audio_seconds=1800, device="cpu", language=""):
        if task not in LIBRARIES:
            raise ValueError("Audio task must be transcription, diarization, or sound")
        if not isinstance(model_path, (str, Path)) or not str(model_path).strip():
            raise ValueError("Audio model path must be an explicit nonempty local directory")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Audio model timeout must be positive and finite")
        if not math.isfinite(max_audio_seconds) or not 0 < max_audio_seconds <= 1800:
            raise ValueError("Audio model maximum duration must be positive and at most 1800 seconds")
        if device not in ("cpu", "cuda"):
            raise ValueError("Audio model device must be cpu or cuda; no automatic fallback")
        if not isinstance(language, str) or len(language) > 32:
            raise ValueError("Audio model language must be an ISO language code or empty")
        self.task, self.path = task, Path(model_path).expanduser().resolve()
        self.ffmpeg, self.ffprobe = ffmpeg, ffprobe
        self.timeout, self.max_audio_seconds = timeout, max_audio_seconds
        self.device, self.language = device, language

    def _model_digest(self):
        if not self.path.is_dir():
            raise ValueError(f"Local audio model directory does not exist: {self.path}")
        required = "config.yaml" if self.task == "diarization" else "config.json"
        if not (self.path / required).is_file():
            raise ValueError(f"Local {self.task} model requires {required} and all local model assets")
        files = sorted(path for path in self.path.rglob("*") if path.is_file())
        if not files:
            raise ValueError("Local audio model directory is empty")
        def state(paths):
            return [(str(p.relative_to(self.path)), p.stat().st_size, p.stat().st_mtime_ns,
                     p.stat().st_ctime_ns, p.stat().st_ino) for p in paths]
        before = state(files)
        digest = hashlib.sha256()
        for path in files:
            digest.update(str(path.relative_to(self.path)).encode())
            digest.update(b"\0")
            content = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    content.update(chunk)
            digest.update(content.digest())
        after_files = sorted(path for path in self.path.rglob("*") if path.is_file())
        if before != state(after_files):
            raise ValueError("Local audio model files changed while hashing")
        return "sha256:" + digest.hexdigest()

    def provenance(self):
        # Rehash on every boundary: restored mtimes must not reuse stale model evidence.
        from .video_analysis import VideoTechnicalExtractor
        versions = VideoTechnicalExtractor(self.ffmpeg, self.ffprobe, self.timeout).provenance()["tools"]
        return {
            "algorithm": ALGORITHM_VERSION, "task": self.task,
            "provider": {"transcription": "local-faster-whisper", "diarization": "local-pyannote",
                         "sound": "local-transformers"}[self.task],
            "model_digest": self._model_digest(), "libraries": _libraries(self.task),
            "device": self.device, "compute_type": (("int8" if self.device == "cpu" else "float16")
            if self.task == "transcription" else "float32"),
            "language": self.language, "tool_versions": versions,
            "preprocessing": {"channels": 1, "sample_rate_hz": 16000, "format": "PCM signed 16-bit",
                              "max_audio_seconds": self.max_audio_seconds},
            "settings": {"word_timestamps": True, "vad": "bundled Silero, default VadOptions",
                         "vad_filters_transcription": False}
            if self.task == "transcription" else
            {"speaker_labels": "anonymous run-local clusters", "preserve_overlap": True}
            if self.task == "diarization" else
            {"window_seconds": SOUND_WINDOW_SECONDS, "top_k": SOUND_TOP_K,
             "scores": "sigmoid for multi_label_classification; otherwise softmax; uncalibrated",
             "resampling": "linear interpolation to model feature-extractor sample rate"},
            "network": "offline environment and Python audit hook denying network before model imports",
        }

    def cache_key(self):
        return hashlib.sha256(json.dumps(self.provenance(), sort_keys=True).encode()).hexdigest()

    def _run_worker(self, wav_path):
        """Only CUDA inference waits for the shared same-host advisory resource.

        Decoding, provenance and CPU work never take this lock. Lock acquisition
        and the worker each have the configured timeout independently; no CPU
        fallback is attempted when either fails.
        """
        if self.device == "cuda":
            with cuda_inference_lock(self.timeout):
                return self._execute_worker(wav_path)
        return self._execute_worker(wav_path)

    def _execute_worker(self, wav_path):
        request = json.dumps({"task": self.task, "model_path": str(self.path), "wav_path": str(wav_path),
                              "device": self.device, "language": self.language}).encode()
        env = dict(os.environ, **OFFLINE_ENV)
        try:
            process = subprocess.Popen([sys.executable, "-m", "storylens.audio_worker"],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       env=env, start_new_session=True)
        except OSError as error:
            raise ValueError(f"Cannot start local audio model worker: {error}") from error
        output, errors, failures = bytearray(), bytearray(), []

        def kill():
            try:
                if sys.platform == "win32":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        def drain(pipe, target, limit):
            try:
                while chunk := pipe.read(65536):
                    if len(target) + len(chunk) > limit:
                        raise ValueError("Audio model worker exceeded bounded output limit")
                    target.extend(chunk)
            except Exception as error:
                failures.append(error)
                kill()
            finally:
                pipe.close()

        threads = [threading.Thread(target=drain, args=(process.stdout, output, MAX_RESPONSE_BYTES)),
                   threading.Thread(target=drain, args=(process.stderr, errors, MAX_ERROR_BYTES))]
        for thread in threads:
            thread.start()
        timed_out = False
        try:
            process.stdin.write(request)
            process.stdin.close()
            process.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            kill()
            process.wait()
        except BaseException:
            kill()
            process.wait()
            raise
        finally:
            if not process.stdin.closed:
                process.stdin.close()
            for thread in threads:
                thread.join()
        if timed_out:
            raise ValueError("Local audio model exceeded configured timeout")
        if failures:
            raise ValueError(str(failures[0])) from failures[0]
        if process.returncode:
            raise ValueError("Local audio model failed: " + errors.decode("utf-8", "replace")[-2000:])
        result = _json(bytes(output))
        if "error" in result:
            raise ValueError("Local audio model failed: " + str(result["error"])[:2000])
        return result

    def _result(self, raw, timing, provenance):
        origin, duration = timing["origin_seconds"], timing["duration_seconds"]
        text_count = word_count = 0

        def text(value):
            nonlocal text_count
            if not isinstance(value, str):
                raise ValueError("Invalid audio model text")
            text_count += len(value)
            if text_count > MAX_TEXT_CHARS:
                raise ValueError("Audio model text exceeds bounded payload limit")
            return value

        def interval(value):
            start = _number(value["start_seconds"], "start time")
            end = _number(value["end_seconds"], "end time")
            if not 0 <= start <= end or end > duration + 0.1:
                raise ValueError("Audio model interval outside decoded source coverage")
            return {"start_seconds": origin + min(start, duration),
                    "end_seconds": origin + min(end, duration)}

        def entries(key, source=raw):
            items = source[key]
            if not isinstance(items, list) or len(items) > MAX_SEGMENTS:
                raise ValueError("Audio model segments exceed bounded payload limit")
            return items

        result = {"schema_version": "1.0", "provenance": provenance, "source_timing": timing}
        try:
            if self.task == "transcription":
                segments = []
                for index, segment in enumerate(entries("segments")):
                    words = []
                    for word in entries("words", segment):
                        word_count += 1
                        if word_count > MAX_WORDS:
                            raise ValueError("Audio model words exceed bounded payload limit")
                        words.append(dict(interval(word), text=text(word["text"]),
                                          probability=_probability(word.get("probability"))))
                    segments.append(dict(interval(segment), index=index, text=text(segment["text"]),
                                         speaker=None, words=words))
                activity = raw["speech_activity"]
                if activity.get("method") != "Silero VAD" or activity.get("enabled") is not True:
                    raise ValueError("Transcription requires actual Silero VAD evidence")
                result.update(language=text(raw["language"]),
                              language_probability=_probability(raw.get("language_probability")),
                              segments=segments,
                              speech_activity={"enabled": True, "method": "Silero VAD",
                                               "intervals": [interval(x) for x in entries("intervals", activity)]})
            elif self.task == "diarization":
                labels, turns = {}, []
                for turn in entries("turns"):
                    label = text(turn["speaker"])
                    if label not in labels:
                        labels[label] = f"SPEAKER_{len(labels):02d}"
                    turns.append(dict(interval(turn), index=len(turns), speaker=labels[label]))
                result.update(turns=turns, speaker_label_scope="anonymous clusters within this result only",
                              overlap_policy="independent turns; overlaps preserved; no transcript assignment")
            else:
                windows = []
                previous_end = origin
                for window in entries("windows"):
                    scores = window["scores"]
                    if not isinstance(scores, list) or not 0 < len(scores) <= SOUND_TOP_K:
                        raise ValueError("Invalid sound model top-k scores")
                    bounds = interval(window)
                    if abs(bounds["start_seconds"] - previous_end) > 1 / 16000:
                        raise ValueError("Sound model windows do not cover the source contiguously")
                    if any(s.get("score") is None for s in scores):
                        raise ValueError("Sound model must return actual numeric scores")
                    previous_end = bounds["end_seconds"]
                    windows.append(dict(bounds, index=len(windows),
                                        scores=[{"label": text(s["label"]), "score": _probability(s["score"])}
                                                for s in scores]))
                if not windows or abs(previous_end - timing["end_seconds"]) > 1 / 16000:
                    raise ValueError("Sound model did not cover the complete decoded source")
                if raw["score_activation"] not in ("softmax", "sigmoid"):
                    raise ValueError("Invalid sound model score activation")
                result.update(windows=windows, coverage={"start_seconds": origin,
                              "end_seconds": timing["end_seconds"], "duration_seconds": duration,
                              "method": "contiguous input windows; model preprocessing may pad/truncate; final short input padded to 0.1 s"},
                              score_interpretation="Uncalibrated model scores, not verified event probabilities",
                              score_activation=text(raw["score_activation"]))
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("Invalid audio model worker response shape") from error
        return result

    def extract(self, path: Path):
        from .audio_analysis import decoded_audio
        before = self.provenance()
        missing = [name for name, identity in before["libraries"].items() if identity is None]
        if missing:
            raise ValueError("Missing optional local audio dependencies: " + ", ".join(missing))
        with decoded_audio(Path(path), ffmpeg=self.ffmpeg, ffprobe=self.ffprobe, timeout=self.timeout,
                           max_audio_seconds=self.max_audio_seconds) as (wav_path, timing):
            result = self._result(self._run_worker(wav_path), timing, before)
        if self.provenance() != before:
            raise ValueError("Local audio model or runtime identity changed during inference; result discarded")
        return result
