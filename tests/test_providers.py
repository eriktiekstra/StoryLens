"""Provider privacy, bounded transport, model provenance and feature contracts."""

import base64
from contextlib import nullcontext
import io
import json
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

from storylens import providers
from storylens.providers import LocalEmbeddings, OllamaVision


class Tensor:
    def __init__(self, value):
        self.value = value

    def to(self, *args, **kwargs):
        return self

    def detach(self):
        return self

    def cpu(self):
        return self

    def tolist(self):
        return self.value


class LocalEmbeddingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.model_path = self.base / "model"
        self.model_path.mkdir()
        (self.model_path / "config.json").write_text('{"model_type":"clip"}')
        (self.model_path / "model.safetensors").write_bytes(b"weights")
        (self.model_path / "tokenizer.json").write_bytes(b"tokenizer")
        (self.model_path / "preprocessor_config.json").write_text('{"size":224}')
        self.image_path = self.base / "image.png"
        Image.new("RGB", (16, 10), "red").save(self.image_path)
        self.version_patch = patch.object(providers, "version", return_value="test-version")
        self.version_patch.start()
        self.addCleanup(self.version_patch.stop)
        self.loads = []
        self.calls = []
        self.image_features = [[3.0, 4.0]]
        self.text_features = [[4.0, 3.0]]
        self.mutate = None
        owner = self

        class Model:
            @classmethod
            def from_pretrained(cls, path, **kwargs):
                owner.loads.append(("model", path, kwargs))
                return cls()

            def to(self, **kwargs):
                owner.calls.append(("device", kwargs))
                return self

            def eval(self):
                return self

            def get_image_features(self, **kwargs):
                if owner.mutate:
                    owner.mutate()
                return Tensor(owner.image_features)

            def get_text_features(self, **kwargs):
                return Tensor(owner.text_features)

        class Processor:
            @classmethod
            def from_pretrained(cls, path, **kwargs):
                owner.loads.append(("processor", path, kwargs))
                return cls()

            def __call__(self, **kwargs):
                owner.calls.append(("process", kwargs))
                return {"pixel_values" if "images" in kwargs else "input_ids": Tensor([])}

        self.torch = SimpleNamespace(float32="float32", inference_mode=nullcontext)
        self.transformers = SimpleNamespace(CLIPModel=Model, CLIPProcessor=Processor,
                                            SiglipModel=Model, SiglipProcessor=Processor)
        self.imports = []

        def import_runtime(name):
            self.imports.append(name)
            return {"torch": self.torch, "transformers": self.transformers}[name]
        self.import_patch = patch.object(providers, "import_module", side_effect=import_runtime)
        self.import_patch.start()
        self.addCleanup(self.import_patch.stop)

    def test_features_are_normalized_and_identity_cannot_be_mutated(self):
        provider = LocalEmbeddings(str(self.model_path))
        identity = provider.identity()
        self.assertEqual(provider.image(self.image_path), [0.6, 0.8])
        self.assertEqual(provider.text("a red image"), [0.8, 0.6])
        identity["libraries"]["torch"] = "changed"
        self.assertNotEqual(identity, provider.identity())

    def test_content_identity_covers_weights_tokenizer_processor_and_versions(self):
        baseline = LocalEmbeddings(str(self.model_path)).identity()
        for filename in ("model.safetensors", "config.json", "tokenizer.json", "preprocessor_config.json"):
            path = self.model_path / filename
            original = path.read_bytes()
            path.write_bytes(original + b" ")
            self.assertNotEqual(LocalEmbeddings(str(self.model_path)).identity()["model_digest"], baseline["model_digest"])
            path.write_bytes(original)
        with patch.object(providers, "version", return_value="next-version"):
            self.assertNotEqual(LocalEmbeddings(str(self.model_path)).identity(), baseline)

    def test_mutations_before_and_during_inference_are_rejected(self):
        provider = LocalEmbeddings(str(self.model_path))
        provider.identity()
        (self.model_path / "tokenizer.json").write_bytes(b"changed")
        with self.assertRaisesRegex(RuntimeError, "changed"):
            provider.text("red")
        self.assertEqual(self.loads, [])
        provider = LocalEmbeddings(str(self.model_path))
        self.mutate = lambda: (self.model_path / "model.safetensors").write_bytes(b"new weights")
        with self.assertRaisesRegex(RuntimeError, "changed"):
            provider.image(self.image_path)

    def test_model_load_mutation_is_rejected(self):
        provider = LocalEmbeddings(str(self.model_path))
        original = self.transformers.CLIPModel.from_pretrained

        def load(*args, **kwargs):
            (self.model_path / "tokenizer.json").write_bytes(b"replaced during load")
            return original(*args, **kwargs)
        with patch.object(self.transformers.CLIPModel, "from_pretrained", side_effect=load):
            with self.assertRaisesRegex(RuntimeError, "changed"):
                provider.text("red")

    def test_finite_nonzero_consistent_features_required(self):
        for features in ([], [[]], [[0, 0]], [[math.nan, 1]], [[math.inf, 1]], [[None, 1]], [[1], [2]]):
            with self.subTest(features=features):
                self.image_features = features
                with self.assertRaises(ValueError):
                    LocalEmbeddings(str(self.model_path)).image(self.image_path)
        self.image_features = [[3, 4]]
        provider = LocalEmbeddings(str(self.model_path))
        provider.image(self.image_path)
        self.text_features = [[1, 2, 3]]
        with self.assertRaisesRegex(ValueError, "dimensions"):
            provider.text("red")

    def test_moving_identical_model_preserves_embedding_space_identity(self):
        provider = LocalEmbeddings(str(self.model_path))
        identity = provider.identity()
        renamed = self.base / "different-name"
        self.model_path.rename(renamed)
        self.assertEqual(LocalEmbeddings(str(renamed)).identity(), identity)

    def test_missing_dependencies_files_and_unsupported_models_are_explicit(self):
        for model_path in ("", " ", None):
            with self.assertRaisesRegex(ValueError, "explicit nonempty"):
                LocalEmbeddings(model_path)
        with patch.object(providers, "version", side_effect=providers.PackageNotFoundError("torch")):
            with self.assertRaisesRegex(ValueError, "install storylens"):
                LocalEmbeddings(str(self.model_path)).identity()
        with self.assertRaisesRegex(ValueError, "does not exist"):
            LocalEmbeddings(str(self.base / "missing")).identity()
        (self.model_path / "config.json").write_text('{"model_type":"custom"}')
        with self.assertRaisesRegex(ValueError, "CLIP or SigLIP"):
            LocalEmbeddings(str(self.model_path)).identity()
        (self.model_path / "config.json").write_text('{"model_type":"clip"}')
        with patch.object(providers, "import_module", side_effect=ImportError("torch")):
            with self.assertRaisesRegex(ValueError, "install storylens"):
                LocalEmbeddings(str(self.model_path)).text("red")
        with patch.object(self.transformers.CLIPModel, "from_pretrained", side_effect=OSError("missing local weights")):
            with self.assertRaisesRegex(ValueError, "complete local embedding model"):
                LocalEmbeddings(str(self.model_path)).text("red")

    def test_invalid_text_images_and_interrupt_are_not_hidden(self):
        provider = LocalEmbeddings(str(self.model_path))
        for text in (None, "", " ", "a" * (providers.MAX_TEXT_CHARS + 1)):
            with self.assertRaises(ValueError):
                provider.text(text)
        self.image_path.write_bytes(b"not image bytes")
        with self.assertRaises(OSError):
            provider.image(self.image_path)
        with patch.object(providers, "import_module", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                provider.text("red")


class OllamaTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.image_path = self.base / "private-name.png"
        Image.new("RGB", (32, 24), "red").save(self.image_path)
        self.digest = "a" * 64
        self.disabled = True
        self.capabilities = ["completion", "vision"]
        self.extra_tag = {}
        self.extra_show = {}
        self.caption = " A red rectangle. "
        self.done = True
        self.returned_model = "vision:latest"
        self.requests = []
        self.overrides = {}
        self.after_generate = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                self.handle_request()

            def do_POST(self):
                self.handle_request()

            def handle_request(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else None
                owner.requests.append((self.command, self.path, body))
                status, headers, delay = 200, {}, 0
                if self.path in owner.overrides:
                    value = owner.overrides[self.path]
                    value = value() if callable(value) else value
                    if isinstance(value, tuple):
                        status, headers, value, delay = value
                elif self.path == "/api/status":
                    value = {"cloud": {"disabled": owner.disabled, "source": "env"}}
                elif self.path == "/api/version":
                    value = {"version": "0.40.4"}
                elif self.path == "/api/tags":
                    value = {"models": [{"name": "vision:latest", "digest": owner.digest, **owner.extra_tag}]}
                elif self.path == "/api/show":
                    value = {"capabilities": owner.capabilities, **owner.extra_show}
                elif self.path == "/api/generate":
                    value = {"model": owner.returned_model, "done": owner.done, "response": owner.caption}
                    if owner.after_generate:
                        owner.after_generate()
                else:
                    status, value = 404, {"error": "not found"}
                encoded = value if isinstance(value, bytes) else json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", headers.pop("Content-Type", "application/json"))
                content_length = headers.pop("Content-Length", str(len(encoded)))
                if content_length is not None:
                    self.send_header("Content-Length", content_length)
                self.send_header("Connection", "close")
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                if delay:
                    time.sleep(delay)
                try:
                    self.wfile.write(encoded)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}"

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def provider(self, **kwargs):
        return OllamaVision(self.endpoint, "vision", kwargs.get("timeout", 1))

    def generated(self):
        return [body for _, path, body in self.requests if path == "/api/generate"]

    def test_explicit_loopback_only_origins_and_model_validation(self):
        invalid = ("http://localhost:11434", "http://example.com", "http://0.0.0.0", "http://192.168.0.1",
                   "http://127.0.0.1.evil.test", "http://2130706433", "http://127.1", "http://127.0.0.1/path",
                   "http://user@127.0.0.1", "http://127.0.0.1?x=1", "http://127.0.0.1#fragment",
                   "ftp://127.0.0.1", "http://[::1%25eth0]", "http://127.0.0.1:bad", "http://127.0.0.1:0")
        for endpoint in invalid:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                OllamaVision(endpoint, "vision", 1)
        self.assertEqual(OllamaVision("http://[::1]:11434/", "vision", 1).endpoint, "http://[::1]:11434")
        for timeout in (0, -1, math.inf, math.nan, True, "10"):
            with self.assertRaises(ValueError):
                OllamaVision(self.endpoint, "vision", timeout)
        for model in ("", "vision-cloud", "cloud/model", "vision:remote", "https://ollama.com/model", "bad name"):
            with self.assertRaises(ValueError):
                OllamaVision(self.endpoint, model, 1)
        self.assertEqual(self.requests, [])

    def test_caption_identity_direct_proxy_bypass_and_no_metadata(self):
        exif = Image.Exif()
        exif[274], exif[315] = 6, "SECRET-PERSON"
        Image.new("RGB", (600, 1800), "red").save(self.image_path, exif=exif)
        before = self.image_path.read_bytes()
        provider = self.provider()
        with patch.dict("os.environ", {"HTTP_PROXY": "http://127.0.0.1:1", "HTTPS_PROXY": "http://127.0.0.1:1", "ALL_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}):
            self.assertEqual(provider.describe(self.image_path), "A red rectangle.")
        identity = provider.identity()
        self.assertEqual(identity["model_digest"], "sha256:" + self.digest)
        identity["options"]["temperature"] = 99
        self.assertEqual(provider.identity()["options"]["temperature"], 0)
        payload = self.generated()[0]
        image_bytes = base64.b64decode(payload["images"][0])
        self.assertNotIn(b"SECRET-PERSON", image_bytes)
        self.assertNotIn("private-name", json.dumps(payload))
        with Image.open(io.BytesIO(image_bytes)) as image:
            self.assertEqual(image.mode, "RGB")
            self.assertEqual(image.size, (1024, 341))
            self.assertEqual(dict(image.getexif()), {})
        self.assertEqual(self.image_path.read_bytes(), before)
        self.assertTrue(all(path in ("/api/status", "/api/version", "/api/tags", "/api/show", "/api/generate") for _, path, _ in self.requests))

    def test_cloud_disabled_is_required_before_any_model_inspection(self):
        for disabled in (False, "true", None):
            self.disabled = disabled
            with self.assertRaisesRegex(ValueError, "OLLAMA_NO_CLOUD"):
                self.provider().describe(self.image_path)
        self.assertTrue(all(path == "/api/status" for _, path, _ in self.requests))
        self.overrides["/api/status"] = (404, {}, {"error": "not supported"}, 0)
        with self.assertRaisesRegex(ValueError, "/api/status"):
            self.provider().identity()
        self.assertEqual(self.generated(), [])

    def test_remote_aliases_and_missing_vision_are_rejected_without_generation(self):
        for fields in ({"remote_host": "ollama.com"}, {"remote_model": "hidden-cloud"}, {"details": {"remote_host": "ollama.com"}}):
            self.extra_tag = fields
            with self.assertRaisesRegex(ValueError, "Remote/cloud"):
                self.provider().describe(self.image_path)
        self.extra_tag = {}
        self.extra_show = {"remote_host": "ollama.com", "remote_model": "alias"}
        with self.assertRaisesRegex(ValueError, "Remote/cloud"):
            self.provider().describe(self.image_path)
        self.extra_show = {}
        for capabilities in ([], ["completion"], "vision", ["vision", 1]):
            self.capabilities = capabilities
            with self.assertRaisesRegex(ValueError, "vision capability"):
                self.provider().describe(self.image_path)
        self.assertEqual(self.generated(), [])

    def test_missing_duplicate_invalid_digest_and_changed_model_rejected(self):
        for tags in ({"models": []}, {"models": "bad"}, {"models": [None]},
                     {"models": [{"name": "vision:latest", "digest": "bad"}]},
                     {"models": [{"name": "vision:latest", "digest": self.digest}] * 2}):
            self.overrides["/api/tags"] = tags
            with self.assertRaises(ValueError):
                self.provider().identity()
        del self.overrides["/api/tags"]
        provider = self.provider()
        provider.identity()
        self.digest = "b" * 64
        with self.assertRaisesRegex(RuntimeError, "changed"):
            provider.describe(self.image_path)
        self.assertEqual(self.generated(), [])

    def test_mutation_during_inspection_or_generation_discards_result(self):
        def show():
            self.digest = "b" * 64
            return {"capabilities": ["vision"]}
        self.overrides["/api/show"] = show
        with self.assertRaisesRegex(RuntimeError, "changed"):
            self.provider().identity()
        del self.overrides["/api/show"]
        provider = self.provider()
        self.after_generate = lambda: setattr(self, "digest", "c" * 64)
        with self.assertRaisesRegex(RuntimeError, "changed"):
            provider.describe(self.image_path)
        self.assertEqual(len(self.generated()), 1)

    def test_cloud_reenabled_during_generation_discards_caption(self):
        self.after_generate = lambda: setattr(self, "disabled", False)
        with self.assertRaisesRegex(ValueError, "OLLAMA_NO_CLOUD"):
            self.provider().describe(self.image_path)
        self.assertEqual(len(self.generated()), 1)

    def test_redirect_is_denied_and_never_followed(self):
        self.overrides["/api/status"] = (302, {"Location": self.endpoint + "/redirect-target"}, b"", 0)
        with self.assertRaisesRegex(ValueError, "/api/status"):
            self.provider().identity()
        self.assertEqual([path for _, path, _ in self.requests], ["/api/status"])

    def test_http_failure_bad_json_schema_and_response_bounds(self):
        for response in ((500, {}, {"error": "failed"}, 0),
                         (200, {"Content-Type": "text/html"}, b"{}", 0),
                         (200, {}, b"not json", 0), (200, {}, b"[]", 0),
                         (200, {}, b'{"models":NaN}', 0),
                         (200, {"Content-Length": str(providers.MAX_RESPONSE_BYTES + 1)}, b"{}", 0),
                         (200, {"Content-Length": "invalid"}, b"{}", 0),
                         (200, {"Content-Length": "100"}, b"{}", 0)):
            self.overrides["/api/tags"] = response
            with self.subTest(response=response), self.assertRaises((ValueError, OSError, RuntimeError)):
                self.provider().identity()
        self.overrides["/api/tags"] = (200, {"Content-Length": None}, b"x" * 1025, 0)
        with patch.object(providers, "MAX_RESPONSE_BYTES", 1024):
            with self.assertRaisesRegex(ValueError, "size limit"):
                self.provider().identity()
        self.overrides["/api/tags"] = (200, {}, b"x" * 1025, 0)
        with patch.object(providers, "MAX_RESPONSE_BYTES", 1024):
            with self.assertRaisesRegex(ValueError, "size limit"):
                self.provider().identity()
        self.assertEqual(self.generated(), [])

    def test_timeout_is_bounded_and_not_retried(self):
        self.overrides["/api/status"] = (200, {}, {"cloud": {"disabled": True}}, 0.1)
        with self.assertRaises(OSError):
            self.provider(timeout=0.02).identity()
        self.assertEqual([path for _, path, _ in self.requests], ["/api/status"])

    def test_caption_schema_error_and_interrupt_boundaries(self):
        provider = self.provider()
        for caption in (None, "", " ", 42, "x" * (providers.MAX_TEXT_CHARS + 1)):
            self.caption = caption
            with self.assertRaises(ValueError):
                provider.describe(self.image_path)
        self.caption = "red"
        self.done = False
        with self.assertRaisesRegex(ValueError, "completion"):
            provider.describe(self.image_path)
        self.done = True
        self.returned_model = "different:latest"
        with self.assertRaisesRegex(ValueError, "model"):
            provider.describe(self.image_path)
        self.overrides["/api/generate"] = {"error": "inference failed"}
        with self.assertRaises(RuntimeError):
            provider.describe(self.image_path)
        with patch.object(providers.http.client.HTTPConnection, "request", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                provider.identity()

    def test_pixel_limit_and_alpha_background(self):
        Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(self.image_path)
        provider = self.provider()
        provider.describe(self.image_path)
        encoded = base64.b64decode(self.generated()[-1]["images"][0])
        with Image.open(io.BytesIO(encoded)) as image:
            self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))
        with patch.object(providers, "MAX_PIXELS", 20):
            with self.assertRaisesRegex(ValueError, "pixel limit"):
                provider.describe(self.image_path)
        self.assertEqual(len(self.generated()), 1)

    def test_runtime_change_invalidates_identity_and_discards_inflight_caption(self):
        provider = self.provider()
        previous = provider.identity()
        self.overrides["/api/version"] = {"version": "new-runtime"}
        self.assertNotEqual(self.provider().identity(), previous)
        with self.assertRaisesRegex(RuntimeError, "runtime changed"):
            provider.describe(self.image_path)
        provider = self.provider()
        provider.identity()
        self.after_generate = lambda: self.overrides.update({"/api/version": {"version": "changed-again"}})
        with self.assertRaisesRegex(RuntimeError, "runtime changed"):
            provider.describe(self.image_path)


if __name__ == "__main__":
    unittest.main()
