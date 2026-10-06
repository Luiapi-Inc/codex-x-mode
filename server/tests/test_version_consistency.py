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
        self.assertEqual(version, "0.2.22")
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

    def test_codex_plugin_mcp_targets_live_loopback_bridge_without_embedded_secret(self):
        root = Path(__file__).resolve().parents[2]

        portable_servers = json.loads((root / "mcp.json").read_text())["mcpServers"]
        self.assertEqual(set(portable_servers), {"codex-x-mode", "codex-x-app"})
        self.assertEqual(portable_servers["codex-x-mode"]["type"], "streamable-http")
        self.assertEqual(portable_servers["codex-x-mode"]["url"], "http://127.0.0.1:8240/mcp")
        self.assertEqual(portable_servers["codex-x-app"]["type"], "streamable-http")
        self.assertEqual(portable_servers["codex-x-app"]["url"], "http://127.0.0.1:8240/codex-x-app/mcp")
        for server in portable_servers.values():
            self.assertNotIn("headers", server)

        codex_servers = json.loads((root / ".mcp.json").read_text())["mcpServers"]
        self.assertEqual(set(codex_servers), {"codex-x-mode", "codex-x-app"})
        for name, portable in portable_servers.items():
            codex = codex_servers[name]
            self.assertEqual(codex["type"], "http")
            self.assertEqual(codex["url"], portable["url"])
            self.assertEqual(codex["bearer_token_env_var"], "CODEX_X_MCP_TOKEN")
            self.assertNotIn("http_headers", codex)
            self.assertNotIn("env", codex)

        skill = root / "skills/codex-x-app-tool/SKILL.md"
        self.assertTrue(skill.is_file())
        text = skill.read_text()
        self.assertIn("name: codex-x-app-tool", text)
        self.assertIn("codex-cli 0.160.1", text)
        self.assertIn("Do not require or invoke the external `codex-app-tools@openai-bundled` package at runtime.", text)

        agent = root / "skills/codex-x-app-tool/agents/openai.yaml"
        self.assertTrue(agent.is_file())
        agent_text = agent.read_text()
        self.assertIn('display_name: "Codex X Mode Tool"', agent_text)
        self.assertIn("- CHAT", agent_text)
        self.assertIn("- CODEX", agent_text)
        self.assertIn("type: mcp", agent_text)
        self.assertIn("value: codex-x-app", agent_text)
        self.assertNotIn("codex-app-tools@openai-bundled", agent_text)

    def test_current_model_contract_is_native_codex_owned(self):
        root = Path(__file__).resolve().parents[2]
        runtime_contract = (root / "skills/codex-x-mode/references/runtime-contracts.md").read_text()
        source_of_truth = (root / "docs/CODEX_X_MODE_SOURCE_OF_TRUTH.md").read_text()

        self.assertIn("Native Codex app-server `model/list`", runtime_contract)
        self.assertIn("Native Codex `model/list` visibility", runtime_contract)
        self.assertNotIn("authorized ChatGPT account catalog", runtime_contract)
        self.assertNotIn("account registration", runtime_contract)
        self.assertNotIn("api.openai.com/v1/models", runtime_contract)

        self.assertIn("packaged model registry ∩ Native Codex model/list for the signed-in account", source_of_truth)
        self.assertNotIn("packaged model registry ∩ authorized ChatGPT account catalog", source_of_truth)
