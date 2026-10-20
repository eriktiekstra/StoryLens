"""Real generated media exercise project isolation, recovery and storyboard state."""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from storylens.config import Config
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

    def test_preflight_discloses_missing_heif_decoder_and_saved_video_timeout(self):
        (self.source / "phone.heic").write_bytes(b"decoder not needed for preflight")
        (self.source / "large.mov").write_bytes(b"codec not needed for preflight")
        config = Config(self.config.database, ffmpeg="ffmpeg", ffprobe="ffprobe", timeout=180)
        with patch("storylens.workflow.importlib.util.find_spec", return_value=None):
            result = preflight(self.source, config)
        warnings = "\n".join(result["warnings"])
        self.assertIn("1 HEIC/HEIF files detected", warnings)
        self.assertIn("python -m pip install 'pillow-heif>=1,<2'", warnings)
        self.assertIn("180s", warnings)
        self.assertIn("Existing projects retain their saved timeout", warnings)

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
        self.write_manifest(manifest)
        resumed = Project.open(self.project.directory)
        self.assertEqual(resumed.state["analysis"]["status"], "interrupted")
        self.assertIn("resum", resumed.state["analysis"]["error"])
        self.assertEqual(self.manifest()["state"], resumed.state)

    def test_storyboard_edits_are_versioned_without_overwrites(self):
        document = self.analyzed()
        first = self.project.document_path
        first_bytes = first.read_bytes()
        edited = deepcopy(document)
        edited["title"] = "Reviewed order"
        edited["items"].reverse()
        second = self.project.save_storyboard(edited)
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), first_bytes)
        self.assertEqual(self.project.load_storyboard(), edited)
        self.assertEqual(Project.open(self.project.directory).load_storyboard(), edited)
        self.assertEqual(set(self.project.state), {"analysis", "storyboard"})

    def test_another_session_cannot_overwrite_current_storyboard(self):
        document = self.analyzed()
        stale = Project.open(self.project.directory)
        changed = deepcopy(document)
        changed["title"] = "The current review"
        self.project.save_storyboard(changed)
        current_path = self.project.document_path
        manifest_before = (self.project.directory / "project.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "another session"):
            stale.save_storyboard(document)
        self.assertEqual((self.project.directory / "project.json").read_bytes(), manifest_before)
        reopened = Project.open(self.project.directory)
        self.assertEqual(reopened.document_path, current_path)
        self.assertEqual(reopened.load_storyboard(), changed)

    def test_external_storyboard_edits_and_current_feedback_are_validated(self):
        document = self.analyzed()
        changed = deepcopy(document)
        changed["title"] = "Externally edited"
        self.project.document_path.write_text(json.dumps(changed))
        self.assertEqual(self.project.load_storyboard(), changed)
        media_id = changed["items"][0]["media_id"]
        with Analyzer(self.project.config) as app:
            app.set_feedback(media_id, {"capture_time": "2025-01-02T03:04:05Z"})
        self.assertEqual(self.project.media(media_id)["feedback"]["capture_time"], "2025-01-02T03:04:05Z")
        self.project.set_selection(media_id, "exclude")
        with self.assertRaises(ValueError):
            self.project.load_storyboard()
        regenerated = self.project.recommend(duration_seconds=8)
        self.assertNotIn(media_id, {item["media_id"] for item in regenerated["items"]})
        self.assertIn(media_id, regenerated["excluded"])

    def test_changed_source_reanalysis_refreshes_identity_and_foreign_catalog_fails_closed(self):
        self.analyzed()
        old_id = fingerprint(self.photo, snapshot(self.photo))
        Image.new("RGB", (48, 32), "green").save(self.photo)
        # UI reads authorize references without rereading the whole collection.
        self.assertEqual(len(self.project.list_media()), 2)
        self.project.analyze()
        current_ids = {item["id"] for item in self.project.list_media()}
        self.assertNotIn(old_id, current_ids)
        self.assertIn(fingerprint(self.photo, snapshot(self.photo)), current_ids)
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
        with patch("storylens.ingest.fingerprint", side_effect=AssertionError("UI read must not hash media")):
            self.assertEqual(self.project.load_storyboard(), document)
            indexed = self.project.list_media()
            with patch.object(Analyzer, "_storyboard_media", side_effect=AssertionError("per-item read must not group catalog")):
                for row in indexed:
                    item = self.project.media(row["id"])
                    self.assertEqual(item["id"], row["id"])
                    self.assertEqual(item["paths"], row["paths"])
                    self.assertIn("analysis", item)
                    self.assertIn("feedback", item)

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
        for field, value in (("schema_version", "3.0"), ("unexpected", "field"), ("source", str(self.project.directory))):
            changed = deepcopy(original)
            changed[field] = value
            variants.append(changed)
        for key, value in (("database", "/tmp/foreign.sqlite3"), ("trust_stat", 1),
                           ("embedding_model", "/tmp/model"),
                           ("ollama_endpoint", "http://user:secret@127.0.0.1/?token=x")):
            changed = deepcopy(original)
            changed["config"][key] = value
            variants.append(changed)
        for key, value in (("storyboard", "../secret.json"), ("unexpected", None),
                           ("analysis", {"status": [], "summary": None, "error": None}),
                           ("analysis", {"status": "completed", "summary": {}, "error": None})):
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
        link = self.project.directory / "storyboards" / "secret"
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Unsafe project path"):
            Project.open(self.project.directory)
        link.unlink()
        self.project.config.database.write_bytes(b"not a sqlite database")
        with self.assertRaisesRegex(ValueError, "catalog"):
            Project.open(self.project.directory)

    def test_legacy_editor_state_is_dropped_without_deleting_project_files(self):
        document = self.analyzed()
        manifest = self.manifest()
        manifest["schema_version"] = "1.0"
        old_files = {
            "outputs/fixture.fcproj": b"legacy native project",
            "outputs/preview.png": b"legacy preview",
            "outputs/export.mp4": b"legacy export",
            "reports/create.json": b'{"status":"succeeded"}',
        }
        for reference, content in old_files.items():
            path = self.project.directory / reference
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(content)
        digest = "a" * 64
        manifest["state"].update(
            approved_hash=digest, approved_evidence=digest,
            editor={"path": "outputs/fixture.fcproj", "storyboard_hash": digest},
            preview={"path": "outputs/preview.png", "project_hash": digest},
            export="outputs/export.mp4", reports=["reports/create.json"],
            operation={"kind": "create", "status": "running", "report": "reports/create.json", "error": None},
        )
        self.write_manifest(manifest)
        reopened = Project.open(self.project.directory)
        self.assertEqual(set(reopened.state), {"analysis", "storyboard"})
        self.assertEqual(reopened.load_storyboard(), document)
        self.assertEqual(reopened.state["analysis"]["status"], "completed")
        self.assertEqual(self.manifest()["state"], reopened.state)
        self.assertEqual(self.manifest()["schema_version"], "2.0")
        for reference, content in old_files.items():
            self.assertEqual((reopened.directory / reference).read_bytes(), content)
        self.assertEqual(Project.open(reopened.directory).state, reopened.state)

    def test_legacy_editor_references_are_ignored_not_followed(self):
        manifest = self.manifest()
        manifest["schema_version"] = "1.0"
        outside = self.root / "private.json"
        outside.write_bytes(b"unrelated private data")
        manifest["state"].update(
            approved_hash="a" * 64, approved_evidence="b" * 64,
            editor={"path": str(outside), "storyboard_hash": "a" * 64},
            preview={"path": "../private.json", "project_hash": "b" * 64},
            export=str(outside), reports=[str(outside)],
            operation={"kind": "export", "status": "running", "report": str(outside), "error": None},
        )
        self.write_manifest(manifest)
        reopened = Project.open(self.project.directory)
        self.assertEqual(set(reopened.state), {"analysis", "storyboard"})
        self.assertEqual(outside.read_bytes(), b"unrelated private data")
        self.assertEqual(self.manifest()["state"], reopened.state)

    def test_invalid_storyboard_does_not_replace_current_version(self):
        document = self.analyzed()
        current = self.project.document_path
        original = current.read_bytes()
        manifest = (self.project.directory / "project.json").read_bytes()
        invalid = deepcopy(document)
        invalid["items"][0]["media_id"] = "f" * 64
        with self.assertRaises(ValueError):
            self.project.save_storyboard(invalid)
        self.assertEqual(self.project.document_path, current)
        self.assertEqual(current.read_bytes(), original)
        self.assertEqual((self.project.directory / "project.json").read_bytes(), manifest)
        self.assertEqual(Project.open(self.project.directory).load_storyboard(), document)
