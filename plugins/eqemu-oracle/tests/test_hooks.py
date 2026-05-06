from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HOOK_SCRIPT = PLUGIN_ROOT / "hooks" / "eqemu_oracle_hooks.py"


class HookTest(unittest.TestCase):
    def run_hook(self, mode: str, payload: dict[str, object]) -> tuple[int, str, str]:
        completed = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT), mode],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )
        return completed.returncode, completed.stdout.strip(), completed.stderr.strip()

    def transcript_file(self, content: str) -> str:
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False)
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        with handle:
            handle.write(content)
        return handle.name

    def test_hooks_manifest_registers_stop_and_post_tool_use(self) -> None:
        manifest = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        hooks = json.loads((PLUGIN_ROOT / "hooks.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["hooks"], "./hooks.json")
        self.assertIn("Stop", hooks["hooks"])
        self.assertIn("PostToolUse", hooks["hooks"])
        self.assertEqual(
            hooks["hooks"]["Stop"][0]["hooks"][0]["command"],
            "./scripts/eqemu_oracle_launcher.cmd hook stop",
        )
        self.assertEqual(
            hooks["hooks"]["PostToolUse"][0]["hooks"][0]["command"],
            "./scripts/eqemu_oracle_launcher.cmd hook post-tool-use",
        )

    def test_stop_hook_ignores_turn_without_explicit_invocation(self) -> None:
        path = self.transcript_file(json.dumps({"role": "user", "content": "How do hooks work?"}))

        code, stdout, stderr = self.run_hook(
            "stop",
            {"hook_event_name": "Stop", "transcript_path": path, "last_assistant_message": "A short answer."},
        )

        self.assertEqual(code, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")

    def test_stop_hook_requires_tool_usage_after_explicit_invocation(self) -> None:
        path = self.transcript_file(json.dumps({"role": "user", "content": "Use EQEmu Oracle to explain quest::say"}))

        code, stdout, _stderr = self.run_hook(
            "stop",
            {"hook_event_name": "Stop", "transcript_path": path, "last_assistant_message": "quest::say sends text."},
        )

        self.assertEqual(code, 0)
        result = json.loads(stdout)
        self.assertEqual(result["decision"], "block")
        self.assertIn("explicitly invoked EQEmu Oracle", result["reason"])

    def test_stop_hook_ignores_explicit_plugin_mechanics_request(self) -> None:
        path = self.transcript_file(json.dumps({"role": "user", "content": "Use EQEmu Oracle to explain the hooks manifest"}))

        code, stdout, stderr = self.run_hook(
            "stop",
            {"hook_event_name": "Stop", "transcript_path": path, "last_assistant_message": "The hooks manifest wires lifecycle scripts."},
        )

        self.assertEqual(code, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")

    def test_stop_hook_requires_provenance_for_substantive_oracle_answer(self) -> None:
        transcript = "\n".join(
            [
                json.dumps({"role": "user", "content": "Use EQEmu Oracle to explain quest::say"}),
                json.dumps({"tool": "get_quest_api_entry", "arguments": {"query": "quest::say"}}),
            ]
        )
        path = self.transcript_file(transcript)

        code, stdout, _stderr = self.run_hook(
            "stop",
            {
                "hook_event_name": "Stop",
                "transcript_path": path,
                "last_assistant_message": "This function sends text to nearby clients and is commonly used in NPC dialogue responses during quest interactions with players when scripts need a simple spoken response.",
            },
        )

        self.assertEqual(code, 0)
        result = json.loads(stdout)
        self.assertEqual(result["decision"], "block")
        self.assertIn("source type", result["reason"])

    def test_stop_hook_accepts_grounded_answer_with_provenance(self) -> None:
        transcript = "\n".join(
            [
                json.dumps({"role": "user", "content": "Use EQEmu Oracle to explain quest::say"}),
                json.dumps({"tool": "get_quest_api_entry", "arguments": {"query": "quest::say"}}),
            ]
        )
        path = self.transcript_file(transcript)

        code, stdout, stderr = self.run_hook(
            "stop",
            {
                "hook_event_name": "Stop",
                "transcript_path": path,
                "last_assistant_message": "From the EQEmu Oracle quest API entry, quest::say sends NPC dialogue text to nearby clients.",
            },
        )

        self.assertEqual(code, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")

    def test_stop_hook_accepts_cli_tool_fallback_usage(self) -> None:
        transcript = "\n".join(
            [
                json.dumps({"role": "user", "content": "Use EQEmu Oracle to explain quest::say"}),
                json.dumps(
                    {
                        "tool_input": {
                            "command": "py -3 plugins/eqemu-oracle/scripts/eqemu_oracle.py tool get_quest_api_entry --args '{\"language\":\"perl\",\"kind\":\"method\",\"name\":\"say\"}'"
                        }
                    }
                ),
            ]
        )
        path = self.transcript_file(transcript)

        code, stdout, stderr = self.run_hook(
            "stop",
            {
                "hook_event_name": "Stop",
                "transcript_path": path,
                "last_assistant_message": "From the EQEmu Oracle quest API entry, quest::say sends NPC dialogue text to nearby clients.",
            },
        )

        self.assertEqual(code, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")

    def test_stop_hook_requires_validation_after_extension_edits(self) -> None:
        transcript = "\n".join(
            [
                "apply_patch",
                "*** Update File: plugins/eqemu-oracle/extensions/docs/example.json",
                "+{\"pages\": []}",
            ]
        )
        path = self.transcript_file(transcript)

        code, stdout, _stderr = self.run_hook(
            "stop",
            {"hook_event_name": "Stop", "transcript_path": path, "last_assistant_message": "Updated the extension."},
        )

        self.assertEqual(code, 0)
        result = json.loads(stdout)
        self.assertEqual(result["decision"], "block")
        self.assertIn("extension overlay files", result["reason"])

    def test_stop_hook_accepts_extension_edits_with_validation(self) -> None:
        transcript = "\n".join(
            [
                "apply_patch",
                "*** Update File: plugins/eqemu-oracle/extensions/docs/example.json",
                "python3 plugins/eqemu-oracle/scripts/eqemu_oracle.py rebuild-extensions --scope docs --mode committed",
            ]
        )
        path = self.transcript_file(transcript)

        code, stdout, stderr = self.run_hook(
            "stop",
            {"hook_event_name": "Stop", "transcript_path": path, "last_assistant_message": "Updated and rebuilt the extension."},
        )

        self.assertEqual(code, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")

    def test_post_tool_use_ignores_irrelevant_bash(self) -> None:
        code, stdout, stderr = self.run_hook(
            "post-tool-use",
            {"hook_event_name": "PostToolUse", "tool_input": {"command": "python3 -m unittest"}, "tool_response": "ok"},
        )

        self.assertEqual(code, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")

    def test_post_tool_use_flags_failed_maintenance_command(self) -> None:
        code, stdout, _stderr = self.run_hook(
            "post-tool-use",
            {
                "hook_event_name": "PostToolUse",
                "tool_input": {"command": "python3 plugins/eqemu-oracle/scripts/eqemu_oracle.py rebuild-extensions --scope all"},
                "tool_response": "Traceback: ExtensionValidationError: invalid record",
            },
        )

        self.assertEqual(code, 0)
        result = json.loads(stdout)
        self.assertFalse(result["continue"])
        self.assertIn("maintenance command output looks unsuccessful", result["systemMessage"])

    def test_post_tool_use_flags_missing_bundle_path(self) -> None:
        code, stdout, _stderr = self.run_hook(
            "post-tool-use",
            {
                "hook_event_name": "PostToolUse",
                "tool_input": {"command": "python3 plugins/eqemu-oracle/scripts/eqemu_oracle.py build-release-bundle"},
                "tool_response": "Process exited with code 0",
            },
        )

        self.assertEqual(code, 0)
        result = json.loads(stdout)
        self.assertFalse(result["continue"])
        self.assertIn("archive_path", result["systemMessage"])


if __name__ == "__main__":
    unittest.main()
