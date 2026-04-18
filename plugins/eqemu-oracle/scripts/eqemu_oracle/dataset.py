from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .constants import BASE_ROOT, CACHE_ROOT, EXTENSIONS_ROOT, LOCAL_EXTENSIONS_ROOT, MERGED_ROOT, OVERLAY_ROOT, SEARCH_DB_PATH
from .extensions import load_domain_extensions, merge_records
from .utils import dump_json, ensure_dir, excerpt, load_json


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


def _load_domain_meta(base_root: Path) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    for domain in ("quest-api", "schema", "docs"):
        meta_path = base_root / domain / "meta.json"
        if meta_path.exists():
            meta[domain] = load_json(meta_path)
    return meta


def _compose_search_text(record: dict[str, Any], domain: str, root: Path) -> tuple[str, str, str]:
    if domain == "quest-api":
        title = f"{record.get('language')} {record.get('kind')} {record.get('container')} {record.get('name')}"
        body = " ".join(
            [
                record.get("signature", ""),
                _stringify_list(record.get("categories")),
                json.dumps(record.get("details", {}), sort_keys=True) if record.get("details") else "",
            ]
        )
        uri = f"eqemu://quest-api/{record['id']}"
        return title, body, uri
    if domain == "schema":
        title = f"table {record.get('table')}"
        body = " ".join(
            [
                record.get("title", ""),
                " ".join(column.get("name", "") for column in record.get("columns", [])),
                " ".join(column.get("description", "") for column in record.get("columns", [])),
            ]
        )
        uri = f"eqemu://schema/table/{record['table']}"
        return title, body, uri
    page_path = root / "docs" / "pages" / f"{record['slug']}.md"
    markdown = page_path.read_text(encoding="utf-8") if page_path.exists() else record.get("summary", "")
    title = record.get("title", record["path"])
    body = " ".join([markdown, _stringify_list(record.get("tags")), _stringify_list(record.get("aliases"))])
    uri = f"eqemu://docs/page/{record['path']}"
    return title, body, uri


