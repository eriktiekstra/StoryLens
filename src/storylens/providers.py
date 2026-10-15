"""Opt-in local semantic runtimes; importing this module never loads a model."""

import base64
from contextlib import contextmanager
import hashlib
import http.client
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
import io
import ipaddress
import json
import math
from pathlib import Path
import re
import socket
import time
from urllib.parse import urlsplit
import warnings

from PIL import Image, ImageOps

MAX_DIMENSION = 1024
MAX_PIXELS = 50_000_000
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_TEXT_CHARS = 32_768
PREPROCESSING = {
    "version": "semantic-rgb-1", "exif_transpose": True, "mode": "RGB",
    "alpha_background": "white", "max_dimension": MAX_DIMENSION,
    "max_source_pixels": MAX_PIXELS, "resize": "lanczos", "metadata": "removed",
}
CAPTION_PROMPT = "Describe this image. Do not guess identities or events."
GENERATION_OPTIONS = {"temperature": 0, "seed": 0, "num_predict": 128, "num_ctx": 2048}


def _json(data: bytes, label: str) -> dict:
    def invalid_constant(value):
        raise ValueError(f"Non-finite JSON value: {value}")
    try:
        result = json.loads(data, parse_constant=invalid_constant)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValueError(f"Invalid {label} JSON") from error
    if not isinstance(result, dict):
        raise ValueError(f"Invalid {label}: expected an object")
    return result


@contextmanager
def _frame(path: Path):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as source:
                if source.width * source.height > MAX_PIXELS:
                    raise ValueError("Image exceeds safe pixel limit")
                source.seek(0)
                oriented = ImageOps.exif_transpose(source)
                oriented.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
                rgba = oriented.convert("RGBA")
                image = Image.new("RGB", rgba.size, "white")
                image.paste(rgba, mask=rgba.getchannel("A"))
                rgba.close()
                if oriented is not source:
                    oriented.close()
            try:
                yield image
            finally:
                image.close()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError("Image exceeds safe pixel limit") from error


def _library_versions(*names: str) -> dict:
    result = {}
    for name in names:
        try:
            result[name] = version(name)
        except PackageNotFoundError as error:
            raise ValueError(f"Missing {name}; install storylens[semantic] for local embeddings") from error
    return result


class LocalEmbeddings:
    """CLIP/SigLIP features from an explicitly supplied local model directory."""

    def __init__(self, model_path: str):
        if not isinstance(model_path, str) or not model_path.strip():
            raise ValueError("Local embedding model path must be an explicit nonempty directory")
        self.path = Path(model_path).expanduser().resolve()
        self._identity = None
        self._snapshot = None
        self._model = None
        self._processor = None
        self._torch = None
        self._dimension = None

    def _files(self):
        if not self.path.is_dir():
            raise ValueError(f"Local embedding model directory does not exist: {self.path}")
        files = sorted(path for path in self.path.rglob("*") if path.is_file())
        if not files or not (self.path / "config.json").is_file():
            raise ValueError("Local embedding model requires config.json and local weights/tokenizer/processor files")
        return files

    def _state(self, files):
        return tuple((str(path.relative_to(self.path)), path.stat().st_size,
                      path.stat().st_mtime_ns, path.stat().st_ctime_ns, path.stat().st_ino)
                     for path in files)

    def _unchanged(self):
        if self._state(self._files()) != self._snapshot:
            raise RuntimeError("Local embedding model files changed; create a new provider and rebuild semantic results")

    def identity(self) -> dict:
        if self._identity is None:
            libraries = _library_versions("torch", "transformers", "Pillow",
                                          "numpy", "tokenizers", "safetensors", "sentencepiece", "protobuf")
            files = self._files()
            before = self._state(files)
            digest = hashlib.sha256()
            for path in files:
                digest.update(str(path.relative_to(self.path)).encode("utf-8"))
                digest.update(b"\0")
                content = hashlib.sha256()
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        content.update(block)
                digest.update(content.digest())
            config_path = self.path / "config.json"
            with config_path.open("rb") as stream:
                config_data = stream.read(MAX_RESPONSE_BYTES + 1)
            if len(config_data) > MAX_RESPONSE_BYTES:
                raise ValueError("Local model config exceeds size limit")
            config = _json(config_data, "model config")
            model_type = config.get("model_type")
            if model_type not in ("clip", "siglip"):
                raise ValueError("Local embedding model must be CLIP or SigLIP")
            if self._state(self._files()) != before:
                raise RuntimeError("Local embedding model files changed while computing identity")
            self._snapshot = before
            self._identity = {
                "provider": "local-transformers", "model": model_type,
                "model_type": model_type, "model_digest": "sha256:" + digest.hexdigest(),
                "preprocessing": dict(PREPROCESSING, processor="local-model-slow", device="cpu",
                                      dtype="float32", normalization="l2"),
                "libraries": libraries,
            }
        else:
            self._unchanged()
        # Callers cannot mutate the provider's pinned cache identity.
        return json.loads(json.dumps(self._identity))

    def _load(self):
        identity = self.identity()
        if self._model is not None:
            return
        try:
            torch = import_module("torch")
            transformers = import_module("transformers")
            prefix = "CLIP" if identity["model_type"] == "clip" else "Siglip"
            model_class = getattr(transformers, prefix + "Model")
            processor_class = getattr(transformers, prefix + "Processor")
            processor = processor_class.from_pretrained(
                str(self.path), local_files_only=True, trust_remote_code=False, use_fast=False)
            model = model_class.from_pretrained(
                str(self.path), local_files_only=True, trust_remote_code=False)
            model = model.to(device="cpu", dtype=torch.float32).eval()
        except (ImportError, AttributeError) as error:
            raise ValueError("Local embeddings require compatible torch and transformers; install storylens[semantic]") from error
        except (OSError, ValueError, RuntimeError) as error:
            raise ValueError(f"Cannot load complete local embedding model: {error}") from error
        self._unchanged()
        self._torch, self._model, self._processor = torch, model, processor

    def _features(self, **inputs) -> list[float]:
        self._load()
        try:
            processed = self._processor(return_tensors="pt", **inputs)
            processed = {key: value.to("cpu") for key, value in processed.items()}
            method = self._model.get_image_features if "images" in inputs else self._model.get_text_features
            with self._torch.inference_mode():
                features = method(**processed)
            values = features.detach().cpu().tolist()
            if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], list):
                raise ValueError("Embedding model must return one feature vector")
            vector = [float(value) for value in values[0]]
        except (TypeError, AttributeError, OverflowError) as error:
            raise ValueError("Embedding model returned invalid features") from error
        self._unchanged()
        if not vector or not all(math.isfinite(value) for value in vector):
            raise ValueError("Embedding model returned empty or non-finite features")
        norm = math.hypot(*vector)
        if not math.isfinite(norm) or norm == 0:
            raise ValueError("Embedding model returned a zero or invalid feature norm")
        if self._dimension is not None and len(vector) != self._dimension:
            raise ValueError("Image and text embedding dimensions do not match")
        self._dimension = len(vector)
        return [value / norm for value in vector]

    def image(self, path: Path) -> list[float]:
        with _frame(path) as image:
            return self._features(images=image)

    def text(self, text: str) -> list[float]:
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
            raise ValueError("Embedding text must be nonempty and at most 32768 characters")
        # SigLIP's text encoder uses fixed-length padding; CLIP accepts it too.
        return self._features(text=[text], padding="max_length", truncation=True)


