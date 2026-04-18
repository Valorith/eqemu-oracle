from __future__ import annotations

import re
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


QUEST_TOPIC_STOPWORDS = {
    "a",
    "an",
    "api",
    "apis",
    "are",
    "available",
    "for",
    "is",
    "of",
    "option",
    "options",
    "related",
    "the",
    "what",
}


def _extract_section_code_block(markdown: str, heading: str) -> str | None:
    lines = markdown.splitlines()
    in_section = False
    in_code = False
    code_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            if in_section:
                break
            in_section = stripped[3:].strip() == heading
            continue
        if not in_section:
            continue
        if stripped.startswith("```"):
            if in_code:
                return "\n".join(code_lines).rstrip()
            in_code = True
            continue
        if in_code:
            code_lines.append(line)
    return "\n".join(code_lines).rstrip() or None


def _extract_matching_lines_from_code_block(markdown: str, matcher: str) -> str | None:
    lines = markdown.splitlines()
    in_code = False
    matches: list[str] = []
    pattern = re.compile(matcher)
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code and pattern.search(stripped):
            matches.append(stripped)
    return "\n".join(matches) if matches else None


def _generated_perl_event(record: dict[str, Any]) -> str:
    event_name = record.get("name", "EVENT_UNKNOWN")
    entity = record.get("details", {}).get("entity_type") or record.get("container", "Entity")
    event_vars = record.get("details", {}).get("event_vars", []) or []
    lines = [
        f"sub {event_name} {{",
        f"\t# {entity}-{event_name}",
    ]
    if event_vars:
        lines.append("\t# Exported event variables")
        lines.extend(f'\tquest::debug("{item} " . ${item});' for item in event_vars)
    lines.append("}")
    return "\n".join(lines)


def _generated_lua_event(record: dict[str, Any]) -> str:
    event_name = record.get("name", "EVENT_UNKNOWN")
    entity = record.get("details", {}).get("entity_type") or record.get("container", "Entity")
    event_vars = record.get("details", {}).get("event_vars", []) or []
    lines = [
        f"function {event_name}(e)",
        f"\t-- {entity}-{event_name}",
    ]
    if event_vars:
        lines.append("\t-- Exported event variables")
        lines.extend(f'\teq.debug("{item} " .. e.{item});' for item in event_vars)
    lines.append("end")
    return "\n".join(lines)


def _parameter_name(param: str) -> str:
    cleaned = param.replace("const ", "").replace("&", " ").replace("*", " ")
    cleaned = re.sub(r"\b(?:unsigned|signed|struct|class)\b", " ", cleaned)
    pieces = [piece for piece in re.split(r"\s+", cleaned.strip()) if piece]
    if not pieces:
        return "value"
    candidate = pieces[-1]
    candidate = re.sub(r"[^A-Za-z0-9_]", "", candidate)
    return candidate or "value"


def _generated_method_call(record: dict[str, Any]) -> tuple[str, str]:
    language = record.get("language", "perl").lower()
    container = str(record.get("container", "entity"))
    target = container.lower() if container else "entity"
    params = [param for param in (record.get("params") or []) if str(param).strip()]
    if language == "perl":
        receiver = f"${target}"
        args = ", ".join(f"${_parameter_name(param)}" for param in params)
        call = f"{receiver}->{record.get('name', 'Method')}({args});" if args else f"{receiver}->{record.get('name', 'Method')}();"
        return call, "pl"
    receiver = target
    args = ", ".join(_parameter_name(param) for param in params)
    call = f"{receiver}:{record.get('name', 'Method')}({args})" if args else f"{receiver}:{record.get('name', 'Method')}()"
    return call, "lua"


