from __future__ import annotations

import json
import shutil
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from .constants import (
    DEFAULT_DOCS_BRANCH,
    DOCS_COMMIT_API,
    DOCS_REPO,
    DOCS_ZIP_URL,
    QUEST_API_REPO,
    QUEST_API_URL,
    SPIRE_COMMIT_API,
)
from .utils import dump_json, ensure_dir, excerpt, heading_title, markdown_headings, markdown_links, short_hash, slugify, split_identifier_words


def fetch_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "eqemu-oracle/0.1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "eqemu-oracle/0.1.0"})
    with urllib.request.urlopen(req, timeout=120) as response:
        return response.read()


def build_quest_api_id(language: str, kind: str, container: str, name: str, signature: str) -> str:
    base = "|".join([language, kind, container, name, signature])
    return f"{language}:{kind}:{slugify(container or 'global')}:{slugify(name)}:{short_hash(base)}"


def build_schema_id(table_name: str) -> str:
    return table_name


def build_doc_id(path_without_suffix: str) -> str:
    return path_without_suffix.replace("\\", "/").strip("/")


def quest_api_related_docs(language: str, kind: str, container: str) -> list[str]:
    container_slug = slugify(container or "global")
    if kind == "method":
        return [f"quest-api/methods/{container_slug}"]
    if kind == "event":
        return [f"quest-api/events/{language}-{container_slug}"]
    if kind == "constant":
        return [f"quest-api/constants/{language}-{container_slug}"]
    return []


def quest_api_search_aliases(language: str, kind: str, container: str, name: str) -> list[str]:
    aliases = {
        language,
        kind,
        container,
        name,
        " ".join(split_identifier_words(name)),
        " ".join(split_identifier_words(container)),
    }
    if kind == "method":
        aliases.update({"function", "call"})
    if kind == "event":
        aliases.update({"callback", "trigger"})
    if kind == "constant":
        aliases.update({"enum", "value"})
    return sorted(alias for alias in aliases if alias)


def normalize_quest_api() -> dict[str, Any]:
    payload = fetch_json(QUEST_API_URL)["data"]
    spire_commit = fetch_json(SPIRE_COMMIT_API)["sha"]
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    methods: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    constants: list[dict[str, Any]] = []
    for language in ("perl", "lua"):
        api = payload[language]
        for container, entries in api["methods"].items():
            for entry in entries:
                signature = f"{entry['method']}({', '.join(entry.get('params', []))})"
                methods.append(
                    {
                        "id": build_quest_api_id(language, "method", container, entry["method"], signature),
                        "domain": "quest-api",
                        "language": language,
                        "kind": "method",
                        "container": container,
                        "name": entry["method"],
                        "params": entry.get("params", []),
                        "signature": signature,
                        "return_type": entry.get("return_type") or "",
                        "categories": entry.get("categories", []),
                        "source_url": QUEST_API_URL,
                        "source_repo": QUEST_API_REPO,
                        "source_ref": spire_commit,
                        "source_refreshed_at": payload.get("last_refreshed"),
                        "fetched_at": now,
                        "related_docs": quest_api_related_docs(language, "method", container),
                        "search_aliases": quest_api_search_aliases(language, "method", container, entry["method"]),
                    }
                )
        for entry in api["events"]:
            event_name = entry.get("event_identifier") or entry.get("event_name") or ""
            container = entry.get("entity_type") or "global"
            signature = f"{event_name}({', '.join(entry.get('args', []))})"
            events.append(
                {
                    "id": build_quest_api_id(language, "event", container, event_name, signature),
                    "domain": "quest-api",
                    "language": language,
                    "kind": "event",
                    "container": container,
                    "name": event_name,
                    "event_identifier": event_name,
                    "params": entry.get("args", []),
                    "signature": signature,
                    "details": entry,
                    "source_url": QUEST_API_URL,
                    "source_repo": QUEST_API_REPO,
                    "source_ref": spire_commit,
                    "source_refreshed_at": payload.get("last_refreshed"),
                    "fetched_at": now,
                    "related_docs": quest_api_related_docs(language, "event", container),
                    "search_aliases": quest_api_search_aliases(language, "event", container, event_name),
                }
            )
        for container, entries in api["constants"].items():
            for entry in entries:
                constant_name = entry.get("constant") or entry.get("name") or ""
                constants.append(
                    {
                        "id": build_quest_api_id(language, "constant", container, constant_name, constant_name),
                        "domain": "quest-api",
                        "language": language,
                        "kind": "constant",
                        "container": container,
                        "name": constant_name,
                        "value": entry.get("value"),
                        "details": entry,
                        "signature": constant_name,
                        "source_url": QUEST_API_URL,
                        "source_repo": QUEST_API_REPO,
                        "source_ref": spire_commit,
                        "source_refreshed_at": payload.get("last_refreshed"),
                        "fetched_at": now,
                        "related_docs": quest_api_related_docs(language, "constant", container),
                        "search_aliases": quest_api_search_aliases(language, "constant", container, constant_name),
                    }
                )
    methods.sort(key=lambda item: (item["language"], item["container"], item["name"], item["signature"]))
    events.sort(key=lambda item: (item["language"], item["container"], item["name"]))
    constants.sort(key=lambda item: (item["language"], item["container"], item["name"]))
    return {
        "meta": {
            "source_url": QUEST_API_URL,
            "source_repo": QUEST_API_REPO,
            "source_ref": spire_commit,
            "last_refreshed": payload.get("last_refreshed"),
            "fetched_at": now,
        },
        "methods": methods,
        "events": events,
        "constants": constants,
    }