def write_merged_dataset(base_root: Path, target_root: Path) -> dict[str, Any]:
    ensure_dir(target_root)
    ensure_dir(CACHE_ROOT)
    quest_records = merge_records(
        load_quest_base(base_root),
        load_domain_extensions(EXTENSIONS_ROOT, "quest-api"),
        load_domain_extensions(LOCAL_EXTENSIONS_ROOT, "quest-api"),
    )
    schema_records = merge_records(
        load_schema_base(base_root),
        load_domain_extensions(EXTENSIONS_ROOT, "schema"),
        load_domain_extensions(LOCAL_EXTENSIONS_ROOT, "schema"),
    )
    docs_records = merge_records(
        load_docs_base(base_root),
        load_domain_extensions(EXTENSIONS_ROOT, "docs"),
        load_domain_extensions(LOCAL_EXTENSIONS_ROOT, "docs"),
    )

    quest_root = target_root / "quest-api"
    ensure_dir(quest_root)
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

    schema_root = target_root / "schema"
    ensure_dir(schema_root / "tables")
    for table in schema_records:
        dump_json(schema_root / "tables" / f"{table['table']}.json", table)
    dump_json(schema_root / "index.json", schema_records)

    docs_root = target_root / "docs"
    ensure_dir(docs_root / "pages")
    base_docs_root = base_root / "docs" / "pages"
    for page in docs_records:
        md_path = docs_root / "pages" / f"{page['slug']}.md"
        ensure_dir(md_path.parent)
        if page.get("markdown") is not None:
            md_path.write_text(page["markdown"], encoding="utf-8")
            continue
        source_page = base_docs_root / f"{page['slug']}.md"
        if source_page.exists():
            md_path.write_text(source_page.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            md_path.write_text(page.get("summary", page["title"]), encoding="utf-8")
    dump_json(docs_root / "pages.json", docs_records)

    manifest = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "snapshot_version": time.strftime("%Y%m%d%H%M%S", time.gmtime()),
        "active_root": str(target_root),
        "counts": {
            "quest-api": len(quest_records),
            "schema": len(schema_records),
            "docs": len(docs_records),
        },
        "freshness_state": "fresh",
        "sources": _load_domain_meta(base_root),
        "extensions": {
            "repo_root": str(EXTENSIONS_ROOT),
            "local_root": str(LOCAL_EXTENSIONS_ROOT),
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
    conn.execute("CREATE TABLE search_records (domain TEXT, record_id TEXT PRIMARY KEY, title TEXT, body TEXT, uri TEXT)")
    try:
        conn.execute("CREATE VIRTUAL TABLE search_fts USING fts5(record_id, title, body, domain, tokenize='porter')")
        has_fts = True
    except sqlite3.OperationalError:
        has_fts = False
    quest_records = load_json(data_root / "quest-api" / "records.json")
    schema_records = load_json(data_root / "schema" / "index.json")
    docs_records = load_json(data_root / "docs" / "pages.json")
    for record in quest_records:
        title, body, uri = _compose_search_text(record, "quest-api", data_root)
        conn.execute("INSERT INTO search_records VALUES (?, ?, ?, ?, ?)", ("quest-api", record["id"], title, body, uri))
        if has_fts:
            conn.execute("INSERT INTO search_fts VALUES (?, ?, ?, ?)", (record["id"], title, body, "quest-api"))
    for record in schema_records:
        title, body, uri = _compose_search_text(record, "schema", data_root)
        conn.execute("INSERT INTO search_records VALUES (?, ?, ?, ?, ?)", ("schema", record["id"], title, body, uri))
        if has_fts:
            conn.execute("INSERT INTO search_fts VALUES (?, ?, ?, ?)", (record["id"], title, body, "schema"))
    for record in docs_records:
        title, body, uri = _compose_search_text(record, "docs", data_root)
        conn.execute("INSERT INTO search_records VALUES (?, ?, ?, ?, ?)", ("docs", record["id"], title, body, uri))
        if has_fts:
            conn.execute("INSERT INTO search_fts VALUES (?, ?, ?, ?)", (record["id"], title, body, "docs"))
    conn.commit()
    conn.close()


class DataStore:
    def __init__(self) -> None:
        self.data_root = current_data_root()
        self.base_root = base_data_root()
        self.manifest_path = _current_manifest_path()
        self.quest_records = load_json(self.data_root / "quest-api" / "records.json")
        self.schema_records = load_json(self.data_root / "schema" / "index.json")
        self.docs_records = load_json(self.data_root / "docs" / "pages.json")

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

    def get_quest_entry(self, language: str, kind: str, name: str, group_or_type: str | None = None) -> dict[str, Any] | None:
        for item in self.quest_records:
            if (
                item.get("language") == language
                and item.get("kind") == kind
                and item.get("name", "").lower() == name.lower()
                and (not group_or_type or item.get("container", "").lower() == group_or_type.lower())
            ):
                return item
        return None

    def get_table(self, table_name: str) -> dict[str, Any] | None:
        for item in self.schema_records:
            if item.get("table", "").lower() == table_name.lower():
                return item
        return None

    def get_doc_page(self, path_or_slug: str) -> dict[str, Any] | None:
        needle = path_or_slug.strip("/").lower()
        for page in self.docs_records:
            if page.get("path", "").lower() == needle or page.get("slug", "").lower() == needle:
                markdown_path = self.data_root / "docs" / "pages" / f"{page['slug']}.md"
                full_page = dict(page)
                full_page["markdown"] = markdown_path.read_text(encoding="utf-8") if markdown_path.exists() else ""
                return full_page
        return None

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
        if include_extensions:
            if not SEARCH_DB_PATH.exists():
                build_search_index(self.data_root, SEARCH_DB_PATH)
            conn = sqlite3.connect(SEARCH_DB_PATH)
            rows: list[tuple[str, str, str, str, str]] = []
            search_limit = max(limit * 10, 50)
            try:
                sql = (
                    "SELECT r.domain, r.record_id, r.title, r.body, r.uri "
                    "FROM search_fts f JOIN search_records r ON f.record_id = r.record_id "
                    f"WHERE search_fts MATCH ? AND r.domain IN ({','.join('?' for _ in domains)}) LIMIT ?"
                )
                rows = conn.execute(sql, [query, *domains, search_limit]).fetchall()
            except sqlite3.OperationalError:
                like = f"%{query}%"
                sql = (
                    f"SELECT domain, record_id, title, body, uri FROM search_records "
                    f"WHERE domain IN ({','.join('?' for _ in domains)}) AND (title LIKE ? OR body LIKE ?) LIMIT ?"
                )
                rows = conn.execute(sql, [*domains, like, like, search_limit]).fetchall()
            finally:
                conn.close()
        else:
            rows = []
            query_terms = [term for term in query.lower().split() if term]
            records_by_domain = {
                "quest-api": load_quest_base(self.base_root),
                "schema": load_schema_base(self.base_root),
                "docs": load_docs_base(self.base_root),
            }
            for domain in domains:
                for record in records_by_domain[domain]:
                    title, body, uri = _compose_search_text(record, domain, self.base_root)
                    haystack = f"{title} {body}".lower()
                    if all(term in haystack for term in query_terms):
                        rows.append((domain, record["id"], title, body, uri))
        filtered_rows = []
        for row in rows:
            domain, record_id, _title, _body, _uri = row
            if domain == "quest-api" and not self._language_matches(record_id, language):
                continue
            filtered_rows.append(row)
        rows = filtered_rows[:limit]
        hits = [
            {
                "domain": domain,
                "id": record_id,
                "title": title,
                "snippet": excerpt(body),
                "uri": uri,
            }
            for domain, record_id, title, body, uri in rows
        ]
        return {
            "query": query,
            "domains": domains,
            "hits": hits,
            "include_extensions": include_extensions,
            "language": language,
            "prefer_fresh": prefer_fresh,
            "manifest": self.manifest(),
        }
