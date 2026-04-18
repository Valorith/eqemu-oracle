from __future__ import annotations

import copy
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    import tomllib as _tomllib
except ModuleNotFoundError:
    try:
        import tomli as _tomllib  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        _tomllib = None

from .constants import LOCAL_SOURCES_CONFIG_PATH, SOURCES_CONFIG_PATH


class SourceConfigError(ValueError):
    pass


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if _tomllib is None:
        raise SourceConfigError("TOML parsing requires Python 3.11+ or the `tomli` package on Python 3.10 and earlier")
    return _tomllib.loads(path.read_text(encoding="utf-8"))


def _merge_dicts(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _normalize_repo_url(value: str) -> str:
    normalized = value.strip()
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    return normalized.rstrip("/")


def _github_repo_slug(repo_url: str) -> str | None:
    match = re.match(r"^https://github\.com/([^/]+)/([^/]+)$", repo_url)
    if not match:
        return None
    return f"{match.group(1)}/{match.group(2)}"


def _derive_commit_api(repo_url: str, branch: str) -> str | None:
    slug = _github_repo_slug(repo_url)
    if slug is None:
        return None
    return f"https://api.github.com/repos/{slug}/commits/{branch}"


def _derive_archive_url(repo_url: str, branch: str) -> str | None:
    slug = _github_repo_slug(repo_url)
    if slug is None:
        return None
    return f"https://github.com/{slug}/archive/refs/heads/{branch}.zip"


def _derive_source_file_base(repo_url: str, branch: str) -> str:
    return f"{repo_url}/blob/{branch}"


def _require_string(section: str, payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SourceConfigError(f"[{section}] requires a non-empty `{key}` value")
    return value.strip()


def _normalize_quest_api(payload: dict[str, Any]) -> dict[str, str]:
    definitions_url = _require_string("quest_api", payload, "definitions_url")
    repo = _normalize_repo_url(_require_string("quest_api", payload, "repo"))
    branch = _require_string("quest_api", payload, "branch")
    commit_api = payload.get("commit_api")
    if commit_api is None:
        commit_api = _derive_commit_api(repo, branch)
    if not isinstance(commit_api, str) or not commit_api.strip():
        raise SourceConfigError("[quest_api] requires `commit_api` for non-GitHub repositories")
    return {
        "definitions_url": definitions_url,
        "repo": repo,
        "branch": branch,
        "commit_api": commit_api.strip(),
    }


def _normalize_docs(payload: dict[str, Any]) -> dict[str, str]:
    repo = _normalize_repo_url(_require_string("docs", payload, "repo"))
    branch = _require_string("docs", payload, "branch")
    site_base_url = _require_string("docs", payload, "site_base_url").rstrip("/")

    commit_api = payload.get("commit_api")
    if commit_api is None:
        commit_api = _derive_commit_api(repo, branch)
    if not isinstance(commit_api, str) or not commit_api.strip():
        raise SourceConfigError("[docs] requires `commit_api` for non-GitHub repositories")

    archive_url = payload.get("archive_url")
    if archive_url is None:
        archive_url = _derive_archive_url(repo, branch)
    if not isinstance(archive_url, str) or not archive_url.strip():
        raise SourceConfigError("[docs] requires `archive_url` for non-GitHub repositories")

    source_file_base = payload.get("source_file_base")
    if source_file_base is None:
        source_file_base = _derive_source_file_base(repo, branch)
    if not isinstance(source_file_base, str) or not source_file_base.strip():
        raise SourceConfigError("[docs] requires a non-empty `source_file_base`")

    return {
        "repo": repo,
        "branch": branch,
        "site_base_url": site_base_url,
        "commit_api": commit_api.strip(),
        "archive_url": archive_url.strip(),
        "source_file_base": source_file_base.rstrip("/"),
    }


def load_source_config(
    default_path: Path = SOURCES_CONFIG_PATH,
    local_override_path: Path = LOCAL_SOURCES_CONFIG_PATH,
) -> dict[str, dict[str, str]]:
    merged = _merge_dicts(_load_toml(default_path), _load_toml(local_override_path))
    return {
        "quest_api": _normalize_quest_api(merged.get("quest_api", {})),
        "docs": _normalize_docs(merged.get("docs", {})),
    }


@lru_cache(maxsize=1)
def get_source_config() -> dict[str, dict[str, str]]:
    return load_source_config()


def clear_source_config_cache() -> None:
    get_source_config.cache_clear()