def parse_markdown_table(lines: list[str]) -> list[dict[str, str]]:
    if len(lines) < 2:
        return []
    headers = [cell.strip() for cell in lines[0].strip().strip("|").split("|")]
    rows: list[dict[str, str]] = []
    for line in lines[2:]:
        if not line.strip().startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != len(headers):
            continue
        rows.append(dict(zip(headers, cells)))
    return rows


def parse_schema_markdown(category: str, relative_path: str, markdown: str, source_ref: str, fetched_at: str) -> dict[str, Any]:
    title = heading_title(markdown, Path(relative_path).stem)
    lines = markdown.splitlines()
    relationships_lines: list[str] = []
    schema_lines: list[str] = []
    active: str | None = None
    for line in lines:
        stripped = line.strip()
        if stripped == "## Relationships":
            active = "relationships"
            continue
        if stripped == "## Schema":
            active = "schema"
            continue
        if active == "relationships" and stripped.startswith("|"):
            relationships_lines.append(line)
        if active == "schema" and stripped.startswith("|"):
            schema_lines.append(line)
    relationship_rows = parse_markdown_table(relationships_lines)
    schema_rows = parse_markdown_table(schema_lines)
    table_name = Path(relative_path).stem
    columns = [
        {
            "name": row.get("Column", ""),
            "data_type": row.get("Data Type", ""),
            "description": row.get("Description", ""),
        }
        for row in schema_rows
    ]
    relationships = [
        {
            "relationship_type": row.get("Relationship Type", ""),
            "local_key": row.get("Local Key", ""),
            "remote_table": row.get("Relates to Table", ""),
            "remote_key": row.get("Foreign Key", ""),
        }
        for row in relationship_rows
    ]
    path_without_suffix = relative_path[:-3].replace("\\", "/")
    site_path = path_without_suffix.removeprefix("docs/")
    docs_url = "https://docs.eqemu.dev/" + site_path.rstrip("/") + "/"
    table_tokens = split_identifier_words(table_name)
    schema_aliases = {
        category,
        table_name,
        " ".join(table_tokens),
        "mysql",
        "database",
        "schema",
        "table",
        "columns",
    }
    if table_name.startswith("aa_"):
        schema_aliases.update({"aa", "alternate advancement"})
    return {
        "id": build_schema_id(table_name),
        "domain": "schema",
        "table": table_name,
        "title": title,
        "category": category,
        "path": site_path,
        "columns": columns,
        "relationships": relationships,
        "headings": markdown_headings(markdown),
        "docs_url": docs_url,
        "source_url": f"{DOCS_REPO}/blob/{DEFAULT_DOCS_BRANCH}/{relative_path.replace('\\', '/')}",
        "source_repo": DOCS_REPO,
        "source_ref": source_ref,
        "fetched_at": fetched_at,
        "related_docs": [site_path],
        "search_aliases": sorted(alias for alias in schema_aliases if alias),
    }


def parse_doc_markdown(relative_path: str, markdown: str, source_ref: str, fetched_at: str) -> tuple[dict[str, Any], str]:
    path_without_suffix = relative_path[:-3].replace("\\", "/")
    site_path = path_without_suffix.removeprefix("docs/")
    docs_url = "https://docs.eqemu.dev/" + site_path.rstrip("/") + "/"
    page = {
        "id": build_doc_id(site_path),
        "domain": "docs",
        "path": site_path,
        "slug": slugify(site_path.replace("/", "-"), fallback="docs-page"),
        "title": heading_title(markdown, Path(relative_path).stem),
        "headings": markdown_headings(markdown),
        "links": markdown_links(markdown),
        "summary": excerpt(markdown),
        "docs_url": docs_url,
        "source_url": f"{DOCS_REPO}/blob/{DEFAULT_DOCS_BRANCH}/{relative_path.replace('\\', '/')}",
        "source_repo": DOCS_REPO,
        "source_ref": source_ref,
        "fetched_at": fetched_at,
        "aliases": [],
        "tags": [],
        "search_aliases": sorted({site_path, " ".join(split_identifier_words(site_path))}),
    }
    return page, markdown


