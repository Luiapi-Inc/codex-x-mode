import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bridge.codex_catalog import CodexCatalogError, build_codex_catalog, write_codex_catalog


class CodexCatalogTests(unittest.TestCase):
    def bundled(self, models=None):
        if models is None:
            models = [
                {"slug": "gpt-5.5", "display_name": "GPT-5.5", "priority": 12,
                 "visibility": "list", "context_window": 100},
                {"slug": "gpt-5.6-luna", "display_name": "GPT-5.6-Luna", "priority": 8,
                 "visibility": "list", "context_window": 200},
                {"slug": "gpt-5.6-sol", "display_name": "GPT-5.6-Sol", "priority": 6,
                 "visibility": "list", "context_window": 300},
            ]
        return mock.Mock(returncode=0, stdout=json.dumps({"models": models}))

    @mock.patch("bridge.codex_catalog.subprocess.run")
    def test_build_clones_installed_metadata_for_packaged_aliases(self, run):
        run.return_value = self.bundled()
        catalog = build_codex_catalog({"codex_command": ["codex"]})

        self.assertEqual(
            [item["slug"] for item in catalog["models"]],
            ["chatgpt-web/5.6-sol", "chatgpt-web/5.6-luna", "chatgpt-web/5.5"],
        )
        self.assertEqual(
            [item["context_window"] for item in catalog["models"]],
            [300, 200, 100],
        )
        self.assertEqual([item["priority"] for item in catalog["models"]], [1, 2, 3])
        self.assertTrue(all(item["visibility"] == "list" for item in catalog["models"]))
        self.assertTrue(all(item["display_name"].startswith("ChatGPT Web — ") for item in catalog["models"]))
        run.assert_called_once_with(
            ["codex", "debug", "models", "--bundled"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

    @mock.patch("bridge.codex_catalog.subprocess.run")
    def test_missing_installed_model_metadata_fails_closed(self, run):
        run.return_value = self.bundled([
            {"slug": "gpt-5.5", "display_name": "GPT-5.5"},
            {"slug": "gpt-5.6-sol", "display_name": "GPT-5.6-Sol"},
        ])
        with self.assertRaisesRegex(CodexCatalogError, "gpt-5.6-luna"):
            build_codex_catalog({"codex_command": ["codex"]})

    @mock.patch("bridge.codex_catalog.subprocess.run")
    def test_write_catalog_is_mode_600_and_valid_json(self, run):
        run.return_value = self.bundled()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.json"
            written = write_codex_catalog({"codex_command": ["codex"]}, path)
            self.assertEqual(written, path.resolve())
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(
                json.loads(path.read_text())["models"][0]["slug"],
                "chatgpt-web/5.6-sol",
            )


if __name__ == "__main__":
    unittest.main()
