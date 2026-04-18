from __future__ import annotations

import json
import errno
import sqlite3
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import BASE_ROOT, CACHE_ROOT, EXTENSIONS_ROOT, LOCAL_EXTENSIONS_ROOT, MAINTENANCE_LOCK_ROOT, MERGED_ROOT, OVERLAY_ROOT, SEARCH_DB_PATH
from .extensions import ExtensionValidationError, load_domain_extensions, merge_records
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
    }
    for domain, loader in domain_loaders.items():
        try:
            merge_records(
                loader(base_root),
                load_domain_extensions(repo_root, domain),
                load_domain_extensions(local_root, domain),
            )
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
    for domain in ("quest-api", "schema", "docs"):
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
                " ".join(relationship.get("remote_table", "") for relationship in record.get("relationships", [])),
                _stringify_list(record.get("search_aliases")),
                _stringify_list(record.get("related_docs")),
            ]
        )
        uri = f"eqemu://schema/table/{record['table']}"
        return title, body, uri, "table", None
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
    }[domain]
    return all(path.exists() for path in required)


def _effective_merge_scope(target_root: Path, scope: str) -> str:
    if scope == "all":
        return scope
    missing_domains = [domain for domain in ("quest-api", "schema", "docs") if domain != scope and not _merged_domain_is_present(target_root, domain)]
    return "all" if missing_domains else scope


