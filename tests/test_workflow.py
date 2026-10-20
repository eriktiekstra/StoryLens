"""Real generated media exercise project isolation, recovery and approval gates."""

import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from storylens.config import Config
from storylens.filmcraft import TICKS
from storylens.ingest import fingerprint, snapshot
from storylens.service import Analyzer
from storylens.workflow import MAX_JSON_BYTES, Project, preflight


class WorkflowTests(unittest.TestCase):
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
        self.config = Config(self.root / "ambient.sqlite3", ffmpeg="storylens-missing-ffmpeg",
                             ffprobe="storylens-missing-ffprobe")
        self.project = Project.create(self.root / "project", self.source, self.config)

    def manifest(self):
        return json.loads((self.project.directory / "project.json").read_text())

    def write_manifest(self, document):
        (self.project.directory / "project.json").write_text(json.dumps(document))

    def analyzed(self):
        self.project.analyze()
        return self.project.recommend(duration_seconds=8)

    def test_default_technical_mode_strips_ambient_inference_and_credentials(self):
        ambient = Config(self.root / "secret.sqlite3", embedding_model="/missing/model",
                         vision_model="ambient", audio_transcription_model="/missing/whisper",
                         ollama_endpoint="https://user:password@example.invalid/?token=secret")
        project = Project.create(self.root / "safe", self.source, ambient)
        self.assertEqual(project.mode, "technical")
        self.assertFalse(project.config.embedding_model)
        self.assertFalse(project.config.vision_model)
        self.assertFalse(project.config.audio_transcription_model)
        self.assertEqual(project.config.ollama_endpoint, "http://127.0.0.1:11434")
        manifest = (project.directory / "project.json").read_text()
        self.assertNotIn("password", manifest)
        self.assertNotIn("token=", manifest)
        self.assertFalse(ambient.database.exists())
        self.assertEqual(project.analyze()["failed"], 0)

    def test_preflight_counts_scope_and_requires_tools_only_for_audio_video(self):
        result = preflight(self.source, self.config)
        self.assertEqual(result["counts"], {"image": 2, "video": 0, "audio": 0})
        self.assertFalse(result["errors"])
        (self.source / "clip.mp4").write_bytes(b"recognized extension, not decoded in preflight")
        result = preflight(self.source, self.config)
        self.assertEqual(result["counts"]["video"], 1)
        self.assertTrue(any("ffmpeg" in error for error in result["errors"]))
        self.assertTrue(any("ffprobe" in error for error in result["errors"]))
        result = preflight(self.source, Config(self.config.database, exclusions=("*.mp4",)))
        self.assertEqual(result["counts"]["video"], 0)
        self.assertFalse(result["errors"])

    def test_preflight_rejects_empty_missing_traversal_and_oversized_collections(self):
        empty = self.root / "empty"
        empty.mkdir()
        for source in (empty, self.root / "missing", self.photo):
            with self.subTest(source=source):
                self.assertTrue(preflight(source, self.config)["errors"])
        with patch("storylens.workflow.discover", return_value=iter([PermissionError("unreadable directory")])):
            self.assertTrue(any("traversal" in error for error in preflight(self.source, self.config)["errors"]))
        with patch("storylens.workflow.discover", return_value=iter([self.photo] * 10001)):
            self.assertTrue(any("10000" in error for error in preflight(self.source, self.config)["errors"]))
        destination = self.root / "not-created" / "project"
        with self.assertRaises(ValueError):
            Project.create(destination, empty, self.config)
        self.assertFalse(destination.parent.exists())

    def test_explicit_local_ai_checks_installed_configuration_without_creating_output(self):
        result = preflight(self.source, self.config, mode="local-ai")
        self.assertTrue(any("embedding" in error for error in result["errors"]))
        with self.assertRaises(ValueError):
            Project.create(self.root / "ai", self.source, self.config, mode="local-ai")
        self.assertFalse((self.root / "ai").exists())
        model = self.root / "installed-model"
        model.mkdir()
        (model / "config.json").write_text('{"model_type":"clip"}')
        config = Config(self.config.database, embedding_model=str(model), vision_model="installed-local",
                        ollama_endpoint="https://user:token@127.0.0.1/?secret=1")
        self.assertTrue(preflight(self.source, config, mode="local-ai", depth="standard")["errors"])
        with self.assertRaises(ValueError):
            Project.create(self.root / "secret-ai", self.source, config, mode="local-ai", depth="standard")
        self.assertFalse((self.root / "secret-ai").exists())
        with patch("storylens.providers.OllamaVision", side_effect=AssertionError("fast mode has no vision provider")):
            fast = Project.create(self.root / "fast-ai", self.source, config, mode="local-ai")
            self.assertEqual(fast.config.vision_model, "")
            self.assertEqual(fast.config.ollama_endpoint, "http://127.0.0.1:11434")
            self.assertEqual(Project.open(fast.directory).depth, "fast")
        result = preflight(self.source, Config(self.config.database, embedding_model=str(model)),
                           mode="local-ai", depth="standard")
        self.assertTrue(any("Ollama model" in error for error in result["errors"]))

    def test_private_project_never_reuses_global_catalog_or_source_directory(self):
        private = self.root / "private"
        private.mkdir()
        secret = private / "secret.png"
        Image.new("RGB", (16, 16), "red").save(secret)
        with Analyzer(self.config) as app:
            app.scan(private)
        before = self.photo.read_bytes()
        self.project.analyze()
        self.assertEqual(len(self.project.list_media()), 2)
        self.assertEqual(self.project.config.database, self.project.directory / "catalog.sqlite3")
        self.assertNotIn(fingerprint(secret, snapshot(secret)), {item["id"] for item in self.project.list_media()})
        self.assertEqual(before, self.photo.read_bytes())
        if os.name == "posix":
            self.assertEqual(self.project.directory.stat().st_mode & 0o777, 0o700)
            self.assertEqual((self.project.directory / "project.json").stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            Project.create(self.source / "project", self.source, self.config)
        with self.assertRaises(ValueError):
            Project.create(self.project.directory, self.source, self.config)
        unrelated = self.root / "unrelated"
        unrelated.mkdir()
        (unrelated / "keep").write_bytes(b"keep")
        with self.assertRaises(ValueError):
            Project.create(unrelated, self.source, self.config)
        self.assertEqual((unrelated / "keep").read_bytes(), b"keep")

    def test_reopen_reuses_stage_caches_and_ignores_ambient_environment(self):
        first = self.project.analyze()
        self.assertEqual(first["analyzed"], 2)
        document = self.project.recommend(duration_seconds=8)
        path = self.project.document_path
        with patch.dict(os.environ, {"STORYLENS_DATABASE": str(self.root / "foreign.sqlite3"),
                                     "STORYLENS_EMBEDDING_MODEL": "/unexpected/model"}):
            resumed = Project.open(self.project.directory)
        self.assertEqual(resumed.document_path, path)
        self.assertEqual(resumed.load_storyboard(), document)
        self.assertFalse(resumed.config.embedding_model)
        second = resumed.analyze()
        self.assertEqual(second["cache_hits"], 2)
        self.assertEqual(second["analyzed"], 0)
        self.assertEqual(resumed.state["analysis"]["status"], "completed")
        self.assertEqual(resumed.document_path, path)
        state = resumed.state
        state["analysis"]["status"] = "forged"
        self.assertEqual(resumed.state["analysis"]["status"], "completed")

    def test_interruption_keeps_completed_stages_for_resume(self):
        def interrupt(summary):
            if summary["stages"].get("metadata", {}).get("analyzed", 0):
                raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            self.project.analyze(progress=interrupt)
        resumed = Project.open(self.project.directory)
        self.assertEqual(resumed.state["analysis"]["status"], "interrupted")
        self.assertGreaterEqual(resumed.state["analysis"]["summary"]["stages"]["metadata"]["analyzed"], 1)
        summary = resumed.analyze()
        self.assertGreaterEqual(summary["stages"]["metadata"]["cache_hits"], 1)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(resumed.state["analysis"]["status"], "completed")

    def test_partial_failures_remain_visible_across_reopen_and_repair(self):
        self.other.write_bytes(b"broken image")
        summary = self.project.analyze()
        self.assertEqual(summary["failed"], 1)
        self.assertTrue(summary["errors"])
        resumed = Project.open(self.project.directory)
        self.assertEqual(resumed.state["analysis"]["status"], "partial")
        self.assertEqual(resumed.state["analysis"]["summary"]["failed"], 1)
        Image.new("RGB", (48, 32), (120, 80, 20)).save(self.other)
        repaired = resumed.analyze()
        self.assertEqual(repaired["failed"], 0)
        self.assertGreaterEqual(repaired["cache_hits"], 1)

    def test_killed_running_state_is_recovered_honestly_on_open(self):
        manifest = self.manifest()
        manifest["state"]["analysis"]["status"] = "running"
        manifest["state"]["operation"] = {"kind": "create", "status": "running", "report": None, "error": None}
        self.write_manifest(manifest)
        resumed = Project.open(self.project.directory)
        self.assertEqual(resumed.state["analysis"]["status"], "interrupted")
        self.assertEqual(resumed.state["operation"]["status"], "interrupted")
        self.assertIn("retry", resumed.state["operation"]["error"])

    def test_storyboard_edits_are_versioned_and_invalidate_approval_without_overwrites(self):
        document = self.analyzed()
        first = self.project.document_path
        first_bytes = first.read_bytes()
        self.project.approve(document)
        edited = deepcopy(document)
        edited["title"] = "Reviewed order"
        edited["items"].reverse()
        second = self.project.save_storyboard(edited)
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), first_bytes)
        self.assertEqual(self.project.load_storyboard(), edited)
        self.assertIsNone(self.project.state["approved_hash"])
        self.assertIsNone(self.project.state["editor"])
        with self.assertRaisesRegex(ValueError, "changed since review"):
            self.project.approve(document)
        self.project.approve(edited)
        self.assertTrue(self.project.state["approved_hash"])
        self.assertEqual(Project.open(self.project.directory).load_storyboard(), edited)

    def test_another_session_cannot_approve_old_version_or_overwrite_new_review(self):
        document = self.analyzed()
        stale = Project.open(self.project.directory)
        changed = deepcopy(document)
        changed["title"] = "The current review"
        self.project.save_storyboard(changed)
        current_path = self.project.document_path
        manifest_before = (self.project.directory / "project.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "another session"):
            stale.approve(document)
        self.assertEqual((self.project.directory / "project.json").read_bytes(), manifest_before)
        self.assertEqual(Project.open(self.project.directory).document_path, current_path)

    def test_changed_source_blocks_preview_and_export_before_external_launch(self):
        self.native_fixture()
        with patch("storylens.filmcraft_transport.FilmCraft", FixtureFilmCraft):
            asyncio.run(self.project.preview_editor("fixture-editor", approved=True))
        Image.new("RGB", (48, 32), "purple").save(self.photo)
        with patch("storylens.filmcraft_transport.FilmCraft", side_effect=AssertionError("must not launch")):
            for operation in (self.project.preview_editor, self.project.export_editor):
                with self.subTest(operation=operation), self.assertRaisesRegex(ValueError, "content changed"):
                    asyncio.run(operation("untrusted-editor", approved=True))

    def test_external_storyboard_edit_and_current_feedback_cannot_reuse_approval(self):
        document = self.analyzed()
        self.project.approve(document)
        changed = deepcopy(document)
        changed["title"] = "Externally edited"
        self.project.document_path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "changed since review"):
            self.project.approve(document)
        with self.assertRaisesRegex(ValueError, "reviewed and approved"):
            asyncio.run(self.project.create_editor("must-not-launch", approved=True))
        self.project.approve(changed)
        media_id = changed["items"][0]["media_id"]
        with Analyzer(self.project.config) as app:
            app.set_feedback(media_id, {"capture_time": "2025-01-02T03:04:05Z"})
        with self.assertRaisesRegex(ValueError, "evidence or feedback changed"):
            asyncio.run(self.project.create_editor("must-not-launch", approved=True))
        self.project.set_selection(media_id, "exclude")
        self.assertIsNone(self.project.state["approved_hash"])
        with self.assertRaises(ValueError):
            self.project.load_storyboard()
        regenerated = self.project.recommend(duration_seconds=8)
        self.assertNotIn(media_id, {item["media_id"] for item in regenerated["items"]})
        self.assertIn(media_id, regenerated["excluded"])

    def test_changed_source_requires_reanalysis_and_foreign_live_catalog_fails_closed(self):
        document = self.analyzed()
        self.project.approve(document)
        Image.new("RGB", (48, 32), "green").save(self.photo)
        # UI reads authorize references without rereading the whole collection.
        self.assertEqual(len(self.project.list_media()), 2)
        with self.assertRaisesRegex(ValueError, "content changed"):
            self.project.approve(document)
        with self.assertRaisesRegex(ValueError, "content changed"):
            asyncio.run(self.project.create_editor("must-not-launch", approved=True))
        self.project.analyze()
        self.assertIsNone(self.project.state["approved_hash"])
        private = self.root / "private"
        private.mkdir()
        Image.new("RGB", (20, 20), "red").save(private / "secret.png")
        with Analyzer(self.project.config) as app:
            app.scan(private)
        for action in (self.project.list_media, self.project.recommend, self.project.analyze):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, "outside.*source"):
                action()

    def test_ui_reads_do_not_hash_sources_or_group_all_media_per_entry(self):
        document = self.analyzed()
        with patch("storylens.workflow.fingerprint", side_effect=AssertionError("UI read must not hash media")):
            self.assertEqual(self.project.load_storyboard(), document)
            indexed = self.project.list_media()
            with patch.object(Analyzer, "_storyboard_media", side_effect=AssertionError("per-item read must not group catalog")):
                for row in indexed:
                    item = self.project.media(row["id"])
                    self.assertEqual(item["id"], row["id"])
                    self.assertEqual(item["paths"], row["paths"])
                    self.assertIn("analysis", item)
                    self.assertIn("feedback", item)

    def test_approval_and_editor_gates_hash_each_source_only_once(self):
        document = self.analyzed()
        with patch("storylens.workflow.fingerprint", wraps=fingerprint) as hashing:
            self.project.approve(document)
            self.assertEqual(hashing.call_count, 2)
        with patch("storylens.workflow.fingerprint", wraps=fingerprint) as hashing:
            with self.assertRaisesRegex(ValueError, "Create an editor project"):
                asyncio.run(self.project.preview_editor("must-not-launch", approved=True))
            self.assertEqual(hashing.call_count, 2)

    def test_selection_applies_to_omitted_current_media_and_requires_regeneration(self):
        self.project.analyze()
        document = self.project.recommend(duration_seconds=4)
        omitted = next(item["id"] for item in self.project.list_media()
                       if item["id"] not in {entry["media_id"] for entry in document["items"]})
        self.project.set_selection(omitted, "include")
        self.assertEqual(self.project.media(omitted)["feedback"]["selection"], "include")
        with self.assertRaises(ValueError):
            self.project.load_storyboard()
        document = self.project.recommend(duration_seconds=8)
        self.assertIn(omitted, document["required"])
        self.assertTrue(next(item for item in document["items"] if item["media_id"] == omitted)["required"])
        self.project.set_selection(omitted, "neutral")
        self.assertEqual(self.project.media(omitted)["feedback"]["selection"], "neutral")
        document = self.project.recommend(duration_seconds=8)
        self.assertNotIn(omitted, document["required"])

    def test_invalid_manifests_are_rejected_without_following_external_references(self):
        original = self.manifest()
        variants = []
        for field, value in (("schema_version", "2.0"), ("unexpected", "field"), ("source", str(self.project.directory))):
            changed = deepcopy(original)
            changed[field] = value
            variants.append(changed)
        for key, value in (("database", "/tmp/foreign.sqlite3"), ("trust_stat", 1),
                           ("embedding_model", "/tmp/model"),
                           ("ollama_endpoint", "http://user:secret@127.0.0.1/?token=x")):
            changed = deepcopy(original)
            changed["config"][key] = value
            variants.append(changed)
        for key, value in (("storyboard", "../secret.json"), ("approved_hash", "f" * 64),
                           ("analysis", {"status": [], "summary": None, "error": None}),
                           ("analysis", {"status": "completed", "summary": {}, "error": None}),
                           ("reports", ["/tmp/secret.json"])):
            changed = deepcopy(original)
            changed["state"][key] = value
            variants.append(changed)
        for changed in variants:
            self.write_manifest(changed)
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                Project.open(self.project.directory)
        self.write_manifest(original)
        self.assertEqual(Project.open(self.project.directory).source, self.source)

    def test_bounded_duplicate_nonfinite_and_nonobject_json_are_rejected(self):
        path = self.project.directory / "project.json"
        original = path.read_bytes()
        for raw in (b"[]", b'{"kind":1,"kind":2}', b'{"x":NaN}', b" " * (MAX_JSON_BYTES + 1), b"{" * 2000):
            path.write_bytes(raw)
            with self.subTest(length=len(raw)), self.assertRaises(ValueError):
                Project.open(self.project.directory)
        path.write_bytes(original)

    def test_symlinked_state_and_unrelated_catalog_are_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.project.directory / "outputs" / "secret"
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Unsafe project path"):
            Project.open(self.project.directory)
        link.unlink()
        self.project.config.database.write_bytes(b"not a sqlite database")
        with self.assertRaisesRegex(ValueError, "catalog"):
            Project.open(self.project.directory)

    def native_fixture(self):
        document = self.analyzed()
        self.project.approve(document)
        native = self.project.directory / "outputs" / "fixture.fcproj"
        native.write_text(json.dumps({"format": "filmcraft.project", "schema_version": 15,
                                     "project": {"items": {"1": {"kind": {"Media": {
                                         "media": {"File": {"path": str(self.photo)}}}}}}}}))
        manifest = self.manifest()
        manifest["state"]["editor"] = {"path": "outputs/fixture.fcproj",
                                         "storyboard_hash": self.project.state["approved_hash"]}
        self.write_manifest(manifest)
        self.project = Project.open(self.project.directory)
        return native

    def test_each_editor_operation_requires_explicit_consent_before_transport(self):
        self.native_fixture()
        with patch("storylens.filmcraft_transport.FilmCraft", side_effect=AssertionError("must not launch")):
            for method in (self.project.create_editor, self.project.preview_editor, self.project.export_editor):
                with self.subTest(method=method), self.assertRaisesRegex(ValueError, "separate explicit approval"):
                    asyncio.run(method("untrusted-binary"))
            with self.assertRaisesRegex(ValueError, "current successful preview"):
                asyncio.run(self.project.export_editor("untrusted-binary", approved=True))
        self.assertFalse(self.project.state["reports"])

    def test_preview_export_reports_and_native_changes_require_fresh_preview(self):
        native = self.native_fixture()
        original = native.read_bytes()
        with patch("storylens.filmcraft_transport.FilmCraft", FixtureFilmCraft):
            preview = asyncio.run(self.project.preview_editor("fixture-editor", approved=True))
            self.assertEqual(preview["status"], "succeeded")
            preview_path = Path(preview["output"])
            with Image.open(preview_path) as image:
                self.assertEqual(image.size, (16, 16))
            self.assertEqual(self.project.state["preview"]["project_hash"], hashlib.sha256(original).hexdigest())
            exported = asyncio.run(self.project.export_editor("fixture-editor", approved=True))
            self.assertEqual(exported["status"], "succeeded")
            export_path = Path(exported["output"])
            export_bytes = export_path.read_bytes()
            native.write_bytes(original + b"\n")
            with self.assertRaisesRegex(ValueError, "current successful preview"):
                asyncio.run(self.project.export_editor("fixture-editor", approved=True))
            newer = asyncio.run(self.project.preview_editor("fixture-editor", approved=True))
            self.assertNotEqual(newer["output"], preview["output"])
            self.assertTrue(preview_path.is_file())
            self.assertEqual(export_path.read_bytes(), export_bytes)
            exported_again = asyncio.run(self.project.export_editor("fixture-editor", approved=True))
            self.assertNotEqual(exported_again["output"], exported["output"])
        reopened = Project.open(self.project.directory)
        self.assertEqual(len(reopened.state["reports"]), 4)
        for reference in reopened.state["reports"]:
            self.assertFalse(Path(reference).is_absolute())
            report = json.loads((reopened.directory / reference).read_text())
            self.assertTrue(report["operations"])
            self.assertEqual(Path(report["report_path"]), reopened.directory / reference)

    def test_failed_render_keeps_full_report_and_existing_output(self):
        self.native_fixture()
        preserved = self.project.directory / "outputs" / "keep.png"
        Image.new("RGB", (10, 10), "blue").save(preserved)
        before = preserved.read_bytes()
        with patch("storylens.filmcraft_transport.FilmCraft", OfflineFilmCraft):
            report = asyncio.run(self.project.preview_editor("fixture-editor", approved=True))
        self.assertEqual(report["status"], "failed")
        self.assertIn("offline", report["error"])
        self.assertEqual(report["operations"][0]["id"], "media.status")
        self.assertIsNone(self.project.state["preview"])
        self.assertEqual(preserved.read_bytes(), before)
        self.assertEqual(Project.open(self.project.directory).state["operation"]["status"], "failed")

    def test_aggregate_tool_report_preserves_failed_state_beyond_input_document_limit(self):
        self.analyzed()
        storyboard = self.project.document_path
        before = storyboard.read_bytes()
        output = self.project.directory / "outputs" / "never-created.fcproj"

        async def failed_operation():
            # Each result is below the editor transport limit, while their
            # aggregate report legitimately exceeds the input-document limit.
            return {"schema_version": "1.0", "status": "failed", "error": "second command failed",
                    "operations": [{"result": {"diagnostic": "a" * 1200000}},
                                   {"result": {"diagnostic": "b" * 1200000}}]}

        def unexpected_success():
            self.fail("A failed native operation must not publish success state")

        report = asyncio.run(self.project._operation("create", output, failed_operation, unexpected_success))
        reopened = Project.open(self.project.directory)
        operation = reopened.state["operation"]
        self.assertEqual(operation["status"], "failed")
        self.assertEqual(operation["error"], "second command failed")
        self.assertGreater((reopened.directory / operation["report"]).stat().st_size, MAX_JSON_BYTES)
        self.assertEqual(storyboard.read_bytes(), before)
        self.assertFalse(output.exists())

    def test_successful_editor_creation_is_versioned_and_review_changes_detach_old_native(self):
        document = self.analyzed()
        self.project.approve(document)
        with patch("storylens.filmcraft_transport.FilmCraft", AssemblingFilmCraft):
            first = asyncio.run(self.project.create_editor("fixture-editor", approved=True))
            second = asyncio.run(self.project.create_editor("fixture-editor", approved=True))
        self.assertEqual(first["status"], "succeeded")
        self.assertEqual(second["status"], "succeeded")
        first_path, second_path = Path(first["project"]), Path(second["project"])
        self.assertNotEqual(first_path, second_path)
        self.assertTrue(first_path.is_file())
        self.assertTrue(second_path.is_file())
        self.assertEqual(self.project.directory / self.project.state["editor"]["path"], second_path)
        saved = json.loads(second_path.read_text())
        self.assertEqual(len(saved["project"]["items"]), 2)
        for item in saved["project"]["items"].values():
            self.assertTrue(Path(item["kind"]["Media"]["media"]["File"]["path"]).is_relative_to(self.source))
        preserved = second_path.read_bytes()
        document["title"] = "A new review"
        self.project.save_storyboard(document)
        self.assertIsNone(self.project.state["editor"])
        self.assertEqual(second_path.read_bytes(), preserved)
        self.assertEqual(len(Project.open(self.project.directory).state["reports"]), 2)

    def test_missing_editor_records_failure_without_retry_or_losing_storyboard(self):
        document = self.analyzed()
        self.project.approve(document)
        storyboard = self.project.document_path.read_bytes()
        with self.assertRaises((OSError, RuntimeError, ValueError)):
            asyncio.run(self.project.create_editor(str(self.root / "missing-editor"), approved=True))
        reopened = Project.open(self.project.directory)
        self.assertEqual(reopened.state["operation"]["status"], "failed")
        self.assertEqual(len(reopened.state["reports"]), 1)
        self.assertIsNone(reopened.state["editor"])
        self.assertEqual(reopened.document_path.read_bytes(), storyboard)


