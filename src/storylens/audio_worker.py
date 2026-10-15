"""Out-of-process optional audio inference; never import ML runtimes in the parent."""

from contextlib import redirect_stdout
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import wave

from .audio_providers import (LIBRARIES, MAX_RESPONSE_BYTES, MAX_SEGMENTS, MAX_TEXT_CHARS,
                              MAX_WORDS, OFFLINE_ENV, SOUND_TOP_K, SOUND_WINDOW_SECONDS)


def _deny_network(event, args):
    # CPython passes (socket, address) for socket.connect; deny loopback as well.
    if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo",
                 "socket.gethostbyname", "socket.gethostbyaddr", "socket.sendto",
                 "socket.sendmsg", "socket.bind", "urllib.Request",
                 "http.client.connect"}:
        raise RuntimeError(f"Audio worker network access denied: {event}")


def _offline():
    os.environ.update(OFFLINE_ENV)
    sys.addaudithook(_deny_network)


def _read_pcm16(path):
    import numpy as np
    with wave.open(str(path), "rb") as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getcomptype()) != (1, 2, 16000, "NONE"):
            raise ValueError("Audio worker requires mono 16 kHz PCM signed-16 WAV")
        count = source.getnframes()
        if not 0 < count <= 1800 * 16000:
            raise ValueError("Audio worker input exceeds duration bound or is empty")
        data = source.readframes(count)
        if len(data) != count * 2:
            raise ValueError("Audio worker received truncated PCM")
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


class _Budget:
    def __init__(self):
        self.chars = self.words = 0

    def text(self, value):
        if not isinstance(value, str):
            raise ValueError("Audio model emitted non-text output")
        self.chars += len(value)
        if self.chars > MAX_TEXT_CHARS:
            raise ValueError("Audio model text exceeds bounded payload limit")
        return value

    def append(self, items, value):
        if len(items) >= MAX_SEGMENTS:
            raise ValueError("Audio model segments exceed bounded payload limit")
        items.append(value)


def _torch_device(device):
    import torch
    if device == "cuda" and not torch.cuda.is_available():
        raise ValueError("Requested CUDA device is unavailable; no CPU fallback")
    torch.set_num_threads(min(4, os.cpu_count() or 1))
    return torch


def _transcribe(request, audio, budget):
    root = Path(request["model_path"])
    for name in ("model.bin", "tokenizer.json", "config.json"):
        if not (root / name).is_file():
            raise ValueError(f"Local Whisper model is missing {name}; downloads are disabled")
    import ctranslate2
    from faster_whisper import WhisperModel
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    device = request["device"]
    if device == "cuda" and ctranslate2.get_cuda_device_count() < 1:
        raise ValueError("Requested CUDA device is unavailable; no CPU fallback")
    model = WhisperModel(request["model_path"], device=device,
                         compute_type="int8" if device == "cpu" else "float16",
                         local_files_only=True, cpu_threads=min(4, os.cpu_count() or 1))
    # Speech activity is independent evidence, not a substitute for ASR timestamps.
    intervals = []
    for chunk in get_speech_timestamps(audio, VadOptions()):
        budget.append(intervals, {"start_seconds": chunk["start"] / 16000,
                                  "end_seconds": chunk["end"] / 16000})
    segments, info = model.transcribe(audio, language=request["language"] or None,
                                      word_timestamps=True, vad_filter=False)
    output = []
    for segment in segments:
        words = []
        for word in segment.words or []:
            budget.words += 1
            if budget.words > MAX_WORDS:
                raise ValueError("Audio model words exceed bounded payload limit")
            budget.append(words, {"start_seconds": word.start, "end_seconds": word.end,
                                  "text": budget.text(word.word), "probability": word.probability})
        budget.append(output, {"start_seconds": segment.start, "end_seconds": segment.end,
                               "text": budget.text(segment.text), "words": words})
    return {"language": info.language, "language_probability": info.language_probability,
            "segments": output, "speech_activity": {"enabled": True, "method": "Silero VAD",
                                                       "intervals": intervals}}


def _local_diarization_config(root):
    """Resolve every external model reference inside the explicitly hashed directory."""
    import yaml
    root = Path(root).resolve()
    with (root / "config.yaml").open("rb") as source:
        raw = source.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("Local diarization config exceeds size limit")
    config = yaml.safe_load(raw)
    if not isinstance(config, dict) or not isinstance(config.get("pipeline"), dict):
        raise ValueError("Local diarization model requires a pipeline config")
    pipeline = config["pipeline"]
    if pipeline.get("name") != "pyannote.audio.pipelines.SpeakerDiarization":
        raise ValueError("Only the local pyannote SpeakerDiarization pipeline is supported")
    params = pipeline.get("params", {})

    def local(value):
        if isinstance(value, dict):
            value = dict(value)
            if "checkpoint" not in value:
                raise ValueError("Diarization model reference requires a local checkpoint")
            value["checkpoint"] = local(value["checkpoint"])
            subfolder = value.get("subfolder")
            if subfolder:
                target = (Path(value["checkpoint"]) / subfolder).resolve()
                if not target.is_relative_to(root) or not target.exists():
                    raise ValueError("Diarization subfolder is not installed inside local model directory")
            if value.get("revision") is not None:
                raise ValueError("Local diarization references cannot specify remote revisions")
            value.pop("token", None)
            value.pop("cache_dir", None)
            return value
        if not isinstance(value, str) or not value:
            raise ValueError("Diarization requires explicit local segmentation and embedding checkpoints")
        relative = value.removeprefix("$model/")
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.exists():
            raise ValueError(f"Diarization checkpoint is not installed inside local model directory: {value}")
        return str(path)

    for key in ("segmentation", "embedding"):
        params[key] = local(params.get(key))
    if "plda" in params:
        params["plda"] = local(params["plda"])
    pipeline["params"] = params
    return config