class OllamaVision:
    """Vision captions from an already-installed, digest-pinned loopback model."""

    def __init__(self, endpoint: str, model: str, timeout: float):
        try:
            parsed = urlsplit(endpoint)
            address = ipaddress.ip_address(parsed.hostname or "")
            port = parsed.port
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("Ollama endpoint must use a numeric loopback IP address") from error
        if (parsed.scheme not in ("http", "https") or not address.is_loopback
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or port == 0 or "%" in (parsed.hostname or "")):
            raise ValueError("Ollama endpoint must be an explicit loopback-only HTTP(S) origin")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Ollama timeout must be a finite positive number")
        if not isinstance(model, str) or not model or len(model) > 256 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@-]*", model):
            raise ValueError("Ollama model must be an installed local model name")
        self._reject_remote({"model": model})
        self._scheme, self._host = parsed.scheme, str(address)
        self._port = port or (443 if parsed.scheme == "https" else 80)
        host = f"[{address}]" if address.version == 6 else str(address)
        self.endpoint = f"{parsed.scheme}://{host}:{self._port}"
        self.model, self.timeout = model, float(timeout)
        self._identity = None
        self._name = None
        self._digest = None

    @staticmethod
    def _reject_remote(value):
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, dict):
                for key, nested in item.items():
                    if ("remote" in key.lower() or "cloud" in key.lower()) and nested:
                        raise ValueError("Remote/cloud Ollama models are not allowed")
                    pending.append(nested)
            elif isinstance(item, list):
                pending.extend(item)
            elif isinstance(item, str):
                if re.search(r"(^|[/:._-])(cloud|remote)([/:._-]|$)|https?://", item, re.I):
                    raise ValueError("Remote/cloud Ollama models are not allowed")

    def _request(self, route: str, payload=None) -> dict:
        # http.client connects directly to the literal IP: no proxy environment,
        # DNS lookup of a supplied hostname, redirect handling, or model pulls.
        connection_class = http.client.HTTPSConnection if self._scheme == "https" else http.client.HTTPConnection
        connection = connection_class(self._host, self._port, timeout=self.timeout)
        deadline = time.monotonic() + self.timeout
        body = None if payload is None else json.dumps(payload, allow_nan=False).encode("utf-8")
        try:
            connection.request("GET" if payload is None else "POST", route, body=body,
                               headers={"Content-Type": "application/json", "Accept": "application/json"})
            response = connection.getresponse()
            if response.status != 200:
                raise RuntimeError(f"Local Ollama {route} returned HTTP {response.status}; redirects are not allowed")
            content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                raise ValueError("Local Ollama returned a non-JSON content type")
            length = response.getheader("Content-Length")
            if length is not None:
                try:
                    size = int(length)
                except ValueError as error:
                    raise ValueError("Invalid Ollama Content-Length") from error
                if size < 0 or size > MAX_RESPONSE_BYTES:
                    raise ValueError("Local Ollama response exceeds size limit")
            result = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise OSError("Local Ollama request timed out")
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - len(result)))
                if not chunk:
                    break
                result.extend(chunk)
                if len(result) > MAX_RESPONSE_BYTES:
                    raise ValueError("Local Ollama response exceeds size limit")
            if length is not None and len(result) != size:
                raise ValueError("Truncated local Ollama response")
        except (http.client.HTTPException, socket.timeout) as error:
            raise OSError(f"Local Ollama request failed: {error}") from error
        finally:
            connection.close()
        data = _json(result, "Ollama response")
        if "error" in data:
            raise RuntimeError("Local Ollama returned a model error")
        return data

    @staticmethod
    def _normalize_digest(value):
        if not isinstance(value, str) or not re.fullmatch(r"(?:sha256:)?[0-9a-fA-F]{64}", value):
            raise ValueError("Installed Ollama model must have a SHA256 digest")
        return "sha256:" + value.removeprefix("sha256:").lower()

    def _installed(self):
        data = self._request("/api/tags")
        models = data.get("models")
        if not isinstance(models, list):
            raise ValueError("Invalid Ollama tags: expected models list")
        matches = []
        requested = self.model if ":" in self.model.rsplit("/", 1)[-1] else self.model + ":latest"
        for item in models:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise ValueError("Invalid Ollama tags model entry")
            if item["name"] in (self.model, requested):
                self._reject_remote(item)
                matches.append((item["name"], self._normalize_digest(item.get("digest"))))
        if len(matches) != 1:
            raise ValueError("Requested Ollama model must be uniquely installed locally; models are never pulled")
        name, digest = matches[0]
        if self._digest is not None and (name != self._name or digest != self._digest):
            raise RuntimeError("Installed Ollama model changed; create a new provider and rebuild semantic results")
        return name, digest

    def _cloud_disabled(self):
        try:
            status = self._request("/api/status")
        except RuntimeError as error:
            raise ValueError("Ollama must support /api/status and have daemon cloud disabled; set OLLAMA_NO_CLOUD=1 on the daemon and restart it") from error
        cloud = status.get("cloud")
        if not isinstance(cloud, dict) or cloud.get("disabled") is not True:
            raise ValueError("Ollama daemon cloud must be disabled; set OLLAMA_NO_CLOUD=1 on the daemon and restart it")

    def identity(self) -> dict:
        self._cloud_disabled()
        runtime_version = self._request("/api/version").get("version")
        if not isinstance(runtime_version, str) or not runtime_version or len(runtime_version) > 128:
            raise ValueError("Local Ollama must report a runtime version")
        if self._identity is not None and runtime_version != self._identity["libraries"]["ollama"]:
            raise RuntimeError("Local Ollama runtime changed; create a new provider and rebuild captions")
        name, digest = self._installed()
        if self._identity is None:
            details = self._request("/api/show", {"model": name})
            # Templates/licenses contain arbitrary prose and URLs, not runtime
            # routing information. Inspect model routing and metadata instead.
            self._reject_remote({key: value for key, value in details.items()
                                 if key not in ("template", "license", "modelfile", "parameters", "system")})
            capabilities = details.get("capabilities")
            if not isinstance(capabilities, list) or not all(isinstance(value, str) for value in capabilities) or "vision" not in capabilities:
                raise ValueError("Installed Ollama model does not advertise vision capability")
            if "digest" in details and self._normalize_digest(details["digest"]) != digest:
                raise RuntimeError("Installed Ollama model changed during inspection")
            if self._installed() != (name, digest):
                raise RuntimeError("Installed Ollama model changed during inspection")
            self._name, self._digest = name, digest
            self._identity = {
                "provider": "ollama-local", "endpoint": self.endpoint, "model": name,
                "model_digest": digest, "capabilities": sorted(capabilities),
                "options": dict(GENERATION_OPTIONS), "prompt": CAPTION_PROMPT,
                "preprocessing": dict(PREPROCESSING, encoding="png"),
                "libraries": {"Pillow": version("Pillow"), "ollama": runtime_version},
            }
        return json.loads(json.dumps(self._identity))

    def describe(self, path: Path) -> str:
        self.identity()
        with _frame(path) as image:
            encoded = io.BytesIO()
            image.save(encoded, format="PNG")
            encoded_image = base64.b64encode(encoded.getvalue()).decode("ascii")
        self._installed()
        self._cloud_disabled()
        response = self._request("/api/generate", {
            "model": self._name, "prompt": CAPTION_PROMPT, "images": [encoded_image],
            "stream": False, "options": dict(GENERATION_OPTIONS),
        })
        self.identity()
        caption = response.get("response")
        if response.get("done") is not True or response.get("model") != self._name:
            raise ValueError("Invalid Ollama generation model/completion response")
        if not isinstance(caption, str) or not caption.strip() or len(caption) > MAX_TEXT_CHARS:
            raise ValueError("Local Ollama returned an empty or invalid caption")
        return caption.strip()
