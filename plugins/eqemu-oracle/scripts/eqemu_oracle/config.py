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


def _strip_inline_comment(value: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    for index, char in enumerate(value):
        if char == "\\" and (in_single or in_double):
            escaped = not escaped
            continue
        if char == "'" and not in_double and not escaped:
            in_single = not in_single
        elif char == '"' and not in_single and not escaped:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            return value[:index].rstrip()
        escaped = False
    return value.rstrip()


def _parse_basic_toml_value(raw_value: str, *, line_number: int) -> Any:
    value = _strip_inline_comment(raw_value).strip()
    if not value:
        raise SourceConfigError(f"Invalid TOML value on line {line_number}")
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if re.fullmatch(r"[+-]?\d+", value):
        return int(value)
    if re.fullmatch(r"[+-]?\d+\.\d+", value):
        return float(value)
    return value


def _parse_basic_toml(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current: dict[str, Any] = result
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            if not section:
                raise SourceConfigError(f"Invalid TOML section header on line {line_number}")
            current = result.setdefault(section, {})
            if not isinstance(current, dict):
                raise SourceConfigError(f"Invalid TOML section reuse on line {line_number}")
            continue
        key, separator, raw_value = line.partition("=")
        if not separator:
            raise SourceConfigError(f"Invalid TOML entry on line {line_number}")
        normalized_key = key.strip()
        if not normalized_key:
            raise SourceConfigError(f"Invalid TOML key on line {line_number}")
        current[normalized_key] = _parse_basic_toml_value(raw_value, line_number=line_number)
    return result


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if _tomllib is not None:
        return _tomllib.loads(text)
    return _parse_basic_toml(text)


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
