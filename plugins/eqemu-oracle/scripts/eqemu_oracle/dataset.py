from __future__ import annotations

import errno
import itertools
import json
import sqlite3
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import BASE_ROOT, CACHE_ROOT, DOMAIN_CHOICES, EXTENSIONS_ROOT, LOCAL_EXTENSIONS_ROOT, MAINTENANCE_LOCK_ROOT, MERGED_ROOT, OVERLAY_ROOT, SEARCH_DB_PATH
from .examples import ensure_example_indexes, example_index_digest, load_example_records
from .extensions import ExtensionValidationError, extension_inputs_digest, load_domain_extensions, merge_records, merge_source_records
from .presentation import QUEST_TOPIC_STOPWORDS, add_presentation, add_search_presentation, present_quest_entry, present_quest_topic_summary
from .utils import deep_merge, dump_json, dump_text, ensure_dir, excerpt, load_json, markdown_sections, split_identifier_words


SEARCH_SYNONYMS: dict[str, list[str]] = {
    "aa": ["alternate", "advancement"],
    "api": ["method", "event", "constant", "function"],
    "database": ["db", "schema", "mysql", "table"],
    "db": ["database", "schema", "mysql", "table"],
    "docs": ["documentation", "wiki"],
    "documentation": ["docs", "wiki"],
    "mob": ["npc", "spawn"],
    "npc": ["mob", "spawn"],
    "quest": ["script", "scripting"],
    "schema": ["database", "db", "table", "columns"],
    "spawn": ["npc", "mob"],
    "table": ["schema", "database", "columns"],
}

QUEST_EVENT_CONTAINER_PRIORITY = {
    "npc": 0,
    "player": 1,
    "item": 2,
    "bot": 3,
    "merc": 4,
}

QUEST_METHOD_CONTAINER_PRIORITY = {
    "mob": 0,
    "npc": 1,
    "client": 2,
    "entitylist": 3,
    "quest": 4,
    "zone": 5,
    "spell": 6,
    "hateentry": 7,
}


