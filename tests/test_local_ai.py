"""Guided setup exercises disk persistence and a real loopback HTTP boundary."""

import base64
import fnmatch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

from storylens import local_ai
from storylens.config import Config


class SetupFilesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.storage = self.base / "user-data"
        root_patch = patch.object(local_ai, "_root", return_value=self.storage)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def model(self, capability="embeddings"):
        path = self.base / capability
        path.mkdir(exist_ok=True)
        if capability == "embeddings":
            files = {"config.json": b'{"model_type":"clip"}', "model.safetensors": b"local weights",
                     "tokenizer.json": b"{}", "preprocessor_config.json": b"{}"}
        else:
            files = {"config.json": b"{}", "model.bin": b"local weights", "tokenizer.json": b"{}"}
        for name, content in files.items():
            (path / name).write_bytes(content)
        return path

    def test_profile_is_explicit_filtered_atomic_and_private(self):
        self.assertEqual(local_ai.load_profile(), {})
        self.assertFalse(self.storage.exists())
        model = self.model()
        saved = local_ai.save_profile({"embedding_model": str(model), "audio_transcription_device": "cpu",
                                       "source": "/private/source", "database": "private.sqlite3", "timeout": 99})
        self.assertEqual(saved, {"embedding_model": str(model), "audio_transcription_device": "cpu"})
        self.assertEqual(local_ai.load_profile(), saved)
        path = self.storage / "ai-profile.json"
        self.assertEqual(json.loads(path.read_text()), saved)
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.storage.glob(".ai-profile-*")), [])
        local_ai.save_profile({"vision_model": "moondream"})
        self.assertEqual(local_ai.load_profile(), {"vision_model": "moondream"})

    def test_profiles_reject_unsafe_paths_shapes_and_model_settings(self):
        self.storage.mkdir()
        target = self.base / "elsewhere.json"
        target.write_text("{}")
        profile = self.storage / "ai-profile.json"
        profile.symlink_to(target)
        with self.assertRaises(ValueError):
            local_ai.load_profile()
        with self.assertRaises(ValueError):
            local_ai.save_profile({})
        self.assertEqual(target.read_text(), "{}")
        profile.unlink()
        for raw in ('[]', '{"source":"/private"}', '{"vision_model":false}', 'x' * 65537):
            profile.write_text(raw)
            with self.subTest(raw=raw[:20]), self.assertRaises(ValueError):
                local_ai.load_profile()
        for values in ({"ollama_endpoint": "https://example.com"}, {"audio_transcription_device": "auto"},
                       {"embedding_model": "bad\0path"}, {"semantic_timeout": float("nan")}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                local_ai.save_profile(values)

    def test_no_capability_is_not_certified_ready_and_dependency_commands_are_usable(self):
        empty = local_ai.check({})
        self.assertFalse(empty["ready"])
        self.assertEqual(empty["errors"], [])
        with patch.object(local_ai, "version", side_effect=local_ai.PackageNotFoundError("torch")):
            result = local_ai.check({"embedding_model": str(self.model())})
        self.assertFalse(result["ready"])
        self.assertIn(sys.executable, result["errors"][0])
        self.assertIn("semantic,model-installer", result["errors"][0])

    def test_local_files_checked_without_loading_inference_runtimes(self):
        model = self.model()
        with patch.object(local_ai, "version", return_value="test-installed"):
            ready = local_ai.check({"embedding_model": str(model)})
            self.assertTrue(ready["ready"], ready)
            (model / "model.safetensors").unlink()
            missing = local_ai.check({"embedding_model": str(model)})
        self.assertFalse(missing["ready"])
        self.assertIn("weights", missing["errors"][0])
        with patch.object(local_ai, "version", return_value="test-installed"):
            speech = local_ai.check({"audio_transcription_model": str(self.model("transcription"))})
        self.assertTrue(speech["ready"], speech)
        self.assertNotIn("embedding_model", speech["settings"])

    def test_download_requires_true_boolean_and_known_capability(self):
        for consent in (False, None, "true", 1):
            with self.subTest(consent=consent), self.assertRaisesRegex(ValueError, "boolean consent"):
                local_ai.install("embeddings", consent=consent)
        with self.assertRaises(TypeError):
            local_ai.install("embeddings")
        with self.assertRaises(ValueError):
            local_ai.install("https://unapproved.example/model", consent=True)
        self.assertFalse(self.storage.exists())

    def download_fixture(self, weight_files):
        assets = {
            "config.json": '{"model_type":"clip"}',
            "tokenizer.json": '{"version":"1.0"}',
            "preprocessor_config.json": '{"size":224}',
            "modeling_custom.py": "raise RuntimeError('remote code must not be installed')",
            **{name: "fixture weights" for name in weight_files},
        }
        repositories = {"openai/clip-vit-base-patch32": assets}

        def metadata(*, repo_id, **kwargs):
            files = repositories[repo_id]
            return SimpleNamespace(sha="b" * 40, siblings=[
                SimpleNamespace(rfilename=name) for name in files])

        def download(*, repo_id, local_dir, allow_patterns, **kwargs):
            target = Path(local_dir)
            for name, content in repositories[repo_id].items():
                if any(fnmatch.fnmatchcase(name, pattern) for pattern in allow_patterns):
                    (target / name).write_text(content)
            return str(target)

        return SimpleNamespace(snapshot_download=download, HfApi=lambda **kwargs: SimpleNamespace(model_info=metadata))

    def test_download_installs_one_weight_format_and_reusable_assets(self):
        local_ai.save_profile({"audio_transcription_model": "/saved-only-transcription"})
        hub = self.download_fixture(["model.safetensors", "pytorch_model.bin"])
        with patch.dict(sys.modules, {"huggingface_hub": hub}):
            result = local_ai.install("embeddings", consent=True)
        target = Path(result["model_path"])
        self.assertEqual(target, self.storage / "models/clip-vit-base-patch32")
        self.assertEqual({path.name for path in target.iterdir()}, {
            "config.json", "tokenizer.json", "preprocessor_config.json", "model.safetensors"})
        self.assertEqual(json.loads((target / "tokenizer.json").read_text()), {"version": "1.0"})
        self.assertEqual(local_ai.load_profile()["embedding_model"], str(target))
        self.assertEqual(local_ai.load_profile()["audio_transcription_model"], "/saved-only-transcription")
        self.assertNotIn("audio_transcription_model", result["settings"])
        self.assertTrue(result["size_is_approximate"])

    def test_download_falls_back_to_single_pytorch_weight_when_needed(self):
        with patch.dict(sys.modules, {"huggingface_hub": self.download_fixture(["pytorch_model.bin"])}):
            result = local_ai.install("embeddings", consent=True)
        target = Path(result["model_path"])
        self.assertEqual({path.name for path in target.iterdir()}, {
            "config.json", "tokenizer.json", "preprocessor_config.json", "pytorch_model.bin"})
        self.assertEqual(local_ai.load_profile()["embedding_model"], str(target))
        self.assertEqual(result["settings"], {"embedding_model": str(target)})

    def test_download_refuses_symlinked_fixed_storage(self):
        target = self.storage / "models/clip-vit-base-patch32"
        target.mkdir(parents=True)
        external = self.base / "external"
        external.write_text("not a model")
        (target / "config.json").symlink_to(external)
        with patch.dict(sys.modules, {"huggingface_hub": SimpleNamespace(snapshot_download=lambda **kwargs: self.fail("download started"))}):
            with self.assertRaisesRegex(ValueError, "symlinks"):
                local_ai.install("embeddings", consent=True)
        self.assertEqual(external.read_text(), "not a model")



class LocalRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        root_patch = patch.object(local_ai, "_root", return_value=self.base / "user-data")
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.requests = []
        self.installed = True
        self.cloud_disabled = True
        self.vision_capability = True
        self.delay = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.respond()

            def do_POST(self):
                self.respond()

            def respond(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else None
                owner.requests.append((self.path, body))
                status = 200
                if self.path == "/api/status":
                    value = {"cloud": {"disabled": owner.cloud_disabled}}
                elif self.path == "/api/version":
                    value = {"version": "local-boundary-test"}
                elif self.path == "/api/tags":
                    value = {"models": [{"name": "moondream:latest", "digest": "a" * 64}] if owner.installed else []}
                elif self.path == "/api/show":
                    value = {"capabilities": ["vision"] if owner.vision_capability else ["completion"]}
                elif self.path == "/api/pull":
                    owner.installed = True
                    value = {"status": "success"}
                elif self.path == "/api/generate":
                    # Decode the actual transmitted image rather than echoing a
                    # canned response or replacing the provider implementation.
                    data = base64.b64decode(body["images"][0], validate=True)
                    with Image.open(io.BytesIO(data)) as image:
                        self.assert_metadata_free(image)
                        color = image.convert("RGB").getpixel((0, 0))
                        size = image.size
                    time.sleep(owner.delay)
                    value = {"model": "moondream:latest", "done": True,
                             "response": f"Received {size[0]}x{size[1]} image with RGB {color}."}
                else:
                    status, value = 404, {"error": "unsupported route"}
                encoded = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                try:
                    self.wfile.write(encoded)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            @staticmethod
            def assert_metadata_free(image):
                if image.getexif():
                    raise AssertionError("Source EXIF must not reach local inference")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}"
        self.settings = {"vision_model": "moondream", "ollama_endpoint": self.endpoint, "semantic_timeout": 5}

    def project(self):
        source = self.base / "private-source"
        source.mkdir(exist_ok=True)
        image = Image.new("RGB", (23, 17), (200, 10, 20))
        exif = Image.Exif()
        exif[270] = "Private capture metadata"
        image.save(source / "private-name.png", exif=exif)
        directory = self.base / "project"
        directory.mkdir(exist_ok=True)
        return SimpleNamespace(source=source, directory=directory, config=Config(directory / "catalog.sqlite3"))

    def test_check_only_reads_local_metadata_and_supports_vision_without_embeddings(self):
        ready = local_ai.check(self.settings)
        self.assertTrue(ready["ready"], ready)
        self.assertTrue(self.requests)
        self.assertFalse(any(route in ("/api/pull", "/api/generate") for route, body in self.requests))
        self.cloud_disabled = False
        self.assertFalse(local_ai.check(self.settings)["ready"])
        self.cloud_disabled = True
        self.vision_capability = False
        self.assertFalse(local_ai.check(self.settings)["ready"])

    def test_external_endpoints_are_rejected_without_any_request(self):
        for endpoint in ("https://example.com", "http://localhost:11434", self.endpoint + "/remote"):
            result = local_ai.check(dict(self.settings, ollama_endpoint=endpoint))
            self.assertFalse(result["ready"])
        self.assertEqual(self.requests, [])

    def test_options_inspect_existing_runtime_and_never_enable_recommended_paths(self):
        local_ai.save_profile({"ollama_endpoint": self.endpoint})
        result = local_ai.options()
        self.assertEqual({item["id"] for item in result["capabilities"]}, {"embeddings", "vision", "transcription"})
        self.assertEqual(result["settings"], {"ollama_endpoint": self.endpoint})
        self.assertNotIn("embedding_model", result["settings"])
        vision = next(item for item in result["capabilities"] if item["id"] == "vision")
        self.assertTrue(vision["installed"])
        self.assertEqual(vision["model_path"], "moondream")
        self.assertFalse(any(route in ("/api/pull", "/api/generate") for route, body in self.requests))

    def test_pull_requires_consent_and_sends_only_fixed_model_to_local_runtime(self):
        local_ai.save_profile({"ollama_endpoint": "http://127.0.0.1:1", "embedding_model": "/saved-only-model"})
        self.installed = False
        with self.assertRaises(ValueError):
            local_ai.install("vision", consent=False)
        self.assertEqual(self.requests, [])
        result = local_ai.install("vision", consent=True, settings={"ollama_endpoint": self.endpoint})
        pulls = [body for route, body in self.requests if route == "/api/pull"]
        self.assertEqual(pulls, [{"model": "moondream", "stream": False}])
        self.assertEqual(local_ai.load_profile()["vision_model"], "moondream")
        self.assertEqual(result["settings"]["ollama_endpoint"], self.endpoint)
        self.assertNotIn("embedding_model", result["settings"])
        self.assertEqual(local_ai.load_profile()["embedding_model"], "/saved-only-model")
        self.assertFalse(any(route == "/api/generate" for route, body in self.requests))

    def test_sample_uses_real_provider_boundary_without_project_or_source_writes(self):
        project = self.project()
        source = project.source / "private-name.png"
        original = source.read_bytes()
        result = local_ai.sample(project, self.settings)
        self.assertTrue(result["ready"], result)
        self.assertEqual(len(result["results"]), 1)
        self.assertIn("23x17", result["results"][0]["caption"])
        self.assertIn("(200, 10, 20)", result["results"][0]["caption"])
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(list(project.directory.iterdir()), [])
        generate = [body for route, body in self.requests if route == "/api/generate"]
        self.assertEqual(len(generate), 1)
        self.assertNotIn("private-name", json.dumps(generate))
        self.assertFalse(any(route == "/api/pull" for route, body in self.requests))

    def test_sample_skips_source_symlinks_and_honors_cancellation(self):
        project = self.project()
        (project.source / "private-name.png").unlink()
        external = self.base / "outside.png"
        Image.new("RGB", (3, 3)).save(external)
        (project.source / "shortcut.png").symlink_to(external)
        result = local_ai.sample(project, self.settings)
        self.assertFalse(result["ready"])
        self.assertFalse(any(route == "/api/generate" for route, body in self.requests))
        self.requests.clear()
        cancelled = threading.Event()
        cancelled.set()
        result = local_ai.sample(project, self.settings, cancelled)
        self.assertFalse(result["ready"])
        self.assertIn("cancelled", result["errors"][0].lower())
        self.assertEqual(self.requests, [])

    def test_running_sample_has_real_deadline(self):
        project = self.project()
        self.delay = 2
        started = time.monotonic()
        result = local_ai.sample(project, dict(self.settings, semantic_timeout=0.5))
        self.assertFalse(result["ready"])
        self.assertLess(time.monotonic() - started, 2)
        self.assertTrue(result["errors"])


if __name__ == "__main__":
    unittest.main()
