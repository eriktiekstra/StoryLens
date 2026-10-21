"""Exercise the loopback browser API against real private image analysis."""

from html.parser import HTMLParser
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image

from storylens.config import Config
from storylens.web_wizard import _handler
from storylens.wizard_session import WizardSession
from storylens.workflow import Project


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "id" in attributes:
            self.elements[attributes["id"]] = (tag, attributes)


class WebWizardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "media"
        self.source.mkdir()
        self.photo = self.source / "first.png"
        self.other = self.source / "second.png"
        Image.new("RGB", (48, 32), (20, 40, 100)).save(self.photo)
        Image.new("RGB", (48, 32), (120, 80, 20)).save(self.other)
        self.directory = self.root / "private-project"
        self.config = Config(self.root / "ambient.sqlite3", ffmpeg="storylens-missing-ffmpeg",
                             ffprobe="storylens-missing-ffprobe")
        self.state = WizardSession(self.config)
        self.token = "private-browser-session-token"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(self.state, self.token))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.host = f"127.0.0.1:{self.server.server_port}"
        self.origin = "http://" + self.host

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method, route, body=None, *, headers=None):
        request_headers = {"Host": self.host, "Authorization": "Bearer " + self.token}
        if method == "POST":
            request_headers.update({"Origin": self.origin, "Content-Type": "application/json"})
        for key, value in (headers or {}).items():
            if value is None:
                request_headers.pop(key, None)
            else:
                request_headers[key] = value
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            raw = json.dumps(body).encode() if body is not None else None
            connection.request(method, route, body=raw, headers=request_headers)
            response = connection.getresponse()
            payload = response.read().decode("utf-8")
            result = json.loads(payload) if response.getheader("Content-Type", "").startswith("application/json") else payload
            return response.status, dict(response.getheaders()), result
        finally:
            connection.close()

    def start(self, **changes):
        body = {"source": str(self.source), "project": str(self.directory)}
        body.update(changes)
        status, headers, response = self.request("POST", "/api/start", body)
        self.assertEqual(status, 200, response)
        return response

    def analyze(self):
        status, _, response = self.request("POST", "/api/analyze", {})
        self.assertEqual(status, 202, response)
        self.assertIn("status", response)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            status, _, response = self.request("GET", "/api/state")
            self.assertEqual(status, 200, response)
            if not response["busy"]:
                return response
            time.sleep(0.01)
        self.fail("Local image analysis did not finish")

    def test_page_has_private_response_and_explicit_inspection_consent(self):
        status, headers, response = self.request("GET", "/", headers={"Authorization": None})
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertNotIn(self.token, response)
        page = Page()
        page.feed(response)
        tag, consent = page.elements["inspect"]
        self.assertEqual(tag, "input")
        self.assertEqual(consent["type"], "checkbox")
        self.assertNotIn("checked", consent)
        self.assertFalse(self.directory.exists())

    def test_state_requires_exact_bearer_token_and_loopback_host(self):
        for headers in ({"Authorization": None}, {"Authorization": "Bearer wrong"},
                        {"Authorization": self.token}, {"Host": "attacker.invalid"},
                        {"Host": f"localhost:{self.server.server_port}"}):
            with self.subTest(headers=headers):
                status, _, response = self.request("GET", "/api/state", headers=headers)
                self.assertEqual(status, 404)
                self.assertIn("error", response)
        status, headers, response = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(response["status"], "idle")
        self.assertFalse(response["busy"])
        self.assertIsNone(response["project"])

    def test_page_rejects_foreign_host_and_unknown_routes(self):
        status, _, _ = self.request("GET", "/", headers={"Host": "attacker.invalid"})
        self.assertEqual(status, 403)
        for method, route, body in (("GET", "/project.json", None),
                                    ("GET", "/api/unknown", None),
                                    ("POST", "/api/unknown", {})):
            with self.subTest(method=method, route=route):
                status, _, _ = self.request(method, route, body)
                self.assertEqual(status, 404)

    def test_mutations_require_matching_origin_host_and_session(self):
        rejected = ({"Origin": None}, {"Origin": "null"}, {"Origin": "https://attacker.invalid"},
                    {"Origin": f"http://localhost:{self.server.server_port}"},
                    {"Host": "attacker.invalid"}, {"Authorization": None},
                    {"Authorization": "Bearer wrong"})
        for route, body in (("/api/start", {"source": str(self.source), "project": str(self.directory)}),
                            ("/api/analyze", {}), ("/api/state", {}), ("/api/cancel", {}),
                            ("/api/configure", {"settings": {}}), ("/api/ai/check", {}),
                            ("/api/ai/sample", {}), ("/api/ai/install", {"consent": True}),
                            ("/api/ai/profile", {"action": "save"}), ("/api/diagnostics", {}),
                            ("/api/open-folder", {}), ("/api/readiness", {"source": str(self.source)})):
            for headers in rejected:
                with self.subTest(route=route, headers=headers):
                    status, _, response = self.request("POST", route, body, headers=headers)
                    self.assertEqual(status, 404)
                    self.assertIn("error", response)
        self.assertFalse(self.directory.exists())
        self.assertEqual(self.state.snapshot()["status"], "idle")

    def test_analysis_requires_project_and_start_does_not_analyze(self):
        status, _, response = self.request("POST", "/api/analyze", {})
        self.assertEqual(status, 400)
        self.assertIn("project", response["error"].lower())
        started = self.start()
        self.assertFalse(started["existing"])
        self.assertEqual(started["counts"], {"image": 2, "video": 0, "audio": 0})
        status, _, response = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(response["status"], "ready")
        self.assertEqual(response["summary"], {})
        project = Project.open(self.directory)
        self.assertEqual(project.state["analysis"]["status"], "not_started")
        self.assertEqual(project.list_media(), [])

    def test_project_response_supplies_isolated_mcp_handoff_paths(self):
        before = {path.name: path.read_bytes() for path in self.source.iterdir()}
        started = self.start()
        details = started["project"]
        self.assertEqual(Path(details["directory"]), self.directory)
        self.assertEqual(Path(details["source"]), self.source)
        self.assertEqual(Path(details["database"]), self.directory / "catalog.sqlite3")
        self.assertTrue(details["executable"])
        project = Project.open(details["directory"])
        self.assertEqual(project.source, Path(details["source"]))
        self.assertEqual(project.config.database, Path(details["database"]))
        self.assertFalse(self.config.database.exists())
        self.assertEqual({path.name: path.read_bytes() for path in self.source.iterdir()}, before)
        self.assertFalse(self.directory.is_relative_to(self.source))
        self.assertEqual(set(project.state), {"analysis", "technical", "ai", "storyboard"})

    def test_project_must_be_separate_and_failed_start_creates_nothing(self):
        nested = self.source / "project"
        status, _, response = self.request("POST", "/api/start", {
            "source": str(self.source), "project": str(nested)})
        self.assertEqual(status, 400, response)
        self.assertFalse(nested.exists())
        self.assertIsNone(self.state.project)
        self.assertEqual(self.state.snapshot()["status"], "idle")

    def test_real_image_analysis_completes_and_resume_reuses_cache(self):
        self.start()
        result = self.analyze()
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["summary"]["discovered"], 2)
        self.assertEqual(result["summary"]["analyzed"], 2)
        self.assertEqual(result["summary"]["failed"], 0)
        self.assertEqual(result["errors"], [])
        self.assertIsNone(result["error"])
        project = Project.open(self.directory)
        self.assertEqual(project.state["analysis"]["summary"], result["summary"])
        self.assertEqual(len(project.list_media()), 2)
        for item in project.list_media():
            self.assertIn("image_technical", item["analysis"])
        resumed = self.start(source="")
        self.assertTrue(resumed["existing"])
        self.assertEqual(resumed["project"]["database"], str(project.config.database))
        self.assertEqual(resumed["technical"]["status"], "completed")
        self.assertEqual(resumed["technical"]["summary"], result["summary"])
        self.assertEqual(resumed["summary"], result["summary"])
        again = self.analyze()
        self.assertEqual(again["status"], "completed", again)
        self.assertEqual(again["summary"]["cache_hits"], 2)
        self.assertEqual(again["summary"]["analyzed"], 0)

    def test_partial_analysis_exposes_failures_and_repair_can_resume(self):
        self.other.write_bytes(b"not a readable PNG")
        self.start()
        result = self.analyze()
        self.assertEqual(result["status"], "partial", result)
        self.assertEqual(result["summary"]["failed"], 1)
        self.assertEqual(result["errors"], result["summary"]["errors"])
        self.assertTrue(result["errors"])
        self.assertTrue(any(error["path"] == str(self.other) for error in result["errors"]))
        self.assertEqual(Project.open(self.directory).state["analysis"]["status"], "partial")
        Image.new("RGB", (48, 32), (120, 80, 20)).save(self.other)
        repaired = self.analyze()
        self.assertEqual(repaired["status"], "completed", repaired)
        self.assertEqual(repaired["summary"]["failed"], 0)
        self.assertGreaterEqual(repaired["summary"]["cache_hits"], 1)

    def test_resume_rejects_different_source_without_replacing_saved_project(self):
        self.start()
        before = (self.directory / "project.json").read_bytes()
        other = self.root / "different-media"
        other.mkdir()
        Image.new("RGB", (16, 16), "red").save(other / "secret.png")
        status, _, response = self.request("POST", "/api/start", {
            "source": str(other), "project": str(self.directory)})
        self.assertEqual(status, 400)
        self.assertIn("Source differs", response["error"])
        self.assertEqual((self.directory / "project.json").read_bytes(), before)
        self.assertEqual(Project.open(self.directory).source, self.source)

    def test_private_directory_browser_lists_local_folders_without_upload(self):
        unusual = self.root / '<img src=x onerror=\"alert(1)\">'
        unusual.mkdir()
        (self.root / "not-a-folder.txt").write_text("private", encoding="utf-8")
        status, headers, value = self.request("GET", "/api/browse?path=" + str(self.root))
        self.assertEqual(status, 200, value)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(value["path"], str(self.root))
        self.assertIn({"name": unusual.name, "path": str(unusual)}, value["directories"])
        self.assertNotIn("not-a-folder.txt", [entry["name"] for entry in value["directories"]])
        self.assertFalse(self.directory.exists())
        for route in ("/api/browse", "/api/ai/options", "/api/handoff"):
            status, _, _ = self.request("GET", route, headers={"Authorization": None})
            self.assertEqual(status, 404)

    def test_zero_media_readiness_explains_failure_without_creating_project(self):
        self.photo.unlink()
        self.other.unlink()
        status, _, readiness = self.request("POST", "/api/readiness", {"source": str(self.source)})
        self.assertEqual(status, 200, readiness)
        self.assertEqual(sum(readiness["counts"].values()), 0)
        self.assertTrue(readiness["errors"])
        self.assertTrue(readiness["installation_instructions"])
        status, _, result = self.request("POST", "/api/start", {
            "source": str(self.source), "project": str(self.directory)})
        self.assertEqual(status, 400, result)
        self.assertIn("media", result["error"].lower())
        self.assertFalse(self.directory.exists())
        self.assertEqual(self.state.snapshot()["status"], "idle")

    def test_timeout_edits_preserve_disabled_ai_and_existing_basic_results(self):
        self.start()
        self.analyze()
        status, _, result = self.request("POST", "/api/configure", {
            "settings": {"vision_model": "user-selected-local-model",
                         "ollama_endpoint": "http://127.0.0.1:11435"}})
        self.assertEqual(status, 200, result)
        status, _, result = self.request("POST", "/api/configure", {"settings": {"mode": "technical"}})
        self.assertEqual(status, 200, result)
        basic = result["technical"]
        status, _, result = self.request("POST", "/api/configure", {
            "settings": {"timeout": 900, "semantic_timeout": 240}})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["project"]["mode"], "technical")
        self.assertEqual(result["technical"], basic)
        reopened = Project.open(self.directory)
        self.assertEqual(reopened.mode, "technical")
        self.assertEqual(reopened.config.timeout, 900)
        self.assertEqual(reopened.config.ollama_endpoint, "http://127.0.0.1:11435")
        self.assertEqual(reopened.config.vision_model, "user-selected-local-model")

    def test_diagnostics_export_keeps_saved_failures_local_and_does_not_start_work(self):
        self.other.write_bytes(b"broken image")
        self.start()
        failed = self.analyze()
        status, _, result = self.request("POST", "/api/diagnostics", {})
        self.assertEqual(status, 200, result)
        report_path = Path(result["path"])
        self.assertEqual(report_path.parent, self.directory)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["technical"]["summary"]["errors"], failed["technical"]["summary"]["errors"])
        self.assertEqual(Project.open(self.directory).analysis_status("technical"), failed["technical"])


    def test_invalid_payloads_fail_before_mutating_shared_session(self):
        for route, body in (
                ("/api/start", {"project": 3}),
                ("/api/configure", {"settings": []}),
                ("/api/ai/check", {"settings": "not settings"}),
                ("/api/ai/sample", {"settings": None}),
                ("/api/ai/install", {"consent": "yes"}),
                ("/api/ai/install", {"consent": True, "settings": []}),
                ("/api/readiness", {"source": 3}),
                ("/api/ai/profile", {"settings": []}),
                ("/api/start", ["not", "an object"])):
            with self.subTest(route=route, body=body):
                status, _, result = self.request("POST", route, body)
                self.assertEqual(status, 400, result)
                self.assertTrue(result["error"])
        self.assertFalse(self.directory.exists())

    def test_model_download_requires_explicit_consent_without_contacting_host(self):
        self.start()
        with patch("storylens.local_ai.OllamaVision._request") as download:
            for body in ({"capability": "vision"}, {"capability": "vision", "consent": False}):
                status, _, result = self.request("POST", "/api/ai/install", body)
                self.assertEqual(status, 400, result)
                self.assertIn("consent", result["error"].lower())
            download.assert_not_called()

    def test_ai_detection_is_read_only_and_does_not_implicitly_reuse_profile(self):
        self.start()
        from storylens import local_ai
        with patch.object(local_ai, "_root", return_value=self.root / "user-settings"):
            local_ai.save_profile({"vision_model": "saved-model"})
            status, _, result = self.request("GET", "/api/ai/options")
            self.assertEqual(status, 200, result)
            self.assertEqual(result["settings"]["vision_model"], "saved-model")
        saved = self.state.snapshot()
        self.assertEqual(saved["project"]["mode"], "technical")
        self.assertFalse(saved["project"]["settings"]["vision_model"])
        self.assertEqual(saved["technical"]["status"], "not_started")


