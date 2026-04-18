from __future__ import annotations

from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PLUGIN_ROOT.parents[1]
DATA_ROOT = PLUGIN_ROOT / "data"
BASE_ROOT = DATA_ROOT / "base"
MERGED_ROOT = DATA_ROOT / "merged"
CACHE_ROOT = PLUGIN_ROOT / "cache"
OVERLAY_ROOT = CACHE_ROOT / "overlay"
EXTENSIONS_ROOT = PLUGIN_ROOT / "extensions"
LOCAL_EXTENSIONS_ROOT = PLUGIN_ROOT / "local-extensions"
SEARCH_DB_PATH = CACHE_ROOT / "search.sqlite3"
PLUGIN_VERSION = "0.1.0"
SERVER_NAME = "eqemu-oracle"
DEFAULT_DOCS_BRANCH = "main"
QUEST_API_URL = "https://spire.eqemu.dev/api/v1/quest-api/definitions"
QUEST_API_REPO = "https://github.com/Valorith/spire"
DOCS_REPO = "https://github.com/EQEmu/eqemu-docs-v2"
DOCS_ZIP_URL = "https://github.com/EQEmu/eqemu-docs-v2/archive/refs/heads/main.zip"
SPIRE_COMMIT_API = "https://api.github.com/repos/Valorith/spire/commits/master"
DOCS_COMMIT_API = "https://api.github.com/repos/EQEmu/eqemu-docs-v2/commits/main"
