"""Terminal actions persist real projects; optional/network work needs consent."""

from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from storylens.config import Config
from storylens.terminal_wizard import TerminalWizard, _ask, _display, _path, _quote, choose_interface
from storylens.wizard_session import WizardSession


class TerminalWizardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "original media"
        self.source.mkdir()
        self.photo = self.source / "photo.png"
        Image.new("RGB", (32, 24), (70, 140, 40)).save(self.photo)
        self.project = self.root / "saved project"
        self.config = Config(self.root / "unused.sqlite3")

    def interact(self, wizard, answers, *, source=None):
        with redirect_stdout(io.StringIO()), patch("builtins.input", side_effect=answers):
            return wizard.run(self.project, source)

    def test_analyze_quit_reopen_and_configure_without_manual_files(self):
        wizard = TerminalWizard(self.config)
        self.assertEqual(self.interact(wizard, ["1", "q"], source=self.source), 0)
        saved = wizard.session.snapshot()
        self.assertGreaterEqual(saved["technical"]["summary"]["analyzed"], 1)
        self.assertTrue((self.project / "project.json").is_file())
        self.assertTrue(Path(saved["project"]["database"]).is_file())
        self.assertFalse(self.config.database.exists())

        reopened = TerminalWizard(self.config)
        self.assertEqual(self.interact(reopened, ["4", "1", "900", "180", "y", "q"]), 0)
        after = WizardSession(self.config).open(str(self.project))
        self.assertEqual(after["technical"]["summary"], saved["technical"]["summary"])
        self.assertEqual(after["project"]["settings"]["timeout"], 900)
        self.assertEqual(after["project"]["settings"]["semantic_timeout"], 180)
        self.assertTrue(self.photo.is_file())

    def test_create_navigation_uses_readiness_and_persists_the_chosen_paths(self):
        wizard = TerminalWizard(self.config)
        answers = ["2", str(self.source), str(self.project), "y", "q"]
        with redirect_stdout(io.StringIO()), patch("builtins.input", side_effect=answers):
            self.assertEqual(wizard.run(), 0)
        reopened = WizardSession(self.config).open(str(self.project))
        self.assertEqual(Path(reopened["project"]["source"]), self.source)
        self.assertEqual(Path(reopened["project"]["directory"]), self.project)

    def test_new_project_suggests_a_separate_output_folder_after_source_selection(self):
        home = self.root / "home"
        expected = home / "storylens-projects" / self.source.name
        wizard = TerminalWizard(self.config)
        with patch("storylens.terminal_wizard.Path.home", return_value=home), \
                redirect_stdout(io.StringIO()), \
                patch("builtins.input", side_effect=["2", str(self.source), "", "y", "q"]):
            self.assertEqual(wizard.run(), 0)
        self.assertTrue((expected / "project.json").is_file())
        self.assertEqual(Path(wizard.session.snapshot()["project"]["directory"]), expected)

    def test_repair_then_retry_keeps_successful_cached_analysis(self):
        broken = self.source / "broken.png"
        broken.write_bytes(b"not an image")
        first = TerminalWizard(self.config)
        self.assertEqual(self.interact(first, ["1", "q"], source=self.source), 2)
        summary = first.session.snapshot()["technical"]["summary"]
        self.assertGreaterEqual(summary["failed"], 1)
        self.assertTrue(summary["errors"])
        Image.new("RGB", (32, 24), (25, 80, 190)).save(broken)
        retry = TerminalWizard(self.config)
        self.assertEqual(self.interact(retry, ["2", "1", "q"]), 0)
        summary = retry.session.snapshot()["technical"]["summary"]
        self.assertEqual(summary["failed"], 0)
        self.assertGreaterEqual(summary["cache_hits"], 1)

    def test_ai_skip_does_not_infer_download_or_change_saved_results(self):
        wizard = TerminalWizard(self.config)
        self.interact(wizard, ["1", "q"], source=self.source)
        before = wizard.session.snapshot()["technical"]
        options = {"capabilities": [], "instructions": []}
        with patch.object(wizard.session, "ai_options", return_value=options), \
                patch.object(wizard.session, "sample_ai") as sample, \
                patch.object(wizard.session, "install_model") as install:
            self.interact(wizard, ["3", "b", "q"])
        sample.assert_not_called()
        install.assert_not_called()
        self.assertEqual(wizard.session.snapshot()["technical"], before)

    def test_ai_sample_requires_explicit_confirmation(self):
        wizard = TerminalWizard(self.config)
        wizard.session.open(str(self.project), str(self.source))
        with patch.object(wizard.session, "ai_options", return_value={"capabilities": []}), \
                patch.object(wizard.session, "sample_ai", return_value={"ready": True}) as sample, \
                redirect_stdout(io.StringIO()), \
                patch("builtins.input", side_effect=["4", "n", "4", "y", "b"]):
            wizard._ai_advanced()
        sample.assert_called_once()

    def test_guided_setup_saves_selected_models_without_running_unapproved_inference(self):
        wizard = TerminalWizard(self.config)
        self.interact(wizard, ["1", "q"], source=self.source)
        before = wizard.session.snapshot()["technical"]
        model = self.root / "installed clip"
        model.mkdir()
        (model / "config.json").write_text('{"model_type":"clip"}')
        capability = {"id": "embeddings", "title": "Visual search", "description": "Search by meaning",
                      "model_path": str(model), "installed": True}
        with patch.object(wizard.session, "ai_options", return_value={"capabilities": [capability]}), \
                patch.object(wizard.session, "check_ai", return_value={"ready": True}) as check, \
                patch.object(wizard.session, "sample_ai") as sample, \
                patch.object(wizard.session, "install_model") as install, \
                patch.object(wizard.session, "analyze") as analyze, \
                redirect_stdout(io.StringIO()), \
                patch("builtins.input", side_effect=["y", "1", "n", "y", "n", "n"]):
            wizard._guided_ai()
        check.assert_called_once()
        sample.assert_not_called()
        install.assert_not_called()
        analyze.assert_not_called()
        reopened = WizardSession(self.config).open(str(self.project))
        self.assertEqual(reopened["project"]["settings"]["embedding_model"], str(model))
        self.assertEqual(reopened["technical"], before)

    def test_guided_download_decline_preserves_the_project(self):
        wizard = TerminalWizard(self.config)
        wizard.session.open(str(self.project), str(self.source))
        before = wizard.session.snapshot()["project"]["settings"]
        capability = {"id": "embeddings", "title": "Visual search", "installed": False}
        with patch.object(wizard.session, "ai_options", return_value={"capabilities": [capability]}), \
                patch.object(wizard.session, "install_model") as install, \
                redirect_stdout(io.StringIO()), patch("builtins.input", side_effect=["y", "2", "n"]):
            wizard._guided_ai()
        install.assert_not_called()
        self.assertEqual(wizard.session.snapshot()["project"]["settings"], before)

    def test_model_download_decline_does_not_call_session_install(self):
        wizard = TerminalWizard(self.config)
        capabilities = [{"id": "embeddings", "title": "Meaning search", "model_path": "/local/model"}]
        with patch.object(wizard.session, "install_model") as install, \
                redirect_stdout(io.StringIO()), patch("builtins.input", side_effect=["1", "n"]):
            wizard._download(capabilities, {})
        install.assert_not_called()

    def test_model_download_accepts_consent_and_stages_but_does_not_save(self):
        wizard = TerminalWizard(self.config)
        pending = {}
        capabilities = [{"id": "embeddings", "title": "Meaning search"}]
        result = {"settings": {"embedding_model": "/local/model"}}
        with patch.object(wizard.session, "install_model", return_value=result) as install, \
                patch.object(wizard.session, "configure") as configure, \
                redirect_stdout(io.StringIO()), patch("builtins.input", side_effect=["1", "y"]):
            wizard._download(capabilities, pending)
        install.assert_called_once_with("embeddings", consent=True, settings=pending)
        configure.assert_not_called()
        self.assertEqual(pending["embedding_model"], "/local/model")

    def test_interrupt_requests_cancellation_then_waits_for_worker(self):
        wizard = TerminalWizard(self.config)
        wizard.session = Mock()
        wizard.session.snapshot.side_effect = [
            {"busy": True, "elapsed": 1, "summary": {}},
            {"busy": False, "status": "cancelled", "summary": {}}]
        with patch("storylens.terminal_wizard.time.sleep", side_effect=KeyboardInterrupt), \
                redirect_stdout(io.StringIO()):
            wizard._analyze()
        wizard.session.analyze.assert_called_once_with("technical")
        wizard.session.cancel.assert_called_once()
        self.assertEqual(wizard.session.snapshot.call_count, 2)

    def test_invalid_timeout_can_be_corrected_before_saving(self):
        wizard = TerminalWizard(self.config)
        wizard.session.open(str(self.project), str(self.source))
        with redirect_stdout(io.StringIO()), \
                patch("builtins.input", side_effect=["not a number", "700", "180", "y"]):
            wizard._settings()
        self.assertEqual(wizard.session.snapshot()["project"]["settings"]["timeout"], 700)

    def test_pasted_quoted_paths_are_not_treated_as_shell_commands(self):
        with patch("builtins.input", return_value='"' + str(self.source) + '"'):
            self.assertEqual(_path("Source"), str(self.source))
        quoted = _quote(self.source)
        self.assertEqual(quoted[0], quoted[-1])
        self.assertIn(str(self.source), quoted)

    def test_interface_invalid_selection_recovers_and_cancel_leaves(self):
        with redirect_stdout(io.StringIO()), patch("builtins.input", side_effect=["invalid", "2"]):
            self.assertEqual(choose_interface(), "terminal")
        with redirect_stdout(io.StringIO()), patch("builtins.input", side_effect=KeyboardInterrupt):
            self.assertIsNone(choose_interface())

    def test_external_values_cannot_emit_terminal_controls_or_bidi_spoofing(self):
        malicious = "photo" + chr(27) + "[2J" + chr(13) + chr(10) + chr(7) + chr(0x202E) + ".png"
        output = io.StringIO()
        with redirect_stdout(output):
            _display({"path": malicious, "model_result": malicious})
        rendered = output.getvalue()
        for character in (chr(27), chr(13), chr(7), chr(0x202E)):
            self.assertNotIn(character, rendered)
        with patch("builtins.input", return_value="") as prompt:
            _ask("Model", malicious)
        self.assertNotIn(chr(27), prompt.call_args.args[0])
        self.assertNotIn(chr(10), prompt.call_args.args[0])

    def test_recommended_action_after_basic_results_opens_ai_without_rerunning_analysis(self):
        wizard = TerminalWizard(self.config)
        self.interact(wizard, ["1", "q"], source=self.source)
        with patch.object(wizard.session, "analyze") as analyze:
            self.assertEqual(self.interact(wizard, ["1", "b", "q"]), 0)
        analyze.assert_not_called()
