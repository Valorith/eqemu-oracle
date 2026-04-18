from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def dump_json(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def slugify(value: str, fallback: str = "item") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or fallback


def short_hash(value: str, length: int = 10) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:length]


def deep_merge(base: Any, overlay: Any, *, list_mode: str) -> Any:
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = dict(base)
        for key, value in overlay.items():
            if key in merged:
                merged[key] = deep_merge(merged[key], value, list_mode=list_mode)
            else:
                merged[key] = value
        return merged
    if isinstance(base, list) and isinstance(overlay, list):
        if list_mode == "append_unique":
            merged_list = list(base)
            seen = {json.dumps(item, sort_keys=True, default=str) for item in merged_list}
            for item in overlay:
                marker = json.dumps(item, sort_keys=True, default=str)
                if marker not in seen:
                    merged_list.append(item)
                    seen.add(marker)
            return merged_list
        return list(overlay)
    return overlay


def heading_title(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return fallback


def markdown_headings(markdown: str) -> list[str]:
    return [line.strip().lstrip("#").strip() for line in markdown.splitlines() if line.strip().startswith("#")]


def markdown_links(markdown: str) -> list[str]:
    return sorted(set(re.findall(r"\[[^\]]+\]\(([^)]+)\)", markdown)))


def excerpt(text: str, limit: int = 220) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[:limit]
