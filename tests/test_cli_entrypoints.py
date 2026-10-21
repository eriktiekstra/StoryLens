"""Bare CLI is safe in pipelines; explicit interactive workflows never hang them."""

from contextlib import redirect_stderr, redirect_stdout
import io

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from storylens.cli import main, parser


class _Terminal(io.StringIO):
    def isatty(self):
        return True


class EntrypointTests(unittest.TestCase):
    def invoke(self, *arguments):
        environment = {key: value for key, value in os.environ.items() if not key.startswith("STORYLENS_")}
        return subprocess.run([sys.executable, "-m", "storylens", *map(str, arguments)],
                              input="", capture_output=True, text=True, timeout=10, env=environment)

    def test_bare_pipeline_prints_help_without_creating_a_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "never-created.sqlite3"
            result = self.invoke("--database", database)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("wizard", result.stdout)
            self.assertFalse(database.exists())

    def test_explicit_wizard_requires_terminal_before_any_project_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "never-created"
            result = self.invoke("wizard", "--project", project)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("interactive", result.stderr.lower())
            self.assertFalse(project.exists())

    def test_machine_mode_without_command_and_wizard_reject_without_prompts(self):
        for arguments in (("--json",), ("--json", "wizard")):
            with self.subTest(arguments=arguments):
                result = self.invoke(*arguments)
                self.assertEqual(result.returncode, 1, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["schema_version"], "1.0")
                self.assertIn("error", payload)

    def test_wizard_cannot_silently_reuse_an_explicit_global_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "private.sqlite3"
            database.write_bytes(b"unchanged private catalog")
            result = self.invoke("--database", database, "wizard")
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("--project", result.stderr)
            self.assertEqual(database.read_bytes(), b"unchanged private catalog")

    def interactive(self, arguments, answers):
        output, errors = _Terminal(), _Terminal()
        with patch("sys.stdin", _Terminal()), redirect_stdout(output), redirect_stderr(errors), \
                patch("builtins.input", side_effect=answers):
            result = main(arguments)
        return result, output.getvalue(), errors.getvalue()

    def test_bare_and_explicit_wizard_choose_a_real_terminal_interface(self):
        for arguments in ([], ["wizard"]):
            with self.subTest(arguments=arguments):
                # The actual interface selector consumes 2; the actual terminal's
                # open/create menu consumes q. No browser or project is opened.
                with patch("storylens.web_wizard.run") as web:
                    result, _, errors = self.interactive(arguments, ["2", "q"])
                self.assertEqual(result, 0, errors)
                web.assert_not_called()

    def test_browser_choice_and_explicit_flags_dispatch_without_extra_prompts(self):
        cases = ((["wizard"], ["1"], "web_wizard"),
                 (["wizard", "--web"], [], "web_wizard"),
                 (["wizard", "--terminal"], [], "terminal_wizard"))
        for arguments, answers, module in cases:
            with self.subTest(arguments=arguments):
                with patch(f"storylens.{module}.run", return_value=0) as run:
                    result, _, errors = self.interactive(arguments, answers)
                self.assertEqual(result, 0, errors)
                run.assert_called_once()

    def test_explicit_interface_flags_are_mutually_exclusive(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            parser().parse_args(["wizard", "--web", "--terminal"])
        self.assertEqual(error.exception.code, 2)

    def test_each_explicit_mode_still_rejects_pipelines_and_machine_output(self):
        for mode in ("--web", "--terminal"):
            with self.subTest(mode=mode):
                self.assertEqual(self.invoke("wizard", mode).returncode, 1)
                result = self.invoke("--json", "wizard", mode)
                self.assertEqual(result.returncode, 1)
                self.assertIn("error", json.loads(result.stdout))

    def test_cancelling_the_interface_choice_does_not_launch_either_frontend(self):
        with patch("storylens.web_wizard.run") as web, \
                patch("storylens.terminal_wizard.run") as terminal:
            result, _, errors = self.interactive([], ["q"])
        self.assertEqual(result, 0, errors)
        web.assert_not_called()
        terminal.assert_not_called()