def normalize_docs_and_schema() -> dict[str, Any]:
    docs_commit = fetch_json(DOCS_COMMIT_API)["sha"]
    fetched_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    archive_bytes = fetch_bytes(DOCS_ZIP_URL)
    docs_pages: list[dict[str, Any]] = []
    docs_markdown: dict[str, str] = {}
    schema_tables: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as temp_dir:
        zip_path = Path(temp_dir) / "docs.zip"
        zip_path.write_bytes(archive_bytes)
        extract_root = Path(temp_dir) / "extract"
        ensure_dir(extract_root)
        with zipfile.ZipFile(zip_path) as zip_file:
            zip_file.extractall(extract_root)
        repo_root = next(extract_root.iterdir())
        docs_root = repo_root / "docs"
        for file_path in docs_root.rglob("*.md"):
            relative_path = file_path.relative_to(repo_root).as_posix()
            markdown = file_path.read_text(encoding="utf-8")
            if relative_path.startswith("docs/schema/"):
                category = Path(relative_path).parent.name
                schema_tables.append(parse_schema_markdown(category, relative_path, markdown, docs_commit, fetched_at))
            else:
                page, content = parse_doc_markdown(relative_path, markdown, docs_commit, fetched_at)
                docs_pages.append(page)
                docs_markdown[page["path"]] = content
    docs_pages.sort(key=lambda item: item["path"])
    schema_tables.sort(key=lambda item: item["table"])
    return {
        "docs": {
            "meta": {
                "source_repo": DOCS_REPO,
                "source_ref": docs_commit,
                "fetched_at": fetched_at,
            },
            "pages": docs_pages,
            "markdown": docs_markdown,
        },
        "schema": {
            "meta": {
                "source_repo": DOCS_REPO,
                "source_ref": docs_commit,
                "fetched_at": fetched_at,
            },
            "tables": schema_tables,
        },
    }


def write_base_dataset(target_root: Path, *, scope: str) -> dict[str, Any]:
    ensure_dir(target_root)
    summary: dict[str, Any] = {}
    docs_schema_payload: dict[str, Any] | None = None
    if scope in ("all", "quest-api"):
        quest_payload = normalize_quest_api()
        quest_root = target_root / "quest-api"
        ensure_dir(quest_root)
        dump_json(quest_root / "methods.json", quest_payload["methods"])
        dump_json(quest_root / "events.json", quest_payload["events"])
        dump_json(quest_root / "constants.json", quest_payload["constants"])
        dump_json(quest_root / "meta.json", quest_payload["meta"])
        summary["quest-api"] = {
            "methods": len(quest_payload["methods"]),
            "events": len(quest_payload["events"]),
            "constants": len(quest_payload["constants"]),
            **quest_payload["meta"],
        }
    if scope in ("all", "schema", "docs"):
        docs_schema_payload = normalize_docs_and_schema()
    if scope in ("all", "schema") and docs_schema_payload is not None:
        schema_root = target_root / "schema"
        ensure_dir(schema_root / "tables")
        for table in docs_schema_payload["schema"]["tables"]:
            dump_json(schema_root / "tables" / f"{table['table']}.json", table)
        dump_json(schema_root / "index.json", docs_schema_payload["schema"]["tables"])
        dump_json(schema_root / "meta.json", docs_schema_payload["schema"]["meta"])
        summary["schema"] = {
            "tables": len(docs_schema_payload["schema"]["tables"]),
            **docs_schema_payload["schema"]["meta"],
        }
    if scope in ("all", "docs") and docs_schema_payload is not None:
        docs_root = target_root / "docs"
        ensure_dir(docs_root / "pages")
        dump_json(docs_root / "pages.json", docs_schema_payload["docs"]["pages"])
        for page in docs_schema_payload["docs"]["pages"]:
            markdown = docs_schema_payload["docs"]["markdown"][page["path"]]
            md_path = docs_root / "pages" / f"{page['slug']}.md"
            ensure_dir(md_path.parent)
            md_path.write_text(markdown, encoding="utf-8")
        dump_json(docs_root / "meta.json", docs_schema_payload["docs"]["meta"])
        summary["docs"] = {
            "pages": len(docs_schema_payload["docs"]["pages"]),
            **docs_schema_payload["docs"]["meta"],
        }
    return summary


def clear_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
