from __future__ import annotations

import json
import unittest
from pathlib import Path


class ManifestTest(unittest.TestCase):
    def test_mcp_manifest_uses_cross_platform_launcher_script(self) -> None:
        manifest_path = Path(__file__).resolve().parents[1] / ".mcp.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        server = manifest["mcpServers"]["eqemu-oracle"]
        self.assertEqual(server["command"], "./plugins/eqemu-oracle/scripts/eqemu_oracle_launcher.cmd")
        self.assertEqual(server["args"], ["mcp-serve"])


if __name__ == "__main__":
    unittest.main()