def _manifest_merge_scope(target_root: Path, scope: str) -> str:
    if all(_merged_domain_is_present(target_root, domain) for domain in ("quest-api", "schema", "docs")):
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
            },
            sort_keys=True,
        )
    identity = {
        "active_root": str(data_root),
        "manifest_fingerprint": manifest_fingerprint,
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
        schema_records = merge_records(
            load_schema_base(base_root),
            load_domain_extensions(EXTENSIONS_ROOT, "schema"),
            load_domain_extensions(LOCAL_EXTENSIONS_ROOT, "schema"),
        )
        schema_root = target_root / "schema"
        _reset_domain_root(schema_root)
        ensure_dir(schema_root / "tables")
        for table in schema_records:
            dump_json(schema_root / "tables" / f"{table['table']}.json", table)
        dump_json(schema_root / "index.json", schema_records)
    else:
        schema_records = load_json(target_root / "schema" / "index.json")

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

    manifest = {
        "counts": {
            "quest-api": len(quest_records),
            "schema": len(schema_records),
            "docs": len(docs_records),
            "docs-sections": len(docs_sections),
        },
        "freshness_state": "fresh",
        "merge_scope": _manifest_merge_scope(target_root, scope),
        "sources": _load_domain_meta(base_root),
        "extension_health": {
            "stale_schema_candidate_count": len(stale_schema_extensions),
            "stale_schema_candidates": stale_schema_extensions,
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
        for record in quest_records:
            title, body, uri, entity_type, parent_id = _compose_search_text(record, "quest-api", data_root)
            freshness_ts = _record_freshness(record)
            conn.execute(
                "INSERT INTO search_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("quest-api", record["id"], title, body, uri, entity_type, parent_id, freshness_ts),
            )
            if has_fts:
                conn.execute("INSERT INTO search_fts VALUES (?, ?, ?, ?)", (record["id"], title, body, "quest-api"))
        for record in schema_records:
            title, body, uri, entity_type, parent_id = _compose_search_text(record, "schema", data_root)
            freshness_ts = _record_freshness(record)
            conn.execute(
                "INSERT INTO search_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("schema", record["id"], title, body, uri, entity_type, parent_id, freshness_ts),
            )
            if has_fts:
                conn.execute("INSERT INTO search_fts VALUES (?, ?, ?, ?)", (record["id"], title, body, "schema"))
        for record in docs_records:
            title, body, uri, entity_type, parent_id = _compose_search_text(record, "docs", data_root)
            freshness_ts = _record_freshness(record)
            conn.execute(
                "INSERT INTO search_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("docs", record["id"], title, body, uri, entity_type, parent_id, freshness_ts),
            )
            if has_fts:
                conn.execute("INSERT INTO search_fts VALUES (?, ?, ?, ?)", (record["id"], title, body, "docs"))
        for record in docs_sections:
            title, body, uri, entity_type, parent_id = _compose_search_text(record, "docs", data_root)
            freshness_ts = _record_freshness(record)
            conn.execute(
                "INSERT INTO search_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("docs", record["id"], title, body, uri, entity_type, parent_id, freshness_ts),
            )
            if has_fts:
                conn.execute("INSERT INTO search_fts VALUES (?, ?, ?, ?)", (record["id"], title, body, "docs"))
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
    groups = _search_term_groups(query)
    if not groups:
        return query
    return " ".join(f"({' OR '.join(term for term in group)})" for group in groups)


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
        self.schema_records = load_json(self.data_root / "schema" / "index.json")
        self.docs_records = load_json(self.data_root / "docs" / "pages.json")
        self.docs_sections = load_docs_sections(self.data_root)

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

    def _raw_doc_page(self, path_or_slug: str) -> dict[str, Any] | None:
        needle = path_or_slug.strip("/").lower().split("#", 1)[0]
        for page in self.docs_records:
            if page.get("path", "").lower() == needle or page.get("slug", "").lower() == needle:
                full_page = dict(page)
                full_page["markdown"] = _doc_markdown(self.data_root, page)
                full_page["sections"] = [section for section in self.docs_sections if section.get("page_id") == page["id"]]
                return full_page
        return None

    def _present_quest_entry(self, item: dict[str, Any]) -> dict[str, Any]:
        full_item = dict(item)
        docs_page = None
        if full_item.get("related_docs"):
            docs_page = self._raw_doc_page(full_item["related_docs"][0])
        full_item["presentation"] = present_quest_entry(full_item, docs_page)
        return full_item

    def get_quest_entry(self, language: str, kind: str, name: str, group_or_type: str | None = None) -> dict[str, Any] | None:
        for item in self.quest_records:
            if (
                item.get("language") == language
                and item.get("kind") == kind
                and item.get("name", "").lower() == name.lower()
                and (not group_or_type or item.get("container", "").lower() == group_or_type.lower())
            ):
                return self._present_quest_entry(item)
        return None

    def get_quest_entry_by_id(self, record_id: str) -> dict[str, Any] | None:
        for item in self.quest_records:
            if item.get("id") == record_id:
                return self._present_quest_entry(item)
        return None

    def get_table(self, table_name: str) -> dict[str, Any] | None:
        for item in self.schema_records:
            if item.get("table", "").lower() == table_name.lower():
                return add_presentation("schema", item)
        return None

    def get_doc_page(self, path_or_slug: str) -> dict[str, Any] | None:
        full_page = self._raw_doc_page(path_or_slug)
        return add_presentation("docs", full_page) if full_page else None

    def explain_provenance(self, domain: str, record_id: str) -> dict[str, Any] | None:
        collection = {"quest-api": self.quest_records, "schema": self.schema_records, "docs": self.docs_records}.get(domain)
        if collection is None:
            return None
        for item in collection:
            if item.get("id") == record_id:
                return {
                    "id": item["id"],
                    "domain": domain,
                    "provenance": item.get("provenance", {}),
                    "extension_flags": item.get("extension_flags", {}),
                    "source_url": item.get("source_url"),
                    "source_ref": item.get("source_ref"),
                }
        if domain == "docs" and "#" in record_id:
            return self.explain_provenance(domain, record_id.split("#", 1)[0])
        return None

    def _language_matches(self, record_id: str, language: str | None) -> bool:
        if not language:
            return True
        for item in self.quest_records:
            if item.get("id") == record_id:
                return item.get("language") == language
        for item in load_quest_base(self.base_root):
            if item.get("id") == record_id:
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
        domains = domains or ["quest-api", "schema", "docs"]
        term_groups = _search_term_groups(query)
        fts_query = _build_fts_query(query)
        if include_extensions:
            if not _search_cache_matches(self.data_root, SEARCH_DB_PATH):
                build_search_index(self.data_root, SEARCH_DB_PATH)
            rows: list[tuple[str, str, str, str, str, str, str | None, float | None, int]] = []
            search_limit = max(limit * 25, 250)
            retried_with_rebuild = False
            while True:
                conn = sqlite3.connect(SEARCH_DB_PATH)
                try:
                    sql = (
                        "SELECT r.domain, r.record_id, r.title, r.body, r.uri, r.entity_type, r.parent_id, bm25(search_fts), r.freshness_ts "
                        "FROM search_fts f JOIN search_records r ON f.record_id = r.record_id "
                        f"WHERE search_fts MATCH ? AND r.domain IN ({','.join('?' for _ in domains)}) LIMIT ?"
                    )
                    rows = conn.execute(sql, [fts_query, *domains, search_limit]).fetchall()
                    break
                except sqlite3.OperationalError as exc:
                    if _search_cache_needs_rebuild(exc) and not retried_with_rebuild:
                        retried_with_rebuild = True
                        conn.close()
                        build_search_index(self.data_root, SEARCH_DB_PATH)
                        continue
                    sql = (
                        f"SELECT domain, record_id, title, body, uri, entity_type, parent_id, NULL, freshness_ts FROM search_records "
                        f"WHERE domain IN ({','.join('?' for _ in domains)}) LIMIT ?"
                    )
                    try:
                        rows = conn.execute(sql, [*domains, search_limit * 4]).fetchall()
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