def _stringify_list(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return str(value)


def _current_manifest_path() -> Path | None:
    overlay_manifest = OVERLAY_ROOT / "manifest.json"
    if _overlay_root_is_valid():
        return overlay_manifest
    committed_manifest = BASE_ROOT.parent / "manifest.json"
    if committed_manifest.exists():
        return committed_manifest
    return None


def _overlay_root_is_valid() -> bool:
    required = [
        OVERLAY_ROOT / "manifest.json",
        OVERLAY_ROOT / "base" / "quest-api" / "methods.json",
        OVERLAY_ROOT / "merged" / "quest-api" / "records.json",
        OVERLAY_ROOT / "merged" / "schema" / "index.json",
        OVERLAY_ROOT / "merged" / "docs" / "pages.json",
        OVERLAY_ROOT / "merged" / "quests" / "sources.json",
        OVERLAY_ROOT / "merged" / "plugins" / "sources.json",
    ]
    return all(path.exists() for path in required)


def current_data_root() -> Path:
    if _overlay_root_is_valid():
        return OVERLAY_ROOT / "merged"
    return MERGED_ROOT


def base_data_root() -> Path:
    if _overlay_root_is_valid():
        return OVERLAY_ROOT / "base"
    return BASE_ROOT


def load_quest_base(root: Path) -> list[dict[str, Any]]:
    quest_root = root / "quest-api"
    records: list[dict[str, Any]] = []
    for name in ("methods", "events", "constants"):
        path = quest_root / f"{name}.json"
        if path.exists():
            records.extend(load_json(path))
    return records


def load_schema_base(root: Path) -> list[dict[str, Any]]:
    index_path = root / "schema" / "index.json"
    return load_json(index_path) if index_path.exists() else []


def load_docs_base(root: Path) -> list[dict[str, Any]]:
    pages_path = root / "docs" / "pages.json"
    return load_json(pages_path) if pages_path.exists() else []


def load_source_base(root: Path, domain: str) -> list[dict[str, Any]]:
    sources_path = root / domain / "sources.json"
    return load_json(sources_path) if sources_path.exists() else []


def validate_extension_overlays(
    base_root: Path,
    repo_root: Path = EXTENSIONS_ROOT,
    local_root: Path = LOCAL_EXTENSIONS_ROOT,
) -> None:
    issues: list[str] = []
    domain_loaders = {
        "quest-api": load_quest_base,
        "schema": load_schema_base,
        "docs": load_docs_base,
        "quests": lambda root: load_source_base(root, "quests"),
        "plugins": lambda root: load_source_base(root, "plugins"),
    }
    for domain, loader in domain_loaders.items():
        try:
            repo_extensions = load_domain_extensions(repo_root, domain)
            local_extensions = load_domain_extensions(local_root, domain)
            if domain in {"quests", "plugins"}:
                merge_source_records(repo_extensions, local_extensions, domain=domain)
            else:
                merge_records(loader(base_root), repo_extensions, local_extensions)
        except Exception as exc:
            issues.append(f"{domain}: {exc}")
    if issues:
        raise ExtensionValidationError(issues)


def _normalize_schema_record(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    ignored_keys = {
        "id",
        "title",
        "category",
        "source_migrations",
        "search_aliases",
        "related_docs",
        "source_url",
        "source_ref",
        "docs_url",
        "provenance",
        "extension_flags",
    }
    return {key: value for key, value in record.items() if key not in ignored_keys}


def _schema_extension_merged_against_base(base_record: dict[str, Any], extension: dict[str, Any]) -> dict[str, Any] | None:
    mode = extension.get("mode")
    if mode == "disable":
        return None
    overlay = {
        key: value
        for key, value in extension.items()
        if not key.startswith("_") and key not in {"id", "mode"}
    }
    list_mode = "replace" if mode == "override" else "append_unique"
    return deep_merge(base_record, overlay, list_mode=list_mode)


def find_stale_schema_extensions(
    base_root: Path,
    repo_root: Path = EXTENSIONS_ROOT,
    local_root: Path = LOCAL_EXTENSIONS_ROOT,
) -> list[dict[str, Any]]:
    validate_extension_overlays(base_root, repo_root, local_root)
    base_by_table = {
        record.get("table"): record
        for record in load_schema_base(base_root)
        if isinstance(record, dict) and record.get("table")
    }
    stale_candidates: list[dict[str, Any]] = []
    for source_name, root in (("repo_extension", repo_root), ("local_extension", local_root)):
        for extension in load_domain_extensions(root, "schema"):
            table_name = extension.get("table")
            if not table_name:
                continue
            base_record = base_by_table.get(table_name)
            if base_record is None:
                continue
            merged_record = _schema_extension_merged_against_base(base_record, extension)
            if _normalize_schema_record(merged_record) != _normalize_schema_record(base_record):
                continue
            stale_candidates.append(
                {
                    "id": extension.get("id"),
                    "table": table_name,
                    "mode": extension.get("mode"),
                    "source": source_name,
                    "file": extension.get("_extension_file"),
                    "reason": f"Upstream table '{table_name}' already covers this extension payload.",
                }
            )
    return sorted(stale_candidates, key=lambda item: (str(item.get("file", "")), str(item.get("table", "")), str(item.get("id", ""))))


def _write_extension_json(path: Path, payload: dict[str, Any]) -> None:
    dump_text(path, json.dumps(payload, indent=2) + "\n")


def _resolve_extension_file(file_value: str, source: str, repo_root: Path, local_root: Path) -> Path:
    candidate = Path(file_value)
    if candidate.is_absolute():
        return candidate

    roots: list[Path]
    if source == "repo_extension":
        roots = [repo_root.parent, repo_root]
    elif source == "local_extension":
        roots = [local_root.parent, local_root]
    else:
        roots = [repo_root.parent, repo_root, local_root.parent, local_root]

    for root in roots:
        resolved = root / candidate
        if resolved.exists():
            return resolved
    return roots[0] / candidate


def prune_stale_schema_extensions(
    base_root: Path,
    repo_root: Path = EXTENSIONS_ROOT,
    local_root: Path = LOCAL_EXTENSIONS_ROOT,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    stale_candidates = find_stale_schema_extensions(base_root, repo_root, local_root)
    files: dict[str, list[dict[str, Any]]] = {}
    for candidate in stale_candidates:
        files.setdefault(str(candidate["file"]), []).append(candidate)

    removed_entries: list[dict[str, Any]] = []
    file_results: list[dict[str, Any]] = []

    if apply:
        for file_path, candidates in files.items():
            path = _resolve_extension_file(file_path, str(candidates[0].get("source", "")), repo_root, local_root)
            payload = load_json(path)
            remaining: list[dict[str, Any]] = []
            pending = [(item.get("id"), item.get("table")) for item in candidates]
            removed_count = 0
            for entry in payload.get("tables", []):
                entry_key = (entry.get("id"), entry.get("table"))
                if entry_key in pending:
                    pending.remove(entry_key)
                    removed_count += 1
                    removed_entries.append({"file": file_path, "id": entry.get("id"), "table": entry.get("table")})
                    continue
                remaining.append(entry)
            payload["tables"] = remaining
            _write_extension_json(path, payload)
            file_results.append({"file": file_path, "removed_count": removed_count, "remaining_count": len(remaining)})

    return {
        "apply": apply,
        "stale_candidates": stale_candidates,
        "candidate_count": len(stale_candidates),
        "removed_entries": removed_entries,
        "removed_count": len(removed_entries),
        "files": file_results,
    }


def _load_domain_meta(base_root: Path) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    for domain in DOMAIN_CHOICES:
        meta_path = base_root / domain / "meta.json"
        if meta_path.exists():
            meta[domain] = load_json(meta_path)
    return meta


def _tokens_text(*values: Any) -> str:
    tokens: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, list):
            for item in value:
                tokens.extend(split_identifier_words(str(item)))
            continue
        tokens.extend(split_identifier_words(str(value)))
    return " ".join(dict.fromkeys(tokens))


def _parse_markdown_link(value: str) -> tuple[str, str | None]:
    stripped = value.strip()
    if stripped.startswith("[") and "](" in stripped and stripped.endswith(")"):
        label, _, remainder = stripped[1:].partition("](")
        return label.strip(), remainder[:-1].strip()
    return stripped, None


def _schema_table_id_from_reference(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    label, link = _parse_markdown_link(value)
    if link:
        stem = Path(link).stem
        if stem:
            return stem
    return label.strip().strip("`").lower()


def normalize_schema_relationships(record: dict[str, Any]) -> dict[str, Any]:
    record_copy = dict(record)
    relationships: list[dict[str, Any]] = []
    for relationship in record.get("relationships", []) or []:
        if not isinstance(relationship, dict):
            continue
        relationship_copy = dict(relationship)
        remote_label, remote_path = _parse_markdown_link(str(relationship_copy.get("remote_table", "")))
        relationship_copy.setdefault("remote_table_label", remote_label)
        relationship_copy.setdefault("remote_table_path", remote_path)
        relationship_copy.setdefault("remote_table_id", _schema_table_id_from_reference(str(relationship_copy.get("remote_table", ""))))
        relationships.append(relationship_copy)
    record_copy["relationships"] = relationships
    return record_copy


def _doc_markdown(root: Path, page: dict[str, Any]) -> str:
    markdown_path = root / "docs" / "pages" / f"{page['slug']}.md"
    if markdown_path.exists():
        return markdown_path.read_text(encoding="utf-8")
    return page.get("markdown", "") or page.get("summary", "")


def _build_doc_sections(page: dict[str, Any], markdown: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for section in markdown_sections(markdown):
        section_id = f"{page['id']}#{section['anchor']}"
        sections.append(
            {
                "id": section_id,
                "domain": "docs",
                "entity_type": "section",
                "page_id": page["id"],
                "path": page["path"],
                "slug": page["slug"],
                "page_title": page["title"],
                "title": str(section["title"]),
                "heading": str(section["title"]),
                "level": int(section["level"]),
                "anchor": str(section["anchor"]),
                "summary": str(section["summary"]),
                "content": str(section["content"]),
                "docs_url": f"{page.get('docs_url', page.get('path', ''))}#{section['anchor']}",
                "source_url": page.get("source_url"),
                "source_ref": page.get("source_ref"),
                "fetched_at": page.get("fetched_at"),
                "source_refreshed_at": page.get("source_refreshed_at"),
                "search_aliases": sorted(
                    {
                        *page.get("aliases", []),
                        *page.get("tags", []),
                        str(section["title"]),
                        page["path"],
                    }
                ),
            }
        )
    return sections


def _docs_sections_path(root: Path) -> Path:
    return root / "docs" / "sections.json"


def load_docs_sections(root: Path) -> list[dict[str, Any]]:
    path = _docs_sections_path(root)
    if path.exists():
        return load_json(path)
    pages_path = root / "docs" / "pages.json"
    if not pages_path.exists():
        return []
    pages = load_json(pages_path)
    sections: list[dict[str, Any]] = []
    for page in pages:
        sections.extend(_build_doc_sections(page, _doc_markdown(root, page)))
    return sections


def _compose_search_text(record: dict[str, Any], domain: str, root: Path) -> tuple[str, str, str, str, str | None]:
    if domain == "quest-api":
        title = f"{record.get('language')} {record.get('kind')} {record.get('container')} {record.get('name')}"
        body = " ".join(
            [
                record.get("signature", ""),
                _tokens_text(record.get("name"), record.get("container"), record.get("params")),
                _stringify_list(record.get("categories")),
                _stringify_list(record.get("search_aliases")),
                _stringify_list(record.get("related_docs")),
                json.dumps(record.get("details", {}), sort_keys=True) if record.get("details") else "",
            ]
        )
        uri = f"eqemu://quest-api/{record['id']}"
        return title, body, uri, record.get("kind", "entry"), None
    if domain == "schema":
        title = f"table {record.get('table')}"
        body = " ".join(
            [
                record.get("title", ""),
                record.get("category", ""),
                _tokens_text(record.get("table"), record.get("title")),
                " ".join(column.get("name", "") for column in record.get("columns", [])),
                " ".join(column.get("description", "") for column in record.get("columns", [])),
                " ".join(str(relationship.get("remote_table", "")) for relationship in record.get("relationships", [])),
                " ".join(str(relationship.get("remote_table_id", "")) for relationship in record.get("relationships", [])),
                " ".join(str(relationship.get("remote_table_label", "")) for relationship in record.get("relationships", [])),
                _stringify_list(record.get("search_aliases")),
                _stringify_list(record.get("related_docs")),
            ]
        )
        uri = f"eqemu://schema/table/{record['table']}"
        return title, body, uri, "table", None
    if domain in {"quests", "plugins"}:
        if record.get("entity_type") == "example-file":
            title = record.get("title") or record.get("path") or record.get("id", "")
            body = " ".join(
                [
                    record.get("path", ""),
                    record.get("summary", ""),
                    record.get("content", ""),
                    record.get("language", ""),
                    record.get("source_title", ""),
                    _stringify_list(record.get("tags")),
                ]
            )
            uri = f"eqemu://{domain}/example/{record['id']}"
            return title, body, uri, "example-file", record.get("source_id")
        title = record.get("title") or record.get("name") or record.get("id", "")
        body = " ".join(
            [
                record.get("description", ""),
                record.get("url", ""),
                record.get("path", ""),
                record.get("context_key", ""),
                _stringify_list(record.get("languages")),
                _stringify_list(record.get("tags")),
                _stringify_list(record.get("search_aliases")),
            ]
        )
        uri = f"eqemu://{domain}/source/{record['id']}"
        return title, body, uri, "source", None
    if domain != "docs":
        raise ValueError(f"Unsupported search domain '{domain}'")
    if record.get("entity_type") == "section":
        title = f"{record.get('page_title')} > {record.get('heading')}"
        body = " ".join(
            [
                record.get("content", ""),
                record.get("summary", ""),
                _stringify_list(record.get("search_aliases")),
                _tokens_text(record.get("path"), record.get("heading")),
            ]
        )
        uri = f"eqemu://docs/page/{record['path']}#{record['anchor']}"
        return title, body, uri, "section", record.get("page_id")
    markdown = _doc_markdown(root, record)
    title = record.get("title", record["path"])
    body = " ".join(
        [
            markdown,
            _stringify_list(record.get("headings")),
            _stringify_list(record.get("tags")),
            _stringify_list(record.get("aliases")),
            _stringify_list(record.get("search_aliases")),
            _tokens_text(record.get("path"), record.get("title")),
        ]
    )
    uri = f"eqemu://docs/page/{record['path']}"
    return title, body, uri, "page", record.get("id")


def _merged_domain_is_present(target_root: Path, domain: str) -> bool:
    required = {
        "quest-api": [
            target_root / "quest-api" / "records.json",
            target_root / "quest-api" / "index.json",
        ],
        "schema": [
            target_root / "schema" / "index.json",
        ],
        "docs": [
            target_root / "docs" / "pages.json",
            target_root / "docs" / "sections.json",
        ],
        "quests": [
            target_root / "quests" / "sources.json",
        ],
        "plugins": [
            target_root / "plugins" / "sources.json",
        ],
    }[domain]
    return all(path.exists() for path in required)


def _effective_merge_scope(target_root: Path, scope: str) -> str:
    if scope == "all":
        return scope
    source_domains = {"quests", "plugins"}
    missing_domains = [
        domain
        for domain in DOMAIN_CHOICES
        if domain not in source_domains and domain != scope and not _merged_domain_is_present(target_root, domain)
    ]
    return "all" if missing_domains else scope


def _manifest_merge_scope(target_root: Path, scope: str) -> str:
    if all(_merged_domain_is_present(target_root, domain) for domain in DOMAIN_CHOICES):
        return "all"
    return scope


def _reset_domain_root(path: Path) -> None:
    if path.exists():
        for attempt in range(5):
            try:
                shutil.rmtree(path)
                break
            except OSError as exc:
                if exc.errno not in {errno.ENOTEMPTY, 66} or attempt == 4:
                    raise
                time.sleep(0.1 * (attempt + 1))
    ensure_dir(path)


def _parse_iso8601_timestamp(value: Any) -> int:
    if not isinstance(value, str) or not value.strip():
        return 0
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _record_freshness(record: dict[str, Any]) -> int:
    return max(
        _parse_iso8601_timestamp(record.get("source_refreshed_at")),
        _parse_iso8601_timestamp(record.get("fetched_at")),
    )


def _search_cache_needs_rebuild(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return "freshness_ts" in message


def _search_identity(data_root: Path) -> dict[str, str]:
    manifest_fingerprint = ""
    manifest_path = data_root.parent / "manifest.json"
    if manifest_path.exists():
        manifest = load_json(manifest_path)
        manifest_fingerprint = json.dumps(
            {
                "counts": manifest.get("counts", {}),
                "merge_scope": manifest.get("merge_scope"),
                "sources": manifest.get("sources", {}),
                "extension_health": manifest.get("extension_health", {}),
                "extension_inputs": manifest.get("extension_inputs", {}),
            },
            sort_keys=True,
        )
    identity = {
        "active_root": str(data_root),
        "manifest_fingerprint": manifest_fingerprint,
        "examples_fingerprint": example_index_digest(),
    }
    return identity


def _write_search_identity(conn: sqlite3.Connection, data_root: Path) -> None:
    conn.execute("CREATE TABLE search_meta (key TEXT PRIMARY KEY, value TEXT)")
    for key, value in _search_identity(data_root).items():
        conn.execute("INSERT INTO search_meta VALUES (?, ?)", (key, value))


def _search_cache_matches(data_root: Path, db_path: Path) -> bool:
    if not db_path.exists():
        return False
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT key, value FROM search_meta").fetchall()
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()
    actual = {str(key): str(value) for key, value in rows}
    return actual == _search_identity(data_root)


def _wait_for_maintenance_idle(timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while MAINTENANCE_LOCK_ROOT.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("Timed out waiting for an EQEmu Oracle maintenance operation to finish.")
        time.sleep(0.1)


def write_merged_dataset(base_root: Path, target_root: Path, *, scope: str = "all") -> dict[str, Any]:
    ensure_dir(target_root)
    ensure_dir(CACHE_ROOT)
    validate_extension_overlays(base_root)
    scope = _effective_merge_scope(target_root, scope)
    stale_schema_extensions = find_stale_schema_extensions(base_root)
    if scope in ("all", "quest-api"):
        quest_records = merge_records(
            load_quest_base(base_root),
            load_domain_extensions(EXTENSIONS_ROOT, "quest-api"),
            load_domain_extensions(LOCAL_EXTENSIONS_ROOT, "quest-api"),
        )
        quest_root = target_root / "quest-api"
        _reset_domain_root(quest_root)
        dump_json(quest_root / "records.json", quest_records)
        dump_json(
            quest_root / "index.json",
            {
                "counts": {
                    "methods": sum(1 for item in quest_records if item.get("kind") == "method"),
                    "events": sum(1 for item in quest_records if item.get("kind") == "event"),
                    "constants": sum(1 for item in quest_records if item.get("kind") == "constant"),
                },
                "languages": sorted({item.get("language") for item in quest_records}),
            },
        )
    else:
        quest_records = load_json(target_root / "quest-api" / "records.json")

    if scope in ("all", "schema"):
        schema_records = [
            normalize_schema_relationships(record)
            for record in merge_records(
                load_schema_base(base_root),
                load_domain_extensions(EXTENSIONS_ROOT, "schema"),
                load_domain_extensions(LOCAL_EXTENSIONS_ROOT, "schema"),
            )
        ]
        schema_root = target_root / "schema"
        _reset_domain_root(schema_root)
        ensure_dir(schema_root / "tables")
        for table in schema_records:
            dump_json(schema_root / "tables" / f"{table['table']}.json", table)
        dump_json(schema_root / "index.json", schema_records)
    else:
        schema_records = [normalize_schema_relationships(record) for record in load_json(target_root / "schema" / "index.json")]

    if scope in ("all", "docs"):
        docs_records = merge_records(
            load_docs_base(base_root),
            load_domain_extensions(EXTENSIONS_ROOT, "docs"),
            load_domain_extensions(LOCAL_EXTENSIONS_ROOT, "docs"),
        )
        docs_root = target_root / "docs"
        _reset_domain_root(docs_root)
        ensure_dir(docs_root / "pages")
        base_docs_root = base_root / "docs" / "pages"
        docs_sections: list[dict[str, Any]] = []
        for page in docs_records:
            md_path = docs_root / "pages" / f"{page['slug']}.md"
            ensure_dir(md_path.parent)
            if page.get("markdown") is not None:
                dump_text(md_path, page["markdown"])
                markdown = page["markdown"]
            else:
                source_page = base_docs_root / f"{page['slug']}.md"
                if source_page.exists():
                    markdown = source_page.read_text(encoding="utf-8")
                    dump_text(md_path, markdown)
                else:
                    markdown = page.get("summary", page["title"])
                    dump_text(md_path, markdown)
            page["section_count"] = len(markdown_sections(markdown))
            docs_sections.extend(_build_doc_sections(page, markdown))
        dump_json(docs_root / "pages.json", docs_records)
        dump_json(docs_root / "sections.json", docs_sections)
    else:
        docs_records = load_json(target_root / "docs" / "pages.json")
        docs_sections = load_json(target_root / "docs" / "sections.json")

    source_records_by_domain: dict[str, list[dict[str, Any]]] = {}
    for domain in ("quests", "plugins"):
        if scope in ("all", domain) or not _merged_domain_is_present(target_root, domain):
            source_records = merge_source_records(
                load_domain_extensions(EXTENSIONS_ROOT, domain),
                load_domain_extensions(LOCAL_EXTENSIONS_ROOT, domain),
                domain=domain,
            )
            source_root = target_root / domain
            _reset_domain_root(source_root)
            dump_json(source_root / "sources.json", source_records)
        else:
            source_records = load_json(target_root / domain / "sources.json")
        source_records_by_domain[domain] = source_records

    manifest = {
        "counts": {
            "quest-api": len(quest_records),
            "schema": len(schema_records),
            "docs": len(docs_records),
            "docs-sections": len(docs_sections),
            "quests": len(source_records_by_domain["quests"]),
            "plugins": len(source_records_by_domain["plugins"]),
        },
        "freshness_state": "fresh",
        "merge_scope": _manifest_merge_scope(target_root, scope),
        "sources": _load_domain_meta(base_root),
        "extension_health": {
            "stale_schema_candidate_count": len(stale_schema_extensions),
            "stale_schema_candidates": stale_schema_extensions,
        },
        "extension_inputs": {
            "digest": extension_inputs_digest(EXTENSIONS_ROOT, LOCAL_EXTENSIONS_ROOT),
        },
    }
    dump_json(target_root.parent / "manifest.json", manifest)
    build_search_index(target_root, SEARCH_DB_PATH)
    return manifest


def build_search_index(data_root: Path, db_path: Path) -> None:
    ensure_dir(db_path.parent)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE search_records (domain TEXT, record_id TEXT PRIMARY KEY, title TEXT, body TEXT, uri TEXT, entity_type TEXT, parent_id TEXT, freshness_ts INTEGER)"
        )
        _write_search_identity(conn, data_root)
        try:
            conn.execute("CREATE VIRTUAL TABLE search_fts USING fts5(record_id, title, body, domain, tokenize='porter')")
            has_fts = True
        except sqlite3.OperationalError:
            has_fts = False
        quest_records = load_json(data_root / "quest-api" / "records.json")
        schema_records = load_json(data_root / "schema" / "index.json")
        docs_records = load_json(data_root / "docs" / "pages.json")
        docs_sections = load_docs_sections(data_root)
        source_records_by_domain = {
            domain: load_json(data_root / domain / "sources.json") if (data_root / domain / "sources.json").exists() else []
            for domain in ("quests", "plugins")
        }
        example_records_by_domain = {domain: load_example_records(domain) for domain in ("quests", "plugins")}
        indexed_records = [
            ("quest-api", quest_records),
            ("schema", schema_records),
            ("docs", docs_records),
            ("docs", docs_sections),
            ("quests", source_records_by_domain["quests"]),
            ("quests", example_records_by_domain["quests"]),
            ("plugins", source_records_by_domain["plugins"]),
            ("plugins", example_records_by_domain["plugins"]),
        ]
        for domain, records in indexed_records:
            for record in records:
                title, body, uri, entity_type, parent_id = _compose_search_text(record, domain, data_root)
                freshness_ts = _record_freshness(record)
                conn.execute(
                    "INSERT INTO search_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (domain, record["id"], title, body, uri, entity_type, parent_id, freshness_ts),
                )
                if has_fts:
                    conn.execute("INSERT INTO search_fts VALUES (?, ?, ?, ?)", (record["id"], title, body, domain))
        conn.commit()
    finally:
        conn.close()


def _search_term_groups(query: str) -> list[list[str]]:
    groups: list[list[str]] = []
    for token in split_identifier_words(query):
        variants = [token]
        for synonym in SEARCH_SYNONYMS.get(token, []):
            variants.extend(split_identifier_words(synonym))
        deduped = list(dict.fromkeys(item for item in variants if item))
        if deduped:
            groups.append(deduped)
    return groups


def _build_fts_query(query: str) -> str:
    return " OR ".join(_build_fts_queries(query))


def _build_fts_queries(query: str, *, max_queries: int = 64) -> list[str]:
    groups = _search_term_groups(query)
    if not groups:
        stripped = query.strip()
        return [stripped] if stripped else []
    queries: list[str] = []
    for combination in itertools.product(*groups):
        candidate = " ".join(combination)
        if candidate:
            queries.append(candidate)
        if len(queries) >= max_queries:
            break
    return list(dict.fromkeys(queries))


def _matches_term_groups(haystack: str, groups: list[list[str]]) -> bool:
    if not groups:
        return True
    lowered = haystack.lower()
    return all(any(term in lowered for term in group) for group in groups)


def _boost_search_hit(query: str, title: str, record_id: str, entity_type: str, uri: str) -> int:
    normalized_query = " ".join(split_identifier_words(query))
    normalized_title = " ".join(split_identifier_words(title))
    normalized_id = " ".join(split_identifier_words(record_id))
    normalized_uri = " ".join(split_identifier_words(uri))
    query_terms = set(split_identifier_words(query))
    uri_terms = set(split_identifier_words(uri))
    score = 0
    if normalized_query and normalized_id == normalized_query:
        score += 260
    if entity_type == "table" and normalized_query and normalized_title == f"table {normalized_query}":
        score += 220
    if normalized_query and normalized_title == normalized_query:
        score += 120
    if normalized_query and normalized_title.endswith(normalized_query):
        score += 80
    if normalized_query and normalized_query in normalized_title:
        score += 60
    if normalized_query and normalized_query in normalized_id:
        score += 40
    if normalized_query and normalized_query in normalized_uri:
        score += 50
    score += 12 * len(query_terms & uri_terms)
    if entity_type == "section":
        score += 10
    if "#" in uri:
        score += 5
    if entity_type in {"method", "event"}:
        score += 8
    if entity_type == "constant" and query_terms & {"quest", "script", "scripting"}:
        score -= 20
    if "changelog" in uri_terms and "changelog" not in query_terms and not any(term.isdigit() and len(term) == 4 for term in query_terms):
        score -= 120
    return score


class DataStore:
    def __init__(self) -> None:
        _wait_for_maintenance_idle()
        self.data_root = current_data_root()
        self.base_root = base_data_root()
        self.manifest_path = _current_manifest_path()
        self.quest_records = load_json(self.data_root / "quest-api" / "records.json")
        self.schema_records = [normalize_schema_relationships(record) for record in load_json(self.data_root / "schema" / "index.json")]
        self.docs_records = load_json(self.data_root / "docs" / "pages.json")
        self.docs_sections = load_docs_sections(self.data_root)
        self.quest_source_records = load_json(self.data_root / "quests" / "sources.json") if (self.data_root / "quests" / "sources.json").exists() else []
        self.plugin_source_records = load_json(self.data_root / "plugins" / "sources.json") if (self.data_root / "plugins" / "sources.json").exists() else []
        self.quest_example_records = load_example_records("quests")
        self.plugin_example_records = load_example_records("plugins")
        self.quest_records_by_id = {item.get("id"): item for item in self.quest_records if item.get("id")}
        self.quest_records_by_lookup: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        self.quest_records_by_name: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for item in self.quest_records:
            language = str(item.get("language", "")).lower()
            kind = str(item.get("kind", "")).lower()
            name = str(item.get("name", "")).lower()
            container = str(item.get("container", "")).lower()
            self.quest_records_by_lookup.setdefault((language, kind, name, container), []).append(item)
            self.quest_records_by_name.setdefault((language, kind, name), []).append(item)
        self.schema_records_by_table = {str(item.get("table", "")).lower(): item for item in self.schema_records if item.get("table")}
        self.schema_records_by_id = {item.get("id"): item for item in self.schema_records if item.get("id")}
        self.docs_records_by_id = {item.get("id"): item for item in self.docs_records if item.get("id")}
        self.docs_records_by_key: dict[str, dict[str, Any]] = {}
        for page in self.docs_records:
            for key in (page.get("path"), page.get("slug")):
                if isinstance(key, str) and key:
                    self.docs_records_by_key[key.strip("/").lower()] = page
        self.docs_sections_by_page_id: dict[str, list[dict[str, Any]]] = {}
        for section in self.docs_sections:
            page_id = section.get("page_id")
            if isinstance(page_id, str):
                self.docs_sections_by_page_id.setdefault(page_id, []).append(section)
        self.source_records_by_domain = {
            "quests": {item.get("id"): item for item in self.quest_source_records if item.get("id")},
            "plugins": {item.get("id"): item for item in self.plugin_source_records if item.get("id")},
        }
        self.example_records_by_domain = {
            "quests": {item.get("id"): item for item in self.quest_example_records if item.get("id")},
            "plugins": {item.get("id"): item for item in self.plugin_example_records if item.get("id")},
        }
        self.schema_reverse_relationships_by_table: dict[str, list[dict[str, Any]]] = {}
        for table in self.schema_records:
            local_table = str(table.get("table", ""))
            for relationship in table.get("relationships", []) or []:
                remote_table = str(relationship.get("remote_table_id") or "")
                if not remote_table:
                    continue
                reverse = dict(relationship)
                reverse["source_table"] = local_table
                reverse["source_table_id"] = local_table
                reverse["direction"] = "inbound"
                self.schema_reverse_relationships_by_table.setdefault(remote_table.lower(), []).append(reverse)
        self._base_quest_records_by_id: dict[str, dict[str, Any]] | None = None

    def manifest(self) -> dict[str, Any]:
        if self.manifest_path and self.manifest_path.exists():
            return load_json(self.manifest_path)
        return {}

    def quest_index(self) -> dict[str, Any]:
        return load_json(self.data_root / "quest-api" / "index.json")

    def schema_index(self) -> list[dict[str, Any]]:
        return self.schema_records

    def docs_index(self) -> list[dict[str, Any]]:
        return self.docs_records

    def source_index(self, domain: str) -> list[dict[str, Any]]:
        if domain == "quests":
            return self.quest_source_records
        if domain == "plugins":
            return self.plugin_source_records
        raise ValueError(f"Unsupported source domain '{domain}'")

    def example_index(self, domain: str) -> list[dict[str, Any]]:
        if domain == "quests":
            return self.quest_example_records
        if domain == "plugins":
            return self.plugin_example_records
        raise ValueError(f"Unsupported example domain '{domain}'")

    def get_example_file(self, domain: str, record_id: str) -> dict[str, Any] | None:
        if domain not in self.example_records_by_domain:
            raise ValueError(f"Unsupported example domain '{domain}'")
        return self.example_records_by_domain[domain].get(record_id)

    def _raw_doc_page(self, path_or_slug: str) -> dict[str, Any] | None:
        needle = path_or_slug.strip("/").lower().split("#", 1)[0]
        page = self.docs_records_by_key.get(needle)
        if page is None:
            return None
        full_page = dict(page)
        full_page["markdown"] = _doc_markdown(self.data_root, page)
        full_page["sections"] = self.docs_sections_by_page_id.get(page["id"], [])
        return full_page

    def _present_quest_entry(self, item: dict[str, Any]) -> dict[str, Any]:
        full_item = dict(item)
        docs_page = None
        if full_item.get("related_docs"):
            docs_page = self._raw_doc_page(full_item["related_docs"][0])
        full_item["presentation"] = present_quest_entry(full_item, docs_page)
        return full_item

    def _present_quest_entry_with_overloads(self, item: dict[str, Any], matches: list[dict[str, Any]]) -> dict[str, Any]:
        full_item = self._present_quest_entry(item)
        if len(matches) <= 1:
            return full_item
        full_item["overload_count"] = len(matches)
        full_item["overloads"] = [
            {
                "id": match.get("id"),
                "container": match.get("container"),
                "signature": match.get("signature"),
                "params": match.get("params", []),
                "return_type": match.get("return_type", ""),
            }
            for match in matches
        ]
        return full_item

    def _select_quest_match(
        self,
        matches: list[dict[str, Any]],
        *,
        signature: str | None = None,
        params: list[str] | None = None,
    ) -> dict[str, Any] | None:
        filtered = matches
        if signature:
            normalized_signature = signature.strip().lower()
            filtered = [item for item in filtered if str(item.get("signature", "")).strip().lower() == normalized_signature]
        if params is not None:
            normalized_params = [str(param).strip().lower() for param in params]
            filtered = [
                item
                for item in filtered
                if [str(param).strip().lower() for param in item.get("params", [])] == normalized_params
            ]
        return filtered[0] if filtered else None

    def get_quest_entry(
        self,
        language: str,
        kind: str,
        name: str,
        group_or_type: str | None = None,
        signature: str | None = None,
        params: list[str] | None = None,
    ) -> dict[str, Any] | None:
        if group_or_type:
            matches = self.quest_records_by_lookup.get((language.lower(), kind.lower(), name.lower(), group_or_type.lower()), [])
        else:
            matches = self.quest_records_by_name.get((language.lower(), kind.lower(), name.lower()), [])
        item = self._select_quest_match(matches, signature=signature, params=params)
        return self._present_quest_entry_with_overloads(item, matches) if item else None

    def get_quest_overloads(
        self,
        language: str,
        kind: str,
        name: str,
        group_or_type: str | None = None,
        signature: str | None = None,
        params: list[str] | None = None,
    ) -> dict[str, Any]:
        if group_or_type:
            matches = self.quest_records_by_lookup.get((language.lower(), kind.lower(), name.lower(), group_or_type.lower()), [])
        else:
            matches = self.quest_records_by_name.get((language.lower(), kind.lower(), name.lower()), [])
        selected = self._select_quest_match(matches, signature=signature, params=params)
        result = {
            "language": language.lower(),
            "kind": kind.lower(),
            "name": name,
            "group_or_type": group_or_type,
            "signature": signature,
            "params": params,
            "count": len(matches),
            "is_ambiguous": len(matches) > 1 and signature is None and params is None,
            "selected": self._present_quest_entry(selected) if selected else None,
            "matches": [self._present_quest_entry(match) for match in matches],
        }
        signatures = "\n".join(f"- `{item.get('signature')}` ({item.get('id')})" for item in matches) or "No matching overloads found."
        result["presentation"] = {"markdown": f"## Quest API Overloads\n\n{signatures}", "copy_blocks": []}
        return result

    def get_quest_entry_by_id(self, record_id: str) -> dict[str, Any] | None:
        item = self.quest_records_by_id.get(record_id)
        return self._present_quest_entry(item) if item else None

    def get_table(self, table_name: str) -> dict[str, Any] | None:
        item = self.schema_records_by_table.get(table_name.lower())
        if item is None:
            return None
        enriched = dict(item)
        enriched["reverse_relationships"] = self.schema_reverse_relationships_by_table.get(table_name.lower(), [])
        return add_presentation("schema", enriched)

    def explain_table_relationships(self, table_name: str, depth: int = 1) -> dict[str, Any] | None:
        root_table = self.schema_records_by_table.get(table_name.lower())
        if root_table is None:
            return None
        max_depth = max(1, min(depth, 3))
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        queue: list[tuple[str, int]] = [(str(root_table["table"]), 0)]
        seen: set[tuple[str, int]] = set()
        while queue:
            current, current_depth = queue.pop(0)
            key = current.lower()
            if (key, current_depth) in seen:
                continue
            seen.add((key, current_depth))
            record = self.schema_records_by_table.get(key)
            if record is None:
                continue
            nodes[key] = {"table": record.get("table"), "title": record.get("title"), "docs_url": record.get("docs_url")}
            if current_depth >= max_depth:
                continue
            for relationship in record.get("relationships", []) or []:
                remote = str(relationship.get("remote_table_id") or "")
                if not remote:
                    continue
                edges.append(
                    {
                        "direction": "outbound",
                        "from_table": record.get("table"),
                        "from_key": relationship.get("local_key"),
                        "to_table": remote,
                        "to_key": relationship.get("remote_key"),
                        "relationship_type": relationship.get("relationship_type"),
                    }
                )
                if remote.lower() not in nodes:
                    queue.append((remote, current_depth + 1))
            for relationship in self.schema_reverse_relationships_by_table.get(key, []):
                source_table = str(relationship.get("source_table") or "")
                if not source_table:
                    continue
                edges.append(
                    {
                        "direction": "inbound",
                        "from_table": source_table,
                        "from_key": relationship.get("local_key"),
                        "to_table": record.get("table"),
                        "to_key": relationship.get("remote_key"),
                        "relationship_type": relationship.get("relationship_type"),
                    }
                )
                if source_table.lower() not in nodes:
                    queue.append((source_table, current_depth + 1))
        return {
            "table": root_table.get("table"),
            "depth": max_depth,
            "nodes": list(nodes.values()),
            "edges": edges,
            "presentation": {
                "markdown": f"## `{root_table.get('table')}` Relationships\n\nFound {len(edges)} relationship edge(s) across {len(nodes)} table node(s).",
                "copy_blocks": [],
            },
        }

    def get_doc_page(self, path_or_slug: str) -> dict[str, Any] | None:
        full_page = self._raw_doc_page(path_or_slug)
        return add_presentation("docs", full_page) if full_page else None

    def explain_provenance(self, domain: str, record_id: str) -> dict[str, Any] | None:
        item = None
        if domain == "quest-api":
            item = self.quest_records_by_id.get(record_id)
        elif domain == "schema":
            item = self.schema_records_by_id.get(record_id)
        elif domain == "docs":
            item = self.docs_records_by_id.get(record_id)
        elif domain in self.source_records_by_domain:
            item = self.source_records_by_domain[domain].get(record_id)
        else:
            return None
        if item is not None:
            return {
                "id": item["id"],
                "domain": domain,
                "provenance": item.get("provenance", {}),
                "extension_flags": item.get("extension_flags", {}),
                "source_url": item.get("source_url") or item.get("url"),
                "source_ref": item.get("source_ref"),
            }
        if domain == "docs" and "#" in record_id:
            return self.explain_provenance(domain, record_id.split("#", 1)[0])
        return None

    def _language_matches(self, record_id: str, language: str | None) -> bool:
        if not language:
            return True
        item = self.quest_records_by_id.get(record_id)
        if item is not None:
            return item.get("language") == language
        if self._base_quest_records_by_id is None:
            self._base_quest_records_by_id = {item.get("id"): item for item in load_quest_base(self.base_root) if item.get("id")}
        item = self._base_quest_records_by_id.get(record_id)
        if item is not None:
            return item.get("language") == language
        return False

    def summarize_quest_topic(self, query: str, language: str = "perl", limit: int = 16) -> dict[str, Any]:
        requested_language = language.lower()
        query_tokens = [token for token in split_identifier_words(query) if token not in QUEST_TOPIC_STOPWORDS]
        if not query_tokens:
            query_tokens = split_identifier_words(query)

        scored: list[tuple[int, dict[str, Any]]] = []
        for record in self.quest_records:
            if record.get("language") != requested_language:
                continue
            name_text = " ".join(split_identifier_words(record.get("name", "")))
            container_text = " ".join(split_identifier_words(record.get("container", "")))
            category_text = " ".join(split_identifier_words(" ".join(record.get("categories") or [])))
            alias_text = " ".join(split_identifier_words(" ".join(record.get("search_aliases") or [])))
            docs_text = " ".join(split_identifier_words(" ".join(record.get("related_docs") or [])))
            details_text = " ".join(split_identifier_words(json.dumps(record.get("details", {}), sort_keys=True) if record.get("details") else ""))
            haystack_parts = [
                record.get("name", ""),
                record.get("container", ""),
                record.get("signature", ""),
                " ".join(record.get("categories") or []),
                " ".join(record.get("search_aliases") or []),
                " ".join(record.get("related_docs") or []),
                json.dumps(record.get("details", {}), sort_keys=True) if record.get("details") else "",
            ]
            haystack = " ".join(haystack_parts).lower()
            score = 0
            for token in query_tokens:
                if token in name_text:
                    score += 24
                if token in container_text:
                    score += 8
                if token in category_text:
                    score += 10
                if token in alias_text:
                    score += 8
                if token in docs_text:
                    score += 8
                if token in details_text:
                    score += 10
                if token in haystack:
                    score += 4
                for synonym in SEARCH_SYNONYMS.get(token, []):
                    synonym_token = synonym.lower()
                    if synonym_token in name_text:
                        score += 12
                    elif synonym_token in haystack:
                        score += 4
            if "Hate and Aggro" in (record.get("categories") or []):
                score += 3
            if record.get("kind") == "event":
                score += 4
            if record.get("kind") == "event" and any(token in name_text for token in query_tokens):
                score += 12
            if record.get("kind") == "method" and any(token in name_text for token in query_tokens):
                score += 10
            if score > 0:
                scored.append((score, record))

        scored.sort(key=lambda item: (-item[0], item[1].get("kind", ""), item[1].get("container", ""), item[1].get("name", "")))

        grouped_events: list[dict[str, Any]] = []
        grouped_methods: list[dict[str, Any]] = []
        grouped_constants: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, str, str]] = set()

        for _score, record in scored:
            key = (record.get("kind", ""), record.get("container", ""), record.get("name", ""))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            full_record = self.get_quest_entry(requested_language, record["kind"], record["name"], record.get("container"))
            if not full_record:
                continue
            if record.get("kind") == "event":
                grouped_events.append(full_record)
            elif record.get("kind") == "method":
                grouped_methods.append(full_record)
            elif record.get("kind") == "constant":
                grouped_constants.append(full_record)
            if len(grouped_events) + len(grouped_methods) + len(grouped_constants) >= limit:
                break

        grouped_events.sort(
            key=lambda item: (
                QUEST_EVENT_CONTAINER_PRIORITY.get(str(item.get("container", "")).lower(), 99),
                str(item.get("name", "")),
            )
        )
        grouped_methods.sort(
            key=lambda item: (
                QUEST_METHOD_CONTAINER_PRIORITY.get(str(item.get("container", "")).lower(), 99),
                str(item.get("name", "")),
            )
        )

        result = {
            "query": query,
            "language": requested_language,
            "events": grouped_events[:4],
            "methods": grouped_methods[:10],
            "constants": grouped_constants[:6],
            "manifest": self.manifest(),
        }
        result["presentation"] = present_quest_topic_summary(query, requested_language, result["events"], result["methods"], result["constants"])
        return result

    def search(
        self,
        query: str,
        domains: list[str] | None,
        limit: int = 10,
        include_extensions: bool = True,
        language: str | None = None,
        prefer_fresh: bool = False,
    ) -> dict[str, Any]:
        explicit_domains = domains is not None
        domains = domains or list(DOMAIN_CHOICES)
        term_groups = _search_term_groups(query)
        fts_queries = _build_fts_queries(query)
        fts_query = " OR ".join(fts_queries)
        if include_extensions:
            if explicit_domains:
                examples_changed = False
                if "quests" in domains:
                    examples_changed = ensure_example_indexes("quests", self.quest_source_records) or examples_changed
                    self.quest_example_records = load_example_records("quests")
                    self.example_records_by_domain["quests"] = {item.get("id"): item for item in self.quest_example_records if item.get("id")}
                if "plugins" in domains:
                    examples_changed = ensure_example_indexes("plugins", self.plugin_source_records) or examples_changed
                    self.plugin_example_records = load_example_records("plugins")
                    self.example_records_by_domain["plugins"] = {item.get("id"): item for item in self.plugin_example_records if item.get("id")}
                if examples_changed:
                    build_search_index(self.data_root, SEARCH_DB_PATH)
            if not _search_cache_matches(self.data_root, SEARCH_DB_PATH):
                build_search_index(self.data_root, SEARCH_DB_PATH)
            rows: list[tuple[str, str, str, str, str, str, str | None, float | None, int]] = []
            search_limit = max(limit * 25, 250)
            retried_with_rebuild = False
            while True:
                conn = sqlite3.connect(SEARCH_DB_PATH)
                try:
                    rows_by_id: dict[str, tuple[str, str, str, str, str, str, str | None, float | None, int]] = {}
                    for match_query in fts_queries:
                        sql = (
                            "SELECT r.domain, r.record_id, r.title, r.body, r.uri, r.entity_type, r.parent_id, bm25(search_fts), r.freshness_ts "
                            "FROM search_fts f JOIN search_records r ON f.record_id = r.record_id "
                            f"WHERE search_fts MATCH ? AND r.domain IN ({','.join('?' for _ in domains)}) LIMIT ?"
                        )
                        for row in conn.execute(sql, [match_query, *domains, search_limit]).fetchall():
                            existing = rows_by_id.get(row[1])
                            if existing is None or (row[7] is not None and (existing[7] is None or row[7] < existing[7])):
                                rows_by_id[row[1]] = row
                    rows = list(rows_by_id.values())
                    break
                except sqlite3.OperationalError as exc:
                    if _search_cache_needs_rebuild(exc) and not retried_with_rebuild:
                        retried_with_rebuild = True
                        conn.close()
                        build_search_index(self.data_root, SEARCH_DB_PATH)
                        continue
                    sql = (
                        "SELECT domain, record_id, title, body, uri, entity_type, parent_id, NULL, freshness_ts FROM search_records "
                        f"WHERE domain IN ({','.join('?' for _ in domains)})"
                    )
                    try:
                        rows = conn.execute(sql, [*domains]).fetchall()
                        break
                    except sqlite3.OperationalError as fallback_exc:
                        if _search_cache_needs_rebuild(fallback_exc) and not retried_with_rebuild:
                            retried_with_rebuild = True
                            conn.close()
                            build_search_index(self.data_root, SEARCH_DB_PATH)
                            continue
                        raise
                finally:
                    conn.close()
        else:
            rows = []
            records_by_domain = {
                "quest-api": load_quest_base(self.base_root),
                "schema": load_schema_base(self.base_root),
                "docs": load_docs_base(self.base_root),
                "quests": load_source_base(self.base_root, "quests"),
                "plugins": load_source_base(self.base_root, "plugins"),
            }
            for domain in domains:
                for record in records_by_domain[domain]:
                    title, body, uri, entity_type, parent_id = _compose_search_text(record, domain, self.base_root)
                    haystack = f"{title} {body}".lower()
                    if _matches_term_groups(haystack, term_groups):
                        rows.append((domain, record["id"], title, body, uri, entity_type, parent_id, None, _record_freshness(record)))
                if domain == "docs":
                    for section in load_docs_sections(self.base_root):
                        title, body, uri, entity_type, parent_id = _compose_search_text(section, domain, self.base_root)
                        haystack = f"{title} {body}".lower()
                        if _matches_term_groups(haystack, term_groups):
                            rows.append((domain, section["id"], title, body, uri, entity_type, parent_id, None, _record_freshness(section)))
        filtered_rows = []
        for row in rows:
            domain, record_id, title, body, uri, entity_type, parent_id, raw_rank, freshness_ts = row
            if not _matches_term_groups(f"{title} {body}", term_groups):
                continue
            if domain == "quest-api" and not self._language_matches(record_id, language):
                continue
            filtered_rows.append(row)
        filtered_rows.sort(
            key=lambda row: (
                -_boost_search_hit(query, row[2], row[1], row[5], row[4]),
                -(row[8] if prefer_fresh else 0),
                row[7] if row[7] is not None else 0.0,
                row[2],
            )
        )
        rows = filtered_rows[:limit]
        hits = [
            {
                "domain": domain,
                "id": record_id,
                "title": title,
                "snippet": excerpt(body),
                "uri": uri,
                "entity_type": entity_type,
                "parent_id": parent_id,
                "freshness_ts": freshness_ts,
            }
            for domain, record_id, title, body, uri, entity_type, parent_id, _raw_rank, freshness_ts in rows
        ]
        result = {
            "query": query,
            "expanded_query": fts_query,
            "domains": domains,
            "hits": hits,
            "include_extensions": include_extensions,
            "language": language,
            "prefer_fresh": prefer_fresh,
            "manifest": self.manifest(),
        }
        return add_search_presentation(result)
