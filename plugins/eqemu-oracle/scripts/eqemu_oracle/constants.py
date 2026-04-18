from __future__ import annotations

from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PLUGIN_ROOT.parents[1]
CONFIG_ROOT = PLUGIN_ROOT / "config"
SOURCES_CONFIG_PATH = CONFIG_ROOT / "sources.toml"
LOCAL_SOURCES_CONFIG_PATH = CONFIG_ROOT / "sources.local.toml"
DATA_ROOT = PLUGIN_ROOT / "data"
BASE_ROOT = DATA_ROOT / "base"
MERGED_ROOT = DATA_ROOT / "merged"
CACHE_ROOT = PLUGIN_ROOT / "cache"
OVERLAY_ROOT = CACHE_ROOT / "overlay"
MAINTENANCE_LOCK_ROOT = CACHE_ROOT / "maintenance.lock"
EXTENSIONS_ROOT = PLUGIN_ROOT / "extensions"
LOCAL_EXTENSIONS_ROOT = PLUGIN_ROOT / "local-extensions"
SEARCH_DB_PATH = CACHE_ROOT / "search.sqlite3"
PLUGIN_VERSION = "0.1.3"
SERVER_NAME = "eqemu-oracle"
DOMAIN_CHOICES = ("quest-api", "schema", "docs")
SCOPE_CHOICES = ("all", *DOMAIN_CHOICES)
MODE_CHOICES = ("committed", "overlay")
QUEST_LANGUAGE_CHOICES = ("perl", "lua")
QUEST_KIND_CHOICES = ("method", "event", "constant")
