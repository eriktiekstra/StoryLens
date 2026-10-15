"""Out-of-process, network-denied worker for isolated audio inference."""

import json
import math
import os
import signal
import sys
from pathlib import Path


def _deny_network(event, args):
    if event == "urllib.Request" and args:
        url = args[0] if not isinstance(args[0], tuple) else args[0][0]
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            raise RuntimeError(f"Audio worker network access denied: {event} {url}")
    if event == "socket.connect" and args:
        addr = args[0]
        if isinstance(addr, tuple) and len(addr) == 2 and addr[0] not in ("127.0.0.1", "::1"):
            raise RuntimeError(f"Audio worker network access denied: {event} {addr}")


if hasattr(sys, "addaudithook"):
    sys.addaudithook(_deny_network)

# Reject before any model import.
os.environ.update({
    "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1", "PYANNOTE_METRICS_ENABLED": "0",
    "DO_NOT_TRACK": "1", "TOKENIZERS_PARALLELISM": "false",
})


def _load(request):
    task, model_path, device, language = request["task"], request["model_path"], request["device"], request["language"]
    wav_path = request["wav_path"]
    result = {}
    if task == "transcription":
        from faster_whisper import WhisperModel
        model = WhisperModel(model_path, device=device, compute_type="int8" if device == "cpu" else "float16",
                             local_files_only=True, cpu_threads=os.cpu_count() or 1)
        # Transcribe without VAD so short/noisy speech is still attempted; we report VAD separately.
        segments, info = model.transcribe(wav_path, language=language or None,
                                          word_timestamps=True, vad_filter=False)
        output_segments, vad_intervals = [], []
        for segment in segments:
            words = [{"start_seconds": w.start, "end_seconds": w.end, "text": w.word,
                      "probability": w.probability} for w in (segment.words or [])]
            output_segments.append({"start_seconds": segment.start, "end_seconds": segment.end,
                                    "text": segment.text, "words": words})
        # Run Silero VAD to return actual speech activity intervals.
        import numpy as np
        from faster_whisper.audio import decode_audio
        from faster_whisper.vad import VadOptions, get_speech_timestamps
        audio = decode_audio(wav_path, sampling_rate=16000)
        for chunk in get_speech_timestamps(audio, VadOptions()):
            vad_intervals.append({"start_seconds": chunk["start"] / 16000,
                                  "end_seconds": chunk["end"] / 16000})
        result.update(language=info.language, language_probability=info.language_probability,
                      segments=output_segments,
                      speech_activity={"enabled": True, "method": "Silero VAD", "intervals": vad_intervals})
    elif task == "diarization":
        from pyannote.audio import Pipeline
        import torch
        pipeline = Pipeline.from_pretrained(Path(model_path) / "config.yaml")
        device_obj = torch.device(device)
        pipeline.to(device_obj)
        diarization = pipeline({"uri": str(Path(wav_path).name), "audio": str(wav_path)})
        turns = []
        for turn, _, label in diarization.itertracks(yield_label=True):
            turns.append({"start_seconds": turn.start, "end_seconds": turn.end, "speaker": label})
        result.update(turns=turns)
    else:
        import numpy as np
        import torch
        from transformers import AutoConfig, AutoFeatureExtractor, AutoModelForAudioClassification
        config = AutoConfig.from_pretrained(model_path, local_files_only=True)
        feature_extractor = AutoFeatureExtractor.from_pretrained(model_path, local_files_only=True)
        model = AutoModelForAudioClassification.from_pretrained(model_path, local_files_only=True)
        model.to(device)
        model.eval()
        full = _read_pcm16(wav_path).astype(np.float32) / 32768.0
        sr = feature_extractor.sampling_rate
        # Resample linearly if feature extractor expects another rate.
        if sr != 16000:
            import torchaudio
            full = torchaudio.transforms.Resample(16000, sr)(torch.from_numpy(full)).numpy()
        window = int(SOUND_WINDOW_SECONDS * sr)
        activation = "softmax"
        problem = getattr(config, "problem_type", None)
        if problem == "multi_label_classification":
            activation = "sigmoid"
        id2label = getattr(config, "id2label", None)
        windows = []
        with torch.no_grad():
            for start in range(0, len(full), window):
                chunk = full[start:start + window]
                if len(chunk) < sr // 10:
                    continue
                inputs = feature_extractor(chunk, sampling_rate=sr, return_tensors="pt")
                inputs = {k: v.to(device) for k, v in inputs.items()}
                logits = model(**inputs).logits[0]
                if activation == "sigmoid":
                    probs = logits.sigmoid()
                else:
                    probs = logits.softmax(-1)
                top = probs.topk(min(SOUND_TOP_K, len(probs)))
                scores = []
                for score, idx in zip(top.values.tolist(), top.indices.tolist()):
                    label = id2label[str(idx)] if id2label and str(idx) in id2label else f"label_{idx}"
                    scores.append({"label": label, "score": score})
                windows.append({"start_seconds": start / sr, "end_seconds": (start + len(chunk)) / sr,
                                "scores": scores})
        result.update(windows=windows, score_activation=activation)
    return result


def _read_pcm16(path):
    import numpy as np
    raw = np.memmap(path, dtype="int16", mode="r")
    return np.array(raw, dtype=np.int16, copy=False)


def _run():
    try:
        request = json.loads(sys.stdin.read())
        for key in ("task", "model_path", "wav_path", "device", "language"):
            if key not in request:
                raise ValueError(f"Missing key: {key}")
        result = _load(request)
        sys.stdout.write(json.dumps(result, allow_nan=False))
    except Exception as error:
        sys.stdout.write(json.dumps({"error": f"{type(error).__name__}: {error}"}, allow_nan=False))
    sys.stdout.flush()


if __name__ == "__main__":
    _run()