def _diarize(request, audio, budget):
    config = _local_diarization_config(request["model_path"])
    from importlib.metadata import version
    if int(version("pyannote.audio").split(".")[0]) >= 4 and "plda" not in config["pipeline"]["params"]:
        raise ValueError("Pyannote 4 requires an explicitly installed local PLDA checkpoint in config.yaml")
    torch = _torch_device(request["device"])
    from pyannote.audio import Pipeline
    import yaml
    # YAML path works in both pyannote 3.x and 4.x; references are absolute/local.
    with tempfile.TemporaryDirectory(prefix="storylens-pyannote-") as directory:
        config_path = Path(directory) / "config.yaml"
        config_path.write_text(yaml.safe_dump(config))
        pipeline = Pipeline.from_pretrained(str(config_path))
        if pipeline is None:
            raise ValueError("Cannot load complete local diarization pipeline")
        pipeline.to(torch.device(request["device"]))
        output = pipeline({"waveform": torch.from_numpy(audio).unsqueeze(0), "sample_rate": 16000})
    # 4.x returns DiarizeOutput; 3.x returns Annotation. Never use exclusive diarization.
    annotation = getattr(output, "speaker_diarization", output)
    turns = []
    for turn, _, label in annotation.itertracks(yield_label=True):
        budget.append(turns, {"start_seconds": turn.start, "end_seconds": turn.end,
                              "speaker": budget.text(str(label))})
    return {"turns": turns}


def _sound(request, audio, budget):
    import numpy as np
    torch = _torch_device(request["device"])
    from transformers import AutoFeatureExtractor, AutoModelForAudioClassification
    extractor = AutoFeatureExtractor.from_pretrained(request["model_path"], local_files_only=True,
                                                     trust_remote_code=False)
    model = AutoModelForAudioClassification.from_pretrained(request["model_path"], local_files_only=True,
                                                            trust_remote_code=False, use_safetensors=True)
    model = model.to(device=request["device"], dtype=torch.float32).eval()
    rate = extractor.sampling_rate
    if isinstance(rate, bool) or not isinstance(rate, int) or not 1 <= rate <= 384000:
        raise ValueError("Sound feature extractor has unsupported sample rate")
    activation = "sigmoid" if model.config.problem_type == "multi_label_classification" else "softmax"
    windows = []
    size = int(SOUND_WINDOW_SECONDS * 16000)
    with torch.inference_mode():
        for start in range(0, len(audio), size):
            original = audio[start:start + size]
            chunk = original
            if rate != 16000:
                length = max(1, round(len(chunk) * rate / 16000))
                chunk = np.interp(np.arange(length) * 16000 / rate,
                                  np.arange(len(chunk)), chunk).astype(np.float32)
            # Preserve coverage of even a tiny final remainder; pad only model input.
            if len(chunk) < math.ceil(rate / 10):
                chunk = np.pad(chunk, (0, math.ceil(rate / 10) - len(chunk)))
            inputs = extractor(chunk, sampling_rate=rate, return_tensors="pt")
            inputs = {key: value.to(request["device"]) for key, value in inputs.items()}
            logits = model(**inputs).logits[0]
            scores = logits.sigmoid() if activation == "sigmoid" else logits.softmax(-1)
            top = scores.topk(min(SOUND_TOP_K, scores.numel()))
            entries = []
            for score, index in zip(top.values.tolist(), top.indices.tolist()):
                labels = model.config.id2label
                label = labels.get(index, labels.get(str(index)))
                if label is None:
                    raise ValueError("Sound model is missing its class label mapping")
                entries.append({"label": budget.text(label), "score": score})
            budget.append(windows, {"start_seconds": start / 16000,
                                     "end_seconds": (start + len(original)) / 16000,
                                     "scores": entries})
    return {"windows": windows, "score_activation": activation}


def _load(request):
    if request.get("task") not in LIBRARIES or request.get("device") not in ("cpu", "cuda"):
        raise ValueError("Invalid local audio task or device")
    if not Path(request["model_path"]).is_dir():
        raise ValueError("An explicitly installed local model directory is required")
    audio = _read_pcm16(request["wav_path"])
    budget = _Budget()
    return {"transcription": _transcribe, "diarization": _diarize, "sound": _sound}[request["task"]](request, audio, budget)


def _run():
    _offline()  # Before numpy, torch, pyannote, transformers or faster-whisper imports.
    try:
        data = sys.stdin.buffer.read(65537)
        if len(data) > 65536:
            raise ValueError("Audio worker request exceeds bounded payload limit")
        request = json.loads(data)
        with redirect_stdout(sys.stderr):
            result = _load(request)
        payload = json.dumps(result, allow_nan=False)
        if len(payload.encode()) > MAX_RESPONSE_BYTES:
            raise ValueError("Audio model response exceeds bounded payload limit")
    except Exception as error:
        payload = json.dumps({"error": f"{type(error).__name__}: {str(error)[:1600]}"})
    sys.stdout.write(payload)
    sys.stdout.flush()


if __name__ == "__main__":
    _run()
