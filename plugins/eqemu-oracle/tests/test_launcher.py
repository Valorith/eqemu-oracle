from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


class LauncherTest(unittest.TestCase):
    def test_launcher_help_works_on_current_platform(self) -> None:
        launcher = Path(__file__).resolve().parents[1] / "scripts" / "eqemu_oracle_launcher.cmd"
        completed = subprocess.run(
            [str(launcher), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn("EQEmu Oracle plugin runtime", completed.stdout)
        self.assertEqual(completed.stderr, "")

    def test_launcher_mcp_serve_returns_clean_initialize_response(self) -> None:
        launcher = Path(__file__).resolve().parents[1] / "scripts" / "eqemu_oracle_launcher.cmd"
        proc = subprocess.Popen(
            [str(launcher), "mcp-serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        assert proc.stdin is not None
        assert proc.stdout is not None
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "launcher-test", "version": "0"},
            },
        }
        body = json.dumps(request).encode("utf-8")
        proc.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8"))
        proc.stdin.write(body)
        proc.stdin.flush()

        headers: dict[str, str] = {}
        while True:
            line = proc.stdout.readline()
            self.assertTrue(line, "expected MCP response headers from launcher")
            if line in (b"\r\n", b"\n"):
                break
            name, _, value = line.decode("utf-8").partition(":")
            headers[name.lower()] = value.strip()

        content_length = int(headers["content-length"])
        payload = json.loads(proc.stdout.read(content_length).decode("utf-8"))
        self.assertEqual(payload["result"]["serverInfo"]["name"], "eqemu-oracle")

        proc.stdin.close()
        stdout_tail, stderr = proc.communicate(timeout=5)
        self.assertEqual(stdout_tail, b"")
        self.assertEqual(stderr.decode("utf-8"), "")
