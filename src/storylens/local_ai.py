"""Explicit local-AI setup and bounded, read-only samples shared by both wizards.

Inspection never imports ML runtimes, runs inference, or downloads models. Only
``install(..., consent=True)`` contacts a model host. Samples use installed local
assets and isolated workers; no sample result is written to the project catalog.
"""

from contextlib import nullcontext, redirect_stdout
from importlib.metadata import PackageNotFoundError, version
import json
import math
import os
from pathlib import Path
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time

from .audio_providers import AudioModelExtractor, LIBRARIES, OFFLINE_ENV
from .config import default_database
from .ingest import discover, kind
from .providers import LocalEmbeddings, OllamaVision

PROFILE_FIELDS = frozenset({
    "embedding_model", "vision_model", "ollama_endpoint", "audio_transcription_model",
    "audio_transcription_device", "audio_transcription_language",
})
DEFAULT_ENDPOINT = "http://127.0.0.1:11434"
MODEL_SPECS = {
    "embeddings": {
        "title": "Visual search", "description": "Find images and video frames using text or similar images.",
        "recommendation": "OpenAI CLIP ViT-B/32 (CPU)", "repo": "openai/clip-vit-base-patch32",
        "directory": "clip-vit-base-patch32", "download_bytes": 610_000_000,
        "hardware": "CPU supported; allow roughly 2–3 GB free RAM. Speed varies; no GPU is required.",
        "extra": "semantic,model-installer", "field": "embedding_model",
        "patterns": ["config.json", "preprocessor_config.json", "tokenizer.json", "tokenizer_config.json",
                     "special_tokens_map.json", "vocab.json", "merges.txt"],
    },
    "vision": {
        "title": "Image descriptions", "description": "Describe images and sampled video frames with a local Ollama model.",
        "recommendation": "Ollama moondream (local, cloud disabled)", "download_bytes": 1_800_000_000,
        "hardware": "Small local vision model; allow roughly 4 GB free RAM. CPU may be slow; availability is not a hardware certification.",
        "field": "vision_model",
    },
    "transcription": {
        "title": "Speech transcription", "description": "Transcribe spoken words from local audio and videos.",
        "recommendation": "faster-whisper base (CPU int8)", "repo": "Systran/faster-whisper-base",
        "directory": "faster-whisper-base", "download_bytes": 150_000_000,
        "hardware": "CPU int8 is recommended; allow roughly 1 GB free RAM. CUDA is optional and must be explicitly selected.",
        "extra": "transcription,model-installer", "field": "audio_transcription_model",
        "patterns": ["config.json", "model.bin", "tokenizer.json", "vocabulary.*", "preprocessor_config.json"],
    },
}


def _root():
    return default_database().parent.expanduser().absolute()


def _profile_path():
    return _root() / "ai-profile.json"


def _command(extra):
    checkout = Path(__file__).resolve().parents[2]
    command = [sys.executable, "-m", "pip", "install"]
    if (checkout / "pyproject.toml").is_file():
        command.extend(["-e", str(checkout) + "[" + extra + "]"])
    else:
        command.append("storylens[" + extra + "] @ https://github.com/eriktiekstra/StoryLens/archive/refs/heads/main.zip")
    if sys.platform == "win32":
        return "& " + subprocess.list2cmdline(command)
    return shlex.join(command)


