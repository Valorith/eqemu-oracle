from __future__ import annotations

import json
import unittest
from pathlib import Path


class ManifestTest(unittest.TestCase):
    def test_mcp_manifest_uses_python_entrypoint(self) -> None:
        manifest_path = Path(__file__).resolve().parents[1] / ".mcp.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        server = manifest["mcpServers"]["eqemu_oracle"]
        self.assertEqual(server["command"], "python")
        self.assertEqual(server["args"], ["./scripts/eqemu_oracle.py", "mcp-serve"])
        self.assertTrue((manifest_path.parent / "scripts" / "eqemu_oracle.py").exists())


if __name__ == "__main__":
    unittest.main()
