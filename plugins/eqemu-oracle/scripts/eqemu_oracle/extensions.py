from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

from .utils import deep_merge, load_json


class ExtensionValidationError(ValueError):
    def __init__(self, issues: list[str]) -> None:
        self.issues = issues
        message = (
            "EQEmu Oracle extension validation failed. "
            "Fix the extension files and rebuild before using extension-backed data.\n- "
            + "\n- ".join(issues)
        )
        super().__init__(message)


def _iter_extension_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        path
        for path in root.rglob("*.json")
        if path.is_file() and path.name != "_example.json" and not path.name.endswith(".example.json")
    )


def _stable_extension_file_label(path: Path, root: Path) -> str:
    try:
        if root.name in {"extensions", "local-extensions"}:
            return path.relative_to(root.parent).as_posix()
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def extension_inputs_fingerprint(*roots: Path) -> tuple[tuple[str, int, int], ...]:
    fingerprint: list[tuple[str, int, int]] = []
    for root in roots:
        for path in _iter_extension_files(root):
            stat = path.stat()
            fingerprint.append((str(path), stat.st_mtime_ns, stat.st_size))
    return tuple(sorted(fingerprint))


def extension_inputs_digest(*roots: Path) -> str:
    inputs: list[dict[str, str | int]] = []
    for root in roots:
        for path in _iter_extension_files(root):
            inputs.append(
                {
                    "path": _stable_extension_file_label(path, root),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "size": path.stat().st_size,
                }
            )
    payload = json.dumps(sorted(inputs, key=lambda item: str(item["path"])), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_domain_extensions(root: Path, domain: str) -> list[dict[str, Any]]:
    domain_root = root / domain
    if not domain_root.exists():
        return []
    entries: list[dict[str, Any]] = []
    container_key = {
        "quest-api": "records",
        "schema": "tables",
        "docs": "pages",
        "quests": "sources",
        "plugins": "sources",
    }[domain]
    for path in _iter_extension_files(domain_root):
        payload = load_json(path)
        items = payload.get(container_key, [])
        if not isinstance(items, list):
            raise ValueError(f"{path} field '{container_key}' must be an array")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"{path} contains a non-object extension entry")
            item_copy = dict(item)
            item_copy["_extension_file"] = _stable_extension_file_label(path, root)
            entries.append(item_copy)
    return entries


def merge_records(
    base_records: list[dict[str, Any]],
    repo_extensions: list[dict[str, Any]],
    local_extensions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for record in base_records:
        record_copy = dict(record)
        record_copy["provenance"] = {
            "effective_source": "base",
            "contributors": [{"source": "base", "id": record_copy["id"]}],
        }
        record_copy["extension_flags"] = {
            "has_repo_extension": False,
            "has_local_extension": False,
        }
        merged[record_copy["id"]] = record_copy

    for source_name, extensions in (("repo_extension", repo_extensions), ("local_extension", local_extensions)):
        for ext in extensions:
            ext_id = ext.get("id")
            if not ext_id:
                raise ValueError(f"Extension in {ext.get('_extension_file')} is missing 'id'")
            mode = ext.get("mode")
            if mode not in (None, "override", "augment", "disable"):
                raise ValueError(f"Extension {ext_id} in {ext.get('_extension_file')} uses unsupported mode '{mode}'")
            base_record = merged.get(ext_id)
            effective_mode = mode
            if effective_mode is None:
                effective_mode = "override" if base_record is not None else "augment"
            if effective_mode == "disable":
                merged.pop(ext_id, None)
                continue
            overlay = {key: value for key, value in ext.items() if not key.startswith("_")}
            if base_record is None:
                new_record = dict(overlay)
                new_record.setdefault("provenance", {"effective_source": source_name, "contributors": []})
                new_record.setdefault("extension_flags", {"has_repo_extension": False, "has_local_extension": False})
                new_record["provenance"]["effective_source"] = source_name
                new_record["provenance"].setdefault("contributors", []).append(
                    {"source": source_name, "file": ext.get("_extension_file"), "mode": effective_mode}
                )
                new_record["extension_flags"]["has_repo_extension" if source_name == "repo_extension" else "has_local_extension"] = True
                merged[ext_id] = new_record
                continue
            list_mode = "replace" if effective_mode == "override" else "append_unique"
            merged_record = deep_merge(base_record, overlay, list_mode=list_mode)
            merged_record.setdefault("provenance", {"effective_source": source_name, "contributors": []})
            merged_record["provenance"]["effective_source"] = source_name
            merged_record["provenance"].setdefault("contributors", []).append(
                {"source": source_name, "file": ext.get("_extension_file"), "mode": effective_mode}
            )
            merged_record.setdefault("extension_flags", {"has_repo_extension": False, "has_local_extension": False})
            merged_record["extension_flags"]["has_repo_extension" if source_name == "repo_extension" else "has_local_extension"] = True
            merged[ext_id] = merged_record
    return sorted(merged.values(), key=lambda item: item["id"])


def _normalized_source_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    return normalized.rstrip("/")


def _github_source_identity(url_value: str) -> str | None:
    parsed = urlparse(url_value)
    if parsed.netloc.lower() != "github.com":
        return None
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        return None
    owner, repo = parts[0].lower(), parts[1].removesuffix(".git").lower()
    if len(parts) >= 5 and parts[2] == "tree":
        subpath = "/".join(parts[4:]).lower()
        return f"github:{owner}/{repo}/{subpath}" if subpath else f"github:{owner}/{repo}"
    return f"github:{owner}/{repo}"


def _source_competition_keys(record: dict[str, Any], domain: str) -> set[str]:
    keys: set[str] = set()
    context_key = record.get("context_key")
    if isinstance(context_key, str) and context_key.strip():
        keys.add(f"context:{domain}:{context_key.strip().lower()}")
    replacements = record.get("replaces", [])
    if not isinstance(replacements, list):
        replacements = []
    for replacement in replacements:
        if isinstance(replacement, str) and replacement.strip():
            keys.add(f"replace:{replacement.strip().lower()}")
            replacement_url = _normalized_source_url(replacement)
            if replacement_url:
                keys.add(f"url:{replacement_url.lower()}")
                github_identity = _github_source_identity(replacement_url)
                if github_identity:
                    keys.add(github_identity)
    record_id = record.get("id")
    if isinstance(record_id, str) and record_id.strip():
        keys.add(f"replace:{record_id.strip().lower()}")
    url_value = _normalized_source_url(record.get("url"))
    if url_value:
        keys.add(f"url:{url_value.lower()}")
        github_identity = _github_source_identity(url_value)
        if github_identity:
            keys.add(github_identity)
    return keys


def merge_source_records(
    repo_extensions: list[dict[str, Any]],
    local_extensions: list[dict[str, Any]],
    *,
    domain: str,
) -> list[dict[str, Any]]:
    merged = merge_records([], repo_extensions, local_extensions)
    local_competition_keys: set[str] = set()
    for extension in local_extensions:
        local_competition_keys.update(_source_competition_keys(extension, domain))
    if not local_competition_keys:
        return merged
    filtered: list[dict[str, Any]] = []
    for record in merged:
        if (
            record.get("provenance", {}).get("effective_source") == "repo_extension"
            and _source_competition_keys(record, domain) & local_competition_keys
        ):
            continue
        filtered.append(record)
    return filtered
