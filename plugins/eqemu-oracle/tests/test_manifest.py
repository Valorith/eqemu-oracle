from __future__ import annotations

import json
import unittest
from pathlib import Path


class ManifestTest(unittest.TestCase):
    def test_mcp_manifest_uses_windows_safe_python_launcher(self) -> None:
        manifest_path = Path(__file__).resolve().parents[1] / ".mcp.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        server = manifest["mcpServers"]["eqemu-oracle"]
        self.assertEqual(server["command"], "python")
        self.assertEqual(server["args"], ["./plugins/eqemu-oracle/scripts/eqemu_oracle.py", "mcp-serve"])


if __name__ == "__main__":
    unittest.main()
