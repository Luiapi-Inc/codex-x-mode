import ast
import ast
import json
import tarfile
import tomllib
import unittest
from pathlib import Path

from bridge.mcp import SERVER_INFO
from bridge.schema import schema
from bridge.service import status


class VersionConsistencyTests(unittest.TestCase):
    def test_release_versions_match_in_package_runtime_and_bundle(self):
        root = Path(__file__).resolve().parents[2]
        manifest = json.loads((root / "plugin.json").read_text())
        version = manifest["version"]
        self.assertEqual(version, "0.2.14")
        self.assertEqual(json.loads((root / ".codex-plugin/plugin.json").read_text())["version"], version)
        self.assertEqual(tomllib.loads((root / "server/pyproject.toml").read_text())["project"]["version"], version)
        self.assertEqual(SERVER_INFO["version"], version)
        self.assertEqual(schema("https://bridge.example.invalid")["info"]["version"], version)
        self.assertEqual(json.loads((root / "server/openapi-template.json").read_text())["info"]["version"], version)
        # Extract literal status version without opening a persistent database.
        def status_version(source):
            tree = ast.parse(source)
            fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "status")
            result = next(node.value for node in ast.walk(fn) if isinstance(node, ast.Return))
            values = dict(zip((ast.literal_eval(key) for key in result.keys), result.values))
            return ast.literal_eval(values["version"])
        self.assertEqual(status_version((root / "server/bridge/service.py").read_text()), version)
        with tarfile.open(root / "assets/codex-x-mode-bridge.tar.gz") as bundle:
            prefix = "codex-x-mode-bridge/"
            for path in (root / "server").rglob("*"):
                if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                    relative = path.relative_to(root / "server").as_posix()
                    self.assertEqual(bundle.extractfile(prefix + relative).read(), path.read_bytes(), relative)
            self.assertFalse(any("__pycache__" in name or name.endswith(".pyc") for name in bundle.getnames()))
