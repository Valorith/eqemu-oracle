from __future__ import annotations

import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


class SkillGuidanceTest(unittest.TestCase):
    def test_umbrella_skill_describes_remote_profile_discovery_and_undo(self) -> None:
        text = (PLUGIN_ROOT / "skills" / "eqemu-oracle" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("list_eqemu_server_ftp_profiles", text)
        self.assertIn("get_eqemu_server_ftp_onboarding", text)
        self.assertIn("exactly one FTP/FTPS profile", text)
        self.assertIn("multiple profiles", text)
        self.assertIn("preview_eqemu_server_ftp_undo", text)
        self.assertIn("undo_eqemu_server_ftp_write", text)
        self.assertIn("set_eqemu_server_ftp_read_only_mode", text)
        self.assertIn("read_only", text)
        self.assertIn("map_eqemu_server_ftp_layout", text)
        self.assertIn("narrowest useful scope", text)
        self.assertIn("Do not guess remote file paths", text)
        self.assertIn("trust_eqemu_server_ftp_certificate", text)
        self.assertIn("confirm_sha256", text)
        self.assertIn("/quests/global/items", text)
        self.assertIn("Lua script is the active one", text)
        self.assertIn("explicitly instructs", text)
        self.assertIn("garbage_collect_eqemu_server_ftp_write_history", text)
        self.assertIn("retention", text)
        self.assertIn("operation id", text)
        self.assertIn("delete_eqemu_server_ftp_file", text)
        self.assertIn("confirm_remote_sha256", text)
        self.assertIn("fix a script error", text)
        self.assertIn("stage/download that exact script", text)
        self.assertIn("Nexus CLI", text)
        self.assertIn("Webhook Inbox", text)
        self.assertIn("script errors", text)
        self.assertIn("crash-report payloads", text)
        self.assertIn("value-added", text)
        self.assertIn("Test Manager change record", text)
        self.assertIn("Only create it after explicit user approval", text)
        self.assertIn("player-visible issue", text)
        self.assertIn("what should be tested", text)
        self.assertIn("ask the user to review and explicitly approve", text)
        self.assertIn("validate the fix is in place", text)
        self.assertIn("ask for explicit permission to exit read-only mode", text)
        self.assertIn("re-enable read-only mode automatically", text)
        self.assertIn("report the final read-only state", text)

    def test_scripting_skill_describes_remote_staging_and_guarded_write_tools(self) -> None:
        text = (PLUGIN_ROOT / "skills" / "eqemu-scripting-api" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("list_eqemu_server_ftp_profiles", text)
        self.assertIn("map_eqemu_server_ftp_layout", text)
        self.assertIn('scope="zone"', text)
        self.assertIn('scope="plugins"', text)
        self.assertIn("trust_eqemu_server_ftp_certificate", text)
        self.assertIn("stage_eqemu_server_ftp_file", text)
        self.assertIn("upload_eqemu_server_ftp_file", text)
        self.assertIn("delete_eqemu_server_ftp_file", text)
        self.assertIn("confirm_remote_path", text)
        self.assertIn("confirm_remote_sha256", text)
        self.assertIn("set_eqemu_server_ftp_read_only_mode", text)
        self.assertIn("read_only", text)
        self.assertIn("list_eqemu_server_ftp_write_history", text)
        self.assertIn("confirm_operation_id", text)
        self.assertIn("garbage_collect_eqemu_server_ftp_write_history", text)
        self.assertIn("Lua is active and Perl is shadowed", text)
        self.assertIn("fixing a script error", text)
        self.assertIn("stage/download that exact script", text)
        self.assertIn("Nexus CLI", text)
        self.assertIn("Webhook Inbox", text)
        self.assertIn("script errors", text)
        self.assertIn("crash-report payloads", text)
        self.assertIn("value-added", text)
        self.assertIn("Test Manager change record", text)
        self.assertIn("Only create it after explicit user approval", text)
        self.assertIn("player-visible issue", text)
        self.assertIn("what should be tested", text)
        self.assertIn("explicitly approve uploading it back", text)
        self.assertIn("validate the fixed content is in place", text)
        self.assertIn("ask for explicit permission to exit read-only mode", text)
        self.assertIn("re-enable read-only mode automatically", text)
        self.assertIn("report the final read-only state", text)


if __name__ == "__main__":
    unittest.main()
