"""Bare CLI is safe in pipelines; explicit interactive workflows never hang them."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


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
