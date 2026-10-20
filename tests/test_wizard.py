"""User-facing wizard safety, recovery and persisted review transitions."""

from copy import deepcopy
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from storylens.config import Config
from storylens import wizard
from storylens.workflow import Project


class Terminal(io.StringIO):
    def isatty(self):
        return True


class WizardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir()
        Image.new("RGB", (12, 12), (20, 30, 40)).save(self.source / "one.png")
        Image.new("RGB", (12, 12), (90, 70, 40)).save(self.source / "two.png")
        self.config = Config(self.root / "unused.sqlite3")
        self.directory = self.root / "private-project"

    def run_wizard(self, answers, **options):
        output = Terminal()
        with patch("sys.stdin", Terminal(answers)), patch("sys.stdout", output):
            code = wizard.run(self.config, **options)
        return code, output.getvalue()

    def project(self, *, duration=8):
        project = Project.create(self.directory, self.source, self.config)
        project.analyze()
        document = project.recommend(duration_seconds=duration)
        return project, document

    def test_non_tty_rejects_before_prompting_or_creating(self):
        with patch("sys.stdin", io.StringIO()), patch("sys.stdout", Terminal()), patch("builtins.input") as prompt:
            with self.assertRaisesRegex(ValueError, "interactive.*TTY"):
                wizard.run(self.config, project=self.directory, source=self.source)
        prompt.assert_not_called()
        self.assertFalse(self.directory.exists())
        with patch("sys.stdin", Terminal()), patch("sys.stdout", io.StringIO()):
            with self.assertRaisesRegex(ValueError, "interactive.*TTY"):
                wizard.run(self.config)

    def test_eof_after_creation_retains_unanalyzed_project_and_resume_path(self):
        code, output = self.run_wizard("\n", project=self.directory, source=self.source)
        self.assertEqual(code, 130)
        reopened = Project.open(self.directory)
        self.assertEqual(reopened.state["analysis"]["status"], "not_started")
        self.assertIsNone(reopened.document_path)
        self.assertIn("Stopped safely", output)
        self.assertIn("Resume project: " + str(self.directory), output)

    def test_default_technical_analysis_does_not_use_ambient_models(self):
        self.config = Config(self.root / "unused.sqlite3", embedding_model="/missing/embedding",
                             vision_model="ambient-model", audio_transcription_model="/missing/whisper")
        with patch("storylens.service.Analyzer._semantic_provider", side_effect=AssertionError("hidden inference")):
            code, output = self.run_wizard("\ny\n8\n\nq\n", project=self.directory, source=self.source)
        self.assertEqual(code, 0, output)
        project = Project.open(self.directory)
        self.assertEqual(project.mode, "technical")
        self.assertEqual(project.config.embedding_model, "")
        self.assertEqual(project.config.vision_model, "")
        self.assertEqual(project.config.audio_transcription_model, "")
        self.assertEqual(project.state["analysis"]["status"], "completed")
        self.assertTrue(project.document_path.is_file())
        self.assertIsNone(project.state["approved_hash"])
        self.assertFalse(self.config.database.exists())

    def test_invalid_duration_is_reprompted_without_silent_coercion(self):
        code, output = self.run_wizard("\ny\nnan\n-3\n0\ninfinity\n8\n\nq\n",
                                      project=self.directory, source=self.source)
        self.assertEqual(code, 0, output)
        self.assertEqual(Project.open(self.directory).load_storyboard()["duration_seconds"], 8)

    def test_interruption_preserves_existing_storyboard_without_reanalysis(self):
        project, document = self.project()
        path = project.document_path
        before = path.read_bytes()
        output = Terminal()
        with patch("sys.stdin", Terminal()), patch("sys.stdout", output), patch("builtins.input", side_effect=KeyboardInterrupt):
            code = wizard.run(self.config, project=self.directory)
        self.assertEqual(code, 130)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(Project.open(self.directory).load_storyboard(), document)
        self.assertIn(str(self.directory), output.getvalue())

    def test_resume_title_and_reorder_save_versions_then_approve(self):
        project, document = self.project()
        original = project.document_path
        before = original.read_bytes()
        code, output = self.run_wizard("r\nt\nMy film\nm\n1\n2\na\ny\nn\n", project=self.directory)
        self.assertEqual(code, 0, output)
        reopened = Project.open(self.directory)
        edited = reopened.load_storyboard()
        self.assertEqual(edited["title"], "My film")
        self.assertEqual(edited["items"], list(reversed(document["items"])))
        self.assertEqual(original.read_bytes(), before)
        self.assertNotEqual(reopened.document_path, original)
        self.assertTrue(reopened.state["approved_hash"])
        self.assertIsNone(reopened.state["editor"])

    def test_feedback_needs_regeneration_and_invalidates_old_approval(self):
        project, document = self.project()
        project.approve(document)
        code, output = self.run_wizard("r\nb\nx 1\nq\na\nq\n", project=self.directory)
        self.assertEqual(code, 0, output)
        reopened = Project.open(self.directory)
        self.assertIsNone(reopened.state["approved_hash"])
        self.assertIn("Regenerate after feedback before approving", output)
        self.assertEqual(reopened.list_media()[0]["feedback"]["selection"], "exclude")
        with self.assertRaises(ValueError):
            reopened.load_storyboard()
        code, output = self.run_wizard("g\n8\n\na\ny\nn\n", project=self.directory)
        self.assertEqual(code, 0, output)
        final = Project.open(self.directory)
        self.assertEqual(len(final.load_storyboard()["items"]), 1)
        self.assertTrue(final.state["approved_hash"])

    def test_rejected_hold_does_not_change_reviewed_or_saved_document(self):
        project, document = self.project(duration=4)
        session = wizard._Wizard(self.config)
        session.project = project
        session.document = deepcopy(document)
        path = project.document_path
        before = path.read_bytes()
        with patch("sys.stdin", Terminal("1\n99\n")), patch("sys.stdout", Terminal()):
            with self.assertRaisesRegex(ValueError, "budget"):
                session._edit("e")
        self.assertEqual(session.document, document)
        self.assertEqual(project.document_path, path)
        self.assertEqual(path.read_bytes(), before)

    def test_source_controls_and_external_titles_are_data_not_terminal_sequences(self):
        injected = self.source / "bad\u202ename.png"
        Image.new("RGB", (12, 12), (4, 20, 80)).save(injected)
        project, document = self.project(duration=12)
        document["title"] = "Title\x1b]0;injected\x07\nnext\u202e"
        project.save_storyboard(document)
        code, output = self.run_wizard("r\nq\n", project=self.directory)
        self.assertEqual(code, 0, output)
        self.assertNotIn("\x1b", output)
        self.assertNotIn("\x07", output)
        self.assertNotIn("\u202e", output)
        self.assertIn("bad\\u202ename.png", output)
        self.assertIn("Title\\x1b]0;injected\\x07\\nnext\\u202e", output)

    def test_contact_generation_does_not_open_files_without_request(self):
        project, document = self.project()
        session = wizard._Wizard(self.config)
        session.project = project
        session.document = document
        output = Terminal()
        with patch("sys.stdout", output), patch("storylens.wizard.webbrowser.open") as open_page:
            session._contacts()
        open_page.assert_not_called()
        self.assertTrue(session.pages)
        self.assertTrue(all(path.is_file() for path in session.pages))
        self.assertIn("Audio has no visual playback", output.getvalue())

    def test_failed_approval_recovers_to_menu_without_editor_write(self):
        self.project()
        with patch.object(Project, "approve", side_effect=ValueError("Storyboard changed concurrently")), patch.object(Project, "create_editor") as create:
            code, output = self.run_wizard("r\na\ny\nq\n", project=self.directory)
        self.assertEqual(code, 0, output)
        self.assertIn("Storyboard changed concurrently", output)
        self.assertNotIn("Storyboard approved.", output)
        create.assert_not_called()

    def test_partial_analysis_is_visible_requires_consent_and_exits_two(self):
        (self.source / "broken.png").write_bytes(b"not a readable image")
        code, output = self.run_wizard("\ny\nquit\n", project=self.directory, source=self.source)
        self.assertEqual(code, 2, output)
        project = Project.open(self.directory)
        self.assertEqual(project.state["analysis"]["status"], "partial")
        self.assertGreater(project.state["analysis"]["summary"]["failed"], 0)
        self.assertIsNone(project.document_path)
        self.assertIn("Partial analysis is NOT success", output)
        self.assertIn("broken.png", output)
        self.assertIn("Resume project:", output)

    def test_failed_editor_mutation_is_not_retried_or_reported_as_success(self):
        project, document = self.project()
        project.approve(document)
        session = wizard._Wizard(self.config)
        session.project = project
        session.document = document
        output = Terminal()
        executable = self.root / "trusted-editor"
        executable.write_text("unused test executable")
        answers = f"y\n{executable}\ny\nc\n24\ny\nq\n"
        report = {"status": "failed", "error": "batch stopped after partial work",
                  "project": str(self.root / "not-success.fcproj")}
        with patch("sys.stdin", Terminal(answers)), patch("sys.stdout", output), patch.object(Project, "create_editor", return_value=report) as create:
            session._editor()
        self.assertEqual(create.call_count, 1)
        self.assertIn("Operation did not succeed", output.getvalue())
        self.assertNotIn("Saved:", output.getvalue())
        self.assertIsNone(project.state["editor"])

    def test_editor_create_preview_and_export_each_need_separate_confirmation(self):
        project, document = self.project()
        project.approve(document)
        session = wizard._Wizard(self.config)
        session.project = project
        session.document = document
        executable = self.root / "trusted-editor"
        executable.write_text("unused test executable")
        answers = f"y\n{executable}\ny\nc\n24\nn\np\n0\nn\nx\nn\nq\n"
        with patch("sys.stdin", Terminal(answers)), patch("sys.stdout", Terminal()), patch.object(Project, "create_editor") as create, patch.object(Project, "preview_editor") as preview, patch.object(Project, "export_editor") as export:
            session._editor()
        create.assert_not_called()
        preview.assert_not_called()
        export.assert_not_called()