class FixtureFilmCraft:
    """External transport boundary: real adapter still validates and publishes files."""

    def __init__(self, executable, *, project=None, timeout=30):
        self.project = project

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def discover(self):
        return {"server": {"name": "filmcraft", "version": "0.9.0"},
                "tools": [{"name": name} for name in
                          ("command_run", "media_import", "project_inspect", "sequence_inspect")],
                "command_list": [{"id": name} for name in
                                 ("file.exportFrame", "file.exportMedia", "media.status")]}

    async def call(self, tool, arguments, *, allow_null=False):
        if tool == "sequence_inspect":
            return {"duration": 8 * TICKS}
        command = arguments["id"]
        if command == "media.status":
            return [{"item": "1", "status": "online"}]
        path = Path(arguments["params"]["path"])
        if command == "file.exportFrame":
            Image.new("RGB", (16, 16), "green").save(path)
        elif command == "file.exportMedia":
            path.write_bytes(b"external-render-boundary-output")
        else:
            raise AssertionError("Unexpected editor command: " + command)
        return {}


class OfflineFilmCraft(FixtureFilmCraft):
    async def call(self, tool, arguments, *, allow_null=False):
        if tool == "command_run" and arguments["id"] == "media.status":
            return [{"item": "1", "status": "offline"}]
        return await super().call(tool, arguments)


