from __future__ import annotations

from typing import Any


def _short_ref(value: str | None) -> str | None:
    if not value:
        return None
    return value[:8]


def _docs_url_from_path(path: str) -> str:
    return f"https://docs.eqemu.dev/{path.strip('/')}/"


def _provenance_text(record: dict[str, Any]) -> str:
    provenance = record.get("provenance", {})
    effective = provenance.get("effective_source")
    source_ref = _short_ref(record.get("source_ref"))
    parts: list[str] = []
    if effective:
        parts.append(f"effective source: `{effective}`")
    if source_ref:
        parts.append(f"ref: `{source_ref}`")
    return ", ".join(parts)


def _copy_block(label: str, content: str, language: str = "text") -> dict[str, str]:
    return {"label": label, "language": language, "content": content}


def present_quest_entry(record: dict[str, Any]) -> dict[str, Any]:
    language = record.get("language", "").title()
    kind = record.get("kind", "entry").title()
    name = record.get("name", "")
    container = record.get("container", "")
    signature = record.get("signature", name)
    params = record.get("params", [])
    related_docs = record.get("related_docs", [])
    copy_blocks = [_copy_block("Signature", signature)]
    lines = [
        f"## {language} {kind}: `{name}`",
        "",
        f"Container: `{container}`",
        "",
        "```text",
        signature,
        "```",
    ]
    if params:
        lines.extend(["", "**Parameters**"])
        lines.extend(f"- `{param}`" for param in params)
    if record.get("return_type"):
        lines.extend(["", f"Returns: `{record['return_type']}`"])
    if record.get("categories"):
        lines.extend(["", "Categories: " + ", ".join(f"`{item}`" for item in record["categories"])])
    if related_docs:
        lines.extend(["", "**Related Docs**"])
        lines.extend(f"- [{path}]({_docs_url_from_path(path)})" for path in related_docs)
    if record.get("source_url"):
        source_line = f"Source: [Quest API Explorer]({record['source_url']})"
        provenance = _provenance_text(record)
        if provenance:
            source_line += f" ({provenance})"
        lines.extend(["", source_line])
    return {
        "template": "quest-api-entry",
        "title": f"{language} {kind}: {name}",
        "markdown": "\n".join(lines),
        "copy_blocks": copy_blocks,
    }


def present_schema_entry(record: dict[str, Any]) -> dict[str, Any]:
    table_name = record.get("table", "")
    columns = record.get("columns", [])
    relationships = record.get("relationships", [])
    column_lines = [f"  {column.get('name', 'column')} {column.get('data_type', 'text')}".rstrip() for column in columns]
    schema_block = f"{table_name} (\n" + ",\n".join(column_lines) + "\n)"
    copy_blocks = [_copy_block("Column Outline", schema_block, "sql")]
    lines = [
        f"## Schema Table: `{table_name}`",
        "",
        f"Category: `{record.get('category', 'unknown')}`",
        "",
        "```sql",
        schema_block,
        "```",
    ]
    if columns:
        lines.extend(["", "**Columns**"])
        lines.extend(
            f"- `{column.get('name', '')}` `{column.get('data_type', '')}`: {column.get('description', '').strip() or 'No description.'}"
            for column in columns
        )
    if relationships:
        lines.extend(["", "**Relationships**"])
        lines.extend(
            f"- `{relationship.get('local_key', '')}` -> `{relationship.get('remote_table', '')}.{relationship.get('remote_key', '')}` ({relationship.get('relationship_type', 'relation')})"
            for relationship in relationships
        )
    if record.get("docs_url"):
        lines.extend(["", f"Docs: [{record['docs_url']}]({record['docs_url']})"])
    if record.get("source_url"):
        source_line = f"Source: [eqemu-docs-v2]({record['source_url']})"
        provenance = _provenance_text(record)
        if provenance:
            source_line += f" ({provenance})"
        lines.extend(["", source_line])
    return {
        "template": "schema-entry",
        "title": f"Schema Table: {table_name}",
        "markdown": "\n".join(lines),
        "copy_blocks": copy_blocks,
    }


def present_doc_page(record: dict[str, Any]) -> dict[str, Any]:
    sections = record.get("sections", [])
    lines = [
        f"## Docs Page: `{record.get('title', record.get('path', 'page'))}`",
        "",
        f"Path: `{record.get('path', '')}`",
    ]
    if record.get("docs_url"):
        lines.append(f"Docs URL: [{record['docs_url']}]({record['docs_url']})")
    if record.get("summary"):
        lines.extend(["", record["summary"]])
    if sections:
        lines.extend(["", "**Key Sections**"])
        lines.extend(f"- `{section.get('heading', section.get('title', 'Section'))}`" for section in sections[:8])
    if record.get("source_url"):
        source_line = f"Source: [eqemu-docs-v2]({record['source_url']})"
        provenance = _provenance_text(record)
        if provenance:
            source_line += f" ({provenance})"
        lines.extend(["", source_line])
    return {
        "template": "docs-page",
        "title": f"Docs Page: {record.get('title', record.get('path', 'page'))}",
        "markdown": "\n".join(lines),
        "copy_blocks": [],
    }


def present_search_results(result: dict[str, Any]) -> dict[str, Any]:
    hits = result.get("hits", [])
    lines = [f"## EQEmu Search Results for `{result.get('query', '')}`", ""]
    if not hits:
        lines.append("No results found.")
    else:
        for index, hit in enumerate(hits, start=1):
            lines.extend(
                [
                    f"{index}. `{hit.get('title', hit.get('id', 'result'))}`",
                    f"   Domain: `{hit.get('domain', '')}` | Type: `{hit.get('entity_type', 'entry')}`",
                    f"   URI: `{hit.get('uri', '')}`",
                    f"   Snippet: {hit.get('snippet', '')}",
                ]
            )
    return {
        "template": "search-results",
        "title": f"Search Results: {result.get('query', '')}",
        "markdown": "\n".join(lines),
        "copy_blocks": [],
    }


def add_presentation(domain: str, payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    enriched = dict(payload)
    if domain == "quest-api":
        enriched["presentation"] = present_quest_entry(enriched)
    elif domain == "schema":
        enriched["presentation"] = present_schema_entry(enriched)
    elif domain == "docs":
        enriched["presentation"] = present_doc_page(enriched)
    return enriched


def add_search_presentation(payload: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(payload)
    enriched["presentation"] = present_search_results(enriched)
    return enriched
