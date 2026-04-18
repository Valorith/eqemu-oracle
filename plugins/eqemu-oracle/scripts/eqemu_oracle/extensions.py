from __future__ import annotations

from pathlib import Path
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


def load_domain_extensions(root: Path, domain: str) -> list[dict[str, Any]]:
    domain_root = root / domain
    if not domain_root.exists():
        return []
    entries: list[dict[str, Any]] = []
    container_key = {
        "quest-api": "records",
        "schema": "tables",
        "docs": "pages",
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