def _settings(settings):
    if not isinstance(settings, dict):
        raise ValueError("Local AI settings must be an object")
    values = {key: value for key, value in settings.items() if key in PROFILE_FIELDS}
    for key, value in values.items():
        if not isinstance(value, str) or len(value) > 8192 or "\0" in value:
            raise ValueError(key + " must be bounded text without null characters")
    for key in ("embedding_model", "audio_transcription_model"):
        if values.get(key):
            values[key] = str(Path(values[key]).expanduser().absolute())
    if values.get("audio_transcription_device", "cpu") not in ("cpu", "cuda"):
        raise ValueError("Transcription device must be cpu or cuda; no automatic fallback")
    if len(values.get("audio_transcription_language", "")) > 32:
        raise ValueError("Transcription language must be an ISO language code or empty")
    if values.get("ollama_endpoint") or values.get("vision_model"):
        OllamaVision(values.get("ollama_endpoint", DEFAULT_ENDPOINT), values.get("vision_model") or "moondream", 2)
    for key in ("timeout", "semantic_timeout"):
        if key in settings:
            value = settings[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(key + " must be a finite positive number")
            values[key] = value
    return values


def _safe_storage(path):
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError("Local AI storage must not use symlinks: " + str(component))
    if path.exists() and not path.is_dir():
        raise ValueError("Local AI storage must be a directory: " + str(path))


def load_profile():
    """Load model settings only; never apply them to a project automatically."""
    path = _profile_path()
    _safe_storage(path.parent)
    if not path.exists() and not path.is_symlink():
        return {}
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_nlink != 1:
        raise ValueError("Local AI profile must be a regular private file")
    with path.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("Local AI profile exceeds size limit")
    try:
        document = json.loads(raw)
    except (ValueError, UnicodeError) as error:
        raise ValueError("Cannot read local AI profile JSON; save a new profile to replace it") from error
    if not isinstance(document, dict) or set(document) - PROFILE_FIELDS:
        raise ValueError("Local AI profile contains unsupported settings")
    return _settings(document)


def save_profile(settings):
    """Atomically persist only explicitly supplied, allowlisted model settings."""
    values = {key: value for key, value in _settings(settings).items() if key in PROFILE_FIELDS}
    path = _profile_path()
    _safe_storage(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_nlink != 1)):
        raise ValueError("Local AI profile must be a regular private file")
    descriptor, name = tempfile.mkstemp(prefix=".ai-profile-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(values, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
    return values


def _dependencies(names):
    missing = []
    for name in names:
        try:
            version(name)
        except PackageNotFoundError:
            missing.append(name)
    return missing


def _model_files(capability, value):
    path = Path(value).expanduser()
    if not path.is_dir():
        raise ValueError("Installed local model directory does not exist: " + str(path))
    required = ["config.json"]
    if capability == "embeddings":
        required.append("preprocessor_config.json")
        tokenizer_files = ("tokenizer.json", "tokenizer.model", "spiece.model", "vocab.json")
        if not any((path / name).is_file() and (path / name).stat().st_size > 0 for name in tokenizer_files):
            raise ValueError("Embedding model is missing local tokenizer assets")
        weights = any(p.is_file() and p.stat().st_size > 0 for pattern in
                      ("*.safetensors", "pytorch_model*.bin") for p in path.glob(pattern))
        if not weights:
            raise ValueError("Embedding model is missing local weights (.safetensors or pytorch_model.bin)")
    else:
        required.extend(["model.bin", "tokenizer.json"])
    for name in required:
        if not (path / name).is_file() or (path / name).stat().st_size == 0:
            raise ValueError("Local " + capability + " model is missing " + name)
    try:
        with (path / "config.json").open("rb") as stream:
            raw = stream.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("Model configuration exceeds size limit")
        config = json.loads(raw)
    except (ValueError, UnicodeError) as error:
        raise ValueError("Local model config.json is not valid bounded JSON") from error
    if not isinstance(config, dict):
        raise ValueError("Local model config.json must contain an object")
    if capability == "embeddings" and config.get("model_type") not in ("clip", "siglip"):
        raise ValueError("Embedding model must be CLIP or SigLIP; remote code is never enabled")


def _check_one(capability, settings):
    spec = MODEL_SPECS[capability]
    if capability == "vision":
        provider = OllamaVision(settings.get("ollama_endpoint", DEFAULT_ENDPOINT), settings["vision_model"],
                                min(settings.get("semantic_timeout", 120), 3))
        provider.identity()  # Metadata only: verifies cloud disabled, digest, and vision capability.
        return
    names = ("torch", "transformers", "Pillow", "numpy", "tokenizers", "safetensors", "sentencepiece", "protobuf") if capability == "embeddings" else LIBRARIES["transcription"]
    missing = _dependencies(names)
    if missing:
        raise ValueError("Missing " + ", ".join(missing) + ". Install in this interpreter: " + _command(spec["extra"]))
    _model_files(capability, settings[spec["field"]])


def check(settings):
    """Check selected capabilities without inference, imports, or model downloads."""
    errors, warnings = [], []
    try:
        values = _settings(settings)
    except (ValueError, OSError) as error:
        return {"ready": False, "errors": [str(error)], "warnings": [], "settings": {}}
    selected = [key for key, spec in MODEL_SPECS.items() if values.get(spec["field"])]
    if not selected:
        warnings.append("No local AI capability is selected. Technical analysis works without AI.")
    for capability in selected:
        try:
            _check_one(capability, values)
        except (ValueError, RuntimeError, OSError) as error:
            errors.append(MODEL_SPECS[capability]["title"] + ": " + str(error))
    if selected:
        warnings.append("Installed files and dependencies do not certify hardware, complete model compatibility, or inference speed. Run the small sample next.")
    if values.get("audio_transcription_device") == "cuda" and "transcription" in selected:
        warnings.append("CUDA was explicitly selected; no CPU fallback will occur. CPU int8 is the recommended starting point.")
    return {"ready": bool(selected) and not errors, "errors": errors, "warnings": warnings, "settings": values}


def options():
    profile = load_profile()
    capabilities = []
    for capability, spec in MODEL_SPECS.items():
        model = profile.get(spec["field"]) or ("moondream" if capability == "vision" else str(_root() / "models" / spec["directory"]))
        selected = dict(profile, **{spec["field"]: model})
        try:
            _check_one(capability, selected)
            installed = True
        except (ValueError, RuntimeError, OSError):
            installed = False
        command = ("Install Ollama from https://ollama.com/download, then run OLLAMA_NO_CLOUD=1 ollama serve; "
                   "the explicit Download action calls its loopback /api/pull for moondream.") if capability == "vision" else _command(spec["extra"])
        capabilities.append({key: spec[key] for key in ("title", "description", "recommendation", "download_bytes", "hardware")} |
                            {"id": capability, "install_command": command, "model_path": model, "installed": installed})
    return {"capabilities": capabilities, "settings": profile, "profile_path": str(_profile_path()), "instructions": [
        "Start with technical analysis; local AI is optional and can enrich the same project later.",
        "Sizes shown are approximate downloads, not RAM requirements. Available hardware is not certified automatically.",
        "Install optional Python dependencies with the exact interpreter command shown; StoryLens never runs pip for you.",
        "Download requires explicit consent and contacts only the model host or local Ollama runtime. No source paths or media are sent to model hosts.",
        "For vision, install Ollama separately, disable daemon cloud with OLLAMA_NO_CLOUD=1, and restart it. Checking contacts only the numeric loopback endpoint.",
        "Run a small sample before collection analysis. Saving or loading a reusable profile is explicit and does not enable project AI by itself.",
    ]}


def install(capability, *, consent, settings=None):
    """Download one fixed recommendation only after explicit boolean consent."""
    if consent is not True:
        raise ValueError("Explicit boolean consent=True is required before downloading a model")
    if capability not in MODEL_SPECS:
        raise ValueError("Choose embeddings, vision, or transcription")
    spec = MODEL_SPECS[capability]
    profile = load_profile()
    settings = {key: value for key, value in _settings({} if settings is None else settings).items() if key in PROFILE_FIELDS}
    if capability == "vision":
        provider = OllamaVision(settings.get("ollama_endpoint", profile.get("ollama_endpoint", DEFAULT_ENDPOINT)), "moondream", 1800)
        provider._cloud_disabled()
        if not isinstance(provider._request("/api/version").get("version"), str):
            raise ValueError("Install and start a supported local Ollama runtime first")
        response = provider._request("/api/pull", {"model": "moondream", "stream": False})
        if response.get("status") != "success":
            raise RuntimeError("Ollama did not complete the moondream download; retry explicitly when ready")
        provider.identity()
        model = "moondream"
        settings["ollama_endpoint"] = provider.endpoint
    else:
        try:
            from huggingface_hub import snapshot_download
        except ImportError as error:
            raise ValueError("Model downloading needs an optional dependency: " + _command("model-installer")) from error
        target = _root() / "models" / spec["directory"]
        _safe_storage(target)
        if target.exists() and any(path.is_symlink() for path in target.rglob("*")):
            raise ValueError("Recommended model download storage must not contain symlinks")
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Inspect public model metadata only inside this explicitly consented
        # action. Choose one weight format, never two copies of the same model.
        try:
            patterns = list(spec["patterns"])
            revision = None
            if capability == "embeddings":
                from huggingface_hub import HfApi
                info = HfApi(endpoint="https://huggingface.co", token=False).model_info(repo_id=spec["repo"], token=False)
                filenames = {item.rfilename for item in info.siblings}
                weight = next((name for name in ("model.safetensors", "pytorch_model.bin") if name in filenames), None)
                if weight is None:
                    raise ValueError("Recommended embedding repository has no supported local weight file")
                patterns.append(weight)
                revision = info.sha
            snapshot_download(repo_id=spec["repo"], local_dir=str(target), allow_patterns=patterns,
                              revision=revision, token=False, max_workers=2, endpoint="https://huggingface.co")
        except Exception as error:
            raise RuntimeError("Recommended model download failed; partial files remain available for an explicit retry: " + str(error)) from error
        _model_files(capability, str(target))
        model = str(target)
    settings[spec["field"]] = model
    if capability == "transcription":
        settings["audio_transcription_device"] = "cpu"
    save_profile(dict(profile, **settings))
    return {"capability": capability, "settings": settings, "model_path": model, "installed": True,
            "download_bytes": spec["download_bytes"], "size_is_approximate": True,
            "instructions": "The model choice is saved in your reusable profile. Apply it explicitly to this project, check dependencies, and run the small sample."}


def _cancelled(cancelled):
    return cancelled is not None and (cancelled.is_set() if hasattr(cancelled, "is_set") else cancelled())


def _sample_sources(project, cancelled, capabilities):
    from .access import PathPolicy
    root = Path(project.source).resolve(strict=True)
    policy = PathPolicy([root])
    chosen = {}
    needs_visual = bool({"embeddings", "vision"} & set(capabilities))
    needs_audio = "transcription" in capabilities
    for index, entry in enumerate(discover(root, project.config.exclusions)):
        if index >= 10000:
            break
        if _cancelled(cancelled):
            raise ValueError("Sample cancelled")
        if isinstance(entry, OSError):
            continue
        path = policy.authorize(entry)
        media_kind = kind(path)
        if media_kind not in chosen:
            chosen[media_kind] = path
        visual = "image" in chosen or "video" in chosen
        audio = "audio" in chosen or "video" in chosen
        if (visual or not needs_visual) and (audio or not needs_audio):
            break
    return chosen


def _stop(process):
    if process.poll() is None:
        try:
            if sys.platform == "win32":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()


def _sample_worker(request, timeout, cancelled):
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen([sys.executable, "-m", "storylens.local_ai"], stdin=subprocess.PIPE,
                                   stdout=output, stderr=errors, env=dict(os.environ, **OFFLINE_ENV), start_new_session=True)
        deadline = time.monotonic() + timeout
        try:
            process.stdin.write(json.dumps(request).encode())
            process.stdin.close()
            while process.poll() is None:
                if _cancelled(cancelled):
                    raise ValueError("Sample cancelled")
                if time.monotonic() >= deadline:
                    raise ValueError("Small sample exceeded its time limit; check hardware/model compatibility or increase semantic_timeout")
                if os.fstat(output.fileno()).st_size > 2 * 1024 * 1024 or os.fstat(errors.fileno()).st_size > 2 * 1024 * 1024:
                    raise ValueError("Sample runtime exceeded its output limit")
                time.sleep(0.05)
            output.seek(0)
            raw = output.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError("Sample result exceeded its output limit")
            if process.returncode:
                errors.seek(max(0, os.fstat(errors.fileno()).st_size - 2000))
                raise ValueError("Local sample runtime failed: " + errors.read(2000).decode("utf-8", "replace"))
            result = json.loads(raw)
            if "error" in result:
                raise ValueError(result["error"])
            return result
        finally:
            if not process.stdin.closed:
                process.stdin.close()
            _stop(process)


def sample(project, settings, cancelled=None):
    """Try each selected capability on one safe source, at most 15 seconds of audio.

    Model startup/inference/decoding runs in a killable process with an overall
    deadline capped at five minutes per capability. No catalog or status writes.
    """
    results, errors = [], []
    try:
        values = _settings(settings)
        if _cancelled(cancelled):
            raise ValueError("Sample cancelled")
        selected = [key for key, spec in MODEL_SPECS.items() if values.get(spec["field"])]
        if not selected:
            raise ValueError("Select at least one local AI capability before running a sample")
        sources = _sample_sources(project, cancelled, selected)
        for capability in selected:
            if _cancelled(cancelled):
                errors.append("Sample cancelled")
                break
            try:
                _check_one(capability, values)
                if capability == "transcription":
                    path = sources.get("audio") or sources.get("video")
                else:
                    path = sources.get("image") or sources.get("video")
                if path is None:
                    raise ValueError("No suitable source " + ("audio/video" if capability == "transcription" else "image/video") + " found in the project source")
                request = {"capability": capability, "source": str(path), "source_kind": kind(path),
                           "source_root": str(project.source), "settings": values,
                           "ffmpeg": project.config.ffmpeg, "ffprobe": project.config.ffprobe}
                result = _sample_worker(request, min(values.get("semantic_timeout", 120), 300), cancelled)
                results.append({"capability": capability, "source": str(path), **result})
            except (ValueError, RuntimeError, OSError) as error:
                errors.append(MODEL_SPECS[capability]["title"] + ": " + str(error))
    except (ValueError, RuntimeError, OSError) as error:
        errors.append(str(error))
    return {"ready": bool(results) and not errors, "results": results, "errors": errors}


def _infer(request):
    from .video_analysis import VideoTechnicalExtractor
    capability, values = request["capability"], request["settings"]
    from .access import PathPolicy
    source = PathPolicy([request["source_root"]]).authorize(request["source"])
    if not source.is_file():
        raise ValueError("Sample source must remain a regular file")
    timeout = min(values.get("semantic_timeout", 120), 300)
    runner = VideoTechnicalExtractor(ffmpeg=request["ffmpeg"], ffprobe=request["ffprobe"], timeout=timeout)
    with tempfile.TemporaryDirectory(prefix="storylens-ai-sample-") as temporary:
        path = source
        if capability == "transcription":
            path = Path(temporary) / "sample.wav"
            runner._run(request["ffmpeg"], ["-v", "error", "-nostdin", "-protocol_whitelist", "file,pipe", "-i", str(source),
                                          "-t", "15", "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(path)])
            provider = AudioModelExtractor("transcription", values["audio_transcription_model"],
                                           ffmpeg=request["ffmpeg"], ffprobe=request["ffprobe"], timeout=timeout,
                                           max_audio_seconds=16, device=values.get("audio_transcription_device", "cpu"),
                                           language=values.get("audio_transcription_language", ""))
            # This process is already isolated and killable. Reuse the audio
            # worker's inference here rather than spawning a second detached
            # worker that could outlive a cancelled sample.
            from .audio_analysis import decoded_audio
            from .audio_worker import _Budget, _offline, _read_pcm16, _transcribe
            from .resources import cuda_inference_lock
            before = provider.provenance()
            _offline()
            with decoded_audio(path, ffmpeg=request["ffmpeg"], ffprobe=request["ffprobe"],
                               timeout=timeout, max_audio_seconds=16) as (wav_path, timing):
                lock = cuda_inference_lock(timeout) if provider.device == "cuda" else nullcontext()
                with lock:
                    raw = _transcribe({"model_path": str(provider.path), "device": provider.device,
                                       "language": provider.language}, _read_pcm16(wav_path), _Budget())
                result = provider._result(raw, timing, before)
            if provider.provenance() != before:
                raise ValueError("Local transcription model changed during the sample; result discarded")
            return {"text": " ".join(segment["text"] for segment in result.get("segments", [])),
                    "segments": result.get("segments", []), "language": result.get("language"),
                    "sample_seconds": result["source_timing"]["duration_seconds"],
                    "coverage": "First at most 15 seconds, sample-relative timestamps; not a collection transcript."}
        if request["source_kind"] == "video":
            path = Path(temporary) / "frame.jpg"
            runner._run(request["ffmpeg"], ["-v", "error", "-nostdin", "-protocol_whitelist", "file,pipe", "-i", str(source),
                                          "-frames:v", "1", "-vf", "scale=1024:1024:force_original_aspect_ratio=decrease", "-update", "1", str(path)])
        if capability == "vision":
            provider = OllamaVision(values.get("ollama_endpoint", DEFAULT_ENDPOINT), values["vision_model"], timeout)
            return {"caption": provider.describe(path), "coverage": "One source image or the first decoded video frame."}
        from .audio_worker import _offline
        _offline()  # Deny network before importing any ML runtime, including loopback.
        provider = LocalEmbeddings(values["embedding_model"])
        image_vector = provider.image(path)
        text_vector = provider.text("a photograph")
        return {"dimensions": len(image_vector), "text_image_similarity": sum(a * b for a, b in zip(image_vector, text_vector)),
                "coverage": "One image and the example query 'a photograph'; this score is not a quality or relevance guarantee."}


def _main():
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError("Sample request exceeds size limit")
        request = json.loads(raw)
        with redirect_stdout(sys.stderr):
            result = _infer(request)
    except Exception as error:
        result = {"error": str(error)}
    sys.stdout.write(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    _main()