def _quest_code_example(record: dict[str, Any], docs_page: dict[str, Any] | None) -> tuple[str | None, str]:
    language = record.get("language", "text").lower()
    name = record.get("name", "")
    markdown = docs_page.get("markdown", "") if docs_page else ""
    if record.get("kind") == "event":
        generated = _generated_perl_event(record) if language == "perl" else _generated_lua_event(record)
        return generated, "pl" if language == "perl" else "lua"
    if record.get("kind") == "method":
        container = record.get("container", "")
        if language == "perl":
            matcher = rf"\$\w+->\s*{re.escape(name)}\("
        else:
            matcher = rf"\w+:\s*{re.escape(name)}\("
        snippet = _extract_matching_lines_from_code_block(markdown, matcher) if markdown else None
        if snippet:
            return snippet, "pl" if language == "perl" else "lua"
        fallback = record.get("signature", name)
        if language == "perl":
            fallback = f"${container.lower() or 'entity'}->{fallback};"
        else:
            fallback = f"{container.lower() or 'entity'}:{fallback}"
        return fallback, "pl" if language == "perl" else "lua"
    if record.get("kind") == "constant":
        container = record.get("container", "")
        matcher = rf"{re.escape(container)}\.{re.escape(name)}"
        snippet = _extract_matching_lines_from_code_block(markdown, matcher) if markdown else None
        if snippet:
            return snippet, "lua" if language == "lua" else "pl"
        fallback = f"{container}.{name}" if language == "lua" else name
        return fallback, "lua" if language == "lua" else "pl"
    return None, "text"


def present_quest_entry(record: dict[str, Any], docs_page: dict[str, Any] | None = None) -> dict[str, Any]:
    language = record.get("language", "").title()
    kind = record.get("kind", "entry").title()
    name = record.get("name", "")
    container = record.get("container", "")
    signature = record.get("signature", name)
    params = record.get("params", [])
    related_docs = record.get("related_docs", [])
    code_example, code_language = _quest_code_example(record, docs_page)
    copy_blocks = []
    if code_example:
        label = "Quest Example" if record.get("kind") == "event" else "Quest API Snippet"
        copy_blocks.append(_copy_block(label, code_example, code_language))
    else:
        copy_blocks.append(_copy_block("Signature", signature))
    lines = [
        f"## {language} {kind}: `{name}`",
        "",
        f"Container: `{container}`",
    ]
    if code_example:
        lines.extend(["", f"```{code_language}", code_example, "```"])
    else:
        lines.extend(["", "```text", signature, "```"])
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


def present_quest_topic_summary(
    query: str,
    language: str,
    events: list[dict[str, Any]],
    methods: list[dict[str, Any]],
    constants: list[dict[str, Any]],
) -> dict[str, Any]:
    display_language = language.title()
    lines = [f"## {display_language} Quest API Topic: `{query}`", ""]
    copy_blocks: list[dict[str, str]] = []

    if events:
        lines.append("**Event Handlers**")
        for event in events[:3]:
            lines.extend(["", f"`{event['container']}::{event['name']}`", "", f"```{event['presentation']['copy_blocks'][0]['language']}", event["presentation"]["copy_blocks"][0]["content"], "```"])
            copy_blocks.append(_copy_block(f"{event['name']} Handler", event["presentation"]["copy_blocks"][0]["content"], event["presentation"]["copy_blocks"][0]["language"]))

    if methods:
        method_calls: list[str] = []
        method_lang = "pl" if language == "perl" else "lua"
        lines.extend(["", "**Method Calls**"])
        for method in methods[:10]:
            call, method_lang = _generated_method_call(method)
            method_calls.append(call)
        if method_calls:
            method_block = "\n".join(method_calls)
            lines.extend(["", f"```{method_lang}", method_block, "```"])
            copy_blocks.append(_copy_block("Method Calls", method_block, method_lang))
        lines.extend(["", "**Method Reference**"])
        for method in methods[:10]:
            categories = ", ".join(method.get("categories") or [])
            category_suffix = f" [{categories}]" if categories else ""
            lines.append(f"- `{method['container']}::{method['name']}`{category_suffix}")

    if constants:
        constant_lang = "lua" if language == "lua" else "pl"
        constant_lines = []
        for constant in constants[:10]:
            snippet, _ = _quest_code_example(constant, None)
            if snippet:
                constant_lines.append(snippet)
        if constant_lines:
            constant_block = "\n".join(constant_lines)
            lines.extend(["", "**Constants**", "", f"```{constant_lang}", constant_block, "```"])
            copy_blocks.append(_copy_block("Constants", constant_block, constant_lang))

    related_docs = []
    for record in [*events, *methods, *constants]:
        related_docs.extend(record.get("related_docs", []))
    if related_docs:
        unique_docs = list(dict.fromkeys(related_docs))
        lines.extend(["", "**Related Docs**"])
        lines.extend(f"- [{path}]({_docs_url_from_path(path)})" for path in unique_docs[:8])

    return {
        "template": "quest-api-topic-summary",
        "title": f"{display_language} Quest API Topic: {query}",
        "markdown": "\n".join(lines),
        "copy_blocks": copy_blocks,
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
