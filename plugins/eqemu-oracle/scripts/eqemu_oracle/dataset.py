from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .constants import BASE_ROOT, CACHE_ROOT, EXTENSIONS_ROOT, LOCAL_EXTENSIONS_ROOT, MERGED_ROOT, OVERLAY_ROOT, SEARCH_DB_PATH
from .extensions import load_domain_extensions, merge_records
from .presentation import add_presentation, add_search_presentation
from .utils import dump_json, ensure_dir, excerpt, load_json, markdown_sections, split_identifier_words


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
    docs_sections: list[dict[str, Any]] = []
    for page in docs_records:
        md_path = docs_root / "pages" / f"{page['slug']}.md"
        ensure_dir(md_path.parent)
        if page.get("markdown") is not None:
            md_path.write_text(page["markdown"], encoding="utf-8")
            markdown = page["markdown"]
        else:
            source_page = base_docs_root / f"{page['slug']}.md"
            if source_page.exists():
                markdown = source_page.read_text(encoding="utf-8")
                md_path.write_text(markdown, encoding="utf-8")
            else:
                markdown = page.get("summary", page["title"])
                md_path.write_text(markdown, encoding="utf-8")
        page["section_count"] = len(markdown_sections(markdown))
        docs_sections.extend(_build_doc_sections(page, markdown))
    dump_json(docs_root / "pages.json", docs_records)
    dump_json(docs_root / "sections.json", docs_sections)

    manifest = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "snapshot_version": time.strftime("%Y%m%d%H%M%S", time.gmtime()),
        "active_root": str(target_root),
        "counts": {
            "quest-api": len(quest_records),
            "schema": len(schema_records),
            "docs": len(docs_records),
            "docs-sections": len(docs_sections),
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
    try:
        conn.execute(
            "CREATE TABLE search_records (domain TEXT, record_id TEXT PRIMARY KEY, title TEXT, body TEXT, uri TEXT, entity_type TEXT, parent_id TEXT)"
        )
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
            conn.execute(
                "INSERT INTO search_records VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("quest-api", record["id"], title, body, uri, entity_type, parent_id),
            )
            if has_fts:
                conn.execute("INSERT INTO search_fts VALUES (?, ?, ?, ?)", (record["id"], title, body, "quest-api"))
        for record in schema_records:
            title, body, uri, entity_type, parent_id = _compose_search_text(record, "schema", data_root)
            conn.execute(
                "INSERT INTO search_records VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("schema", record["id"], title, body, uri, entity_type, parent_id),
            )
            if has_fts:
                conn.execute("INSERT INTO search_fts VALUES (?, ?, ?, ?)", (record["id"], title, body, "schema"))
        for record in docs_records:
            title, body, uri, entity_type, parent_id = _compose_search_text(record, "docs", data_root)
            conn.execute(
                "INSERT INTO search_records VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("docs", record["id"], title, body, uri, entity_type, parent_id),
            )
            if has_fts:
                conn.execute("INSERT INTO search_fts VALUES (?, ?, ?, ?)", (record["id"], title, body, "docs"))
        for record in docs_sections:
            title, body, uri, entity_type, parent_id = _compose_search_text(record, "docs", data_root)
            conn.execute(
                "INSERT INTO search_records VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("docs", record["id"], title, body, uri, entity_type, parent_id),
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

    def get_quest_entry(self, language: str, kind: str, name: str, group_or_type: str | None = None) -> dict[str, Any] | None:
        for item in self.quest_records:
            if (
                item.get("language") == language
                and item.get("kind") == kind
                and item.get("name", "").lower() == name.lower()
                and (not group_or_type or item.get("container", "").lower() == group_or_type.lower())
            ):
                return add_presentation("quest-api", item)
        return None

    def get_table(self, table_name: str) -> dict[str, Any] | None:
        for item in self.schema_records:
            if item.get("table", "").lower() == table_name.lower():
                return add_presentation("schema", item)
        return None

    def get_doc_page(self, path_or_slug: str) -> dict[str, Any] | None:
        needle = path_or_slug.strip("/").lower().split("#", 1)[0]
        for page in self.docs_records:
            if page.get("path", "").lower() == needle or page.get("slug", "").lower() == needle:
                full_page = dict(page)
                full_page["markdown"] = _doc_markdown(self.data_root, page)
                full_page["sections"] = [section for section in self.docs_sections if section.get("page_id") == page["id"]]
                return add_presentation("docs", full_page)
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
            if not SEARCH_DB_PATH.exists():
                build_search_index(self.data_root, SEARCH_DB_PATH)
            conn = sqlite3.connect(SEARCH_DB_PATH)
            rows: list[tuple[str, str, str, str, str, str, str | None, float | None]] = []
            search_limit = max(limit * 25, 250)
            try:
                sql = (
                    "SELECT r.domain, r.record_id, r.title, r.body, r.uri, r.entity_type, r.parent_id, bm25(search_fts) "
                    "FROM search_fts f JOIN search_records r ON f.record_id = r.record_id "
                    f"WHERE search_fts MATCH ? AND r.domain IN ({','.join('?' for _ in domains)}) LIMIT ?"
                )
                rows = conn.execute(sql, [fts_query, *domains, search_limit]).fetchall()
            except sqlite3.OperationalError:
                sql = (
                    f"SELECT domain, record_id, title, body, uri, entity_type, parent_id, NULL FROM search_records "
                    f"WHERE domain IN ({','.join('?' for _ in domains)}) LIMIT ?"
                )
                rows = conn.execute(sql, [*domains, search_limit * 4]).fetchall()
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
                        rows.append((domain, record["id"], title, body, uri, entity_type, parent_id, None))
                if domain == "docs":
                    for section in load_docs_sections(self.base_root):
                        title, body, uri, entity_type, parent_id = _compose_search_text(section, domain, self.base_root)
                        haystack = f"{title} {body}".lower()
                        if _matches_term_groups(haystack, term_groups):
                            rows.append((domain, section["id"], title, body, uri, entity_type, parent_id, None))
        filtered_rows = []
        for row in rows:
            domain, record_id, title, body, uri, entity_type, parent_id, raw_rank = row
            if not _matches_term_groups(f"{title} {body}", term_groups):
                continue
            if domain == "quest-api" and not self._language_matches(record_id, language):
                continue
            filtered_rows.append(row)
        filtered_rows.sort(
            key=lambda row: (
                -_boost_search_hit(query, row[2], row[1], row[5], row[4]),
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
            }
            for domain, record_id, title, body, uri, entity_type, parent_id, _raw_rank in rows
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