class AssemblingFilmCraft(FixtureFilmCraft):
    saved = {}

    def __init__(self, executable, *, project=None, timeout=30):
        super().__init__(executable, project=project, timeout=timeout)
        self.imported = {}
        self.sequence = deepcopy(self.saved.get(str(project), {
            "settings": {"frame_rate": {"num": 24, "den": 1}}, "id": 7,
            "video": [{"items": []}], "audio": [], "duration": 0}))

    async def discover(self):
        result = await super().discover()
        result["command_list"].extend({"id": name} for name in
                                      ("file.newSequence", "timeline.place", "timeline.select",
                                       "clip.scaleToFrameSize", "file.saveAs"))
        return result

    async def call(self, tool, arguments, *, allow_null=False):
        if tool == "sequence_inspect":
            return deepcopy(self.sequence)
        if tool == "media_import":
            key = str(len(self.imported) + 1)
            self.imported[key] = arguments["paths"][0]
            return {"items": [key]}
        if tool == "project_inspect":
            return {"root": {"children": [{"item": key, "duration": 0} for key in self.imported]}}
        command, params = arguments["id"], arguments["params"]
        if command == "file.newSequence":
            return {"sequence": 7}
        if command == "timeline.place":
            clips = self.sequence["video"][0]["items"]
            key = "clip-" + str(len(clips) + 1)
            clips.append({"clip": key, "item": params["item"], "start": params["time"],
                          "end": params["time"] + params["duration"], "sourceIn": params["sourceIn"],
                          "sourceOut": params["sourceIn"] + params["duration"],
                          "speed": 1, "reverse": False, "enabled": True})
            self.sequence["duration"] = max(self.sequence["duration"], clips[-1]["end"])
            return {"clips": [key]}
        if command in {"timeline.select", "clip.scaleToFrameSize"}:
            return None if command == "clip.scaleToFrameSize" else {}
        if command == "file.saveAs":
            path = Path(params["path"])
            path.write_text(json.dumps({"format": "filmcraft.project", "schema_version": 15,
                                        "project": {"items": {
                                            key: {"kind": {"Media": {"media": {"File": {"path": source}}}}}
                                            for key, source in self.imported.items()}}}))
            self.saved[str(path)] = deepcopy(self.sequence)
            return {}
        raise AssertionError("Unexpected editor operation: " + tool + " " + command)
