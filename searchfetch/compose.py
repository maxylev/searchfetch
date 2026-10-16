"""Render extracted sections as Markdown and paginate tool output.

Mirrors ``src/compose.js``; ``tests/fixtures/composition.json`` keeps them in sync.
"""

import urllib.parse
from typing import Any

NO_CONTENT = "(No content extracted from this page.)"

_SECTION_SEPARATOR = "\n\n---\n\n"

Section = dict[str, Any]


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def field(record: dict[str, Any], *keys: str) -> str:
    """Case-insensitive lookup of the first non-empty field among *keys*."""
    for key in keys:
        for name, value in record.items():
            if name.lower() == key.lower() and _text(value):
                return _text(value)
    return ""


def _is_thread(items: list[dict[str, Any]]) -> bool:
    """Records whose first item has an author/user and a comment/body render as a thread."""
    return bool(items) and bool(
        field(items[0], "author", "user") and field(items[0], "comment", "body")
    )


def _render_records(section: Section) -> str:
    name, items = section["name"], section["items"]
    if _is_thread(items):
        comments = []
        for item in items:
            author = field(item, "author", "user")
            comment = field(item, "comment", "body")
            rendered = f"**{author}:**\n\n{comment}" if author else comment
            if rendered:
                comments.append(rendered)
        return f"## {name}\n\n{_SECTION_SEPARATOR.join(comments)}" if comments else ""

    records = []
    for item in items:
        lines = [f"    {key}: {_text(value)}" for key, value in item.items() if _text(value)]
        if lines:
            records.append("\n".join(lines))
    return f"## {name}\n\n" + "\n\n".join(records) if records else ""


def _render_section(section: Section) -> str:
    kind = section["type"]
    if kind == "value":
        value = _text(section.get("value"))
        return f"## {section['name']}\n\n{value}" if value else ""
    if kind == "list":
        items = [_text(item) for item in section["items"] if _text(item)]
        return f"## {section['name']}\n\n" + "\n".join(f"- {i}" for i in items) if items else ""
    if kind == "group":
        return _SECTION_SEPARATOR.join(
            f"## {name}\n\n{_text(value)}"
            for name, value in section["fields"].items()
            if _text(value)
        )
    if kind == "records":
        return _render_records(section)
    raise ValueError(f"Unknown section type '{kind}'.")


def compose_sections(sections: list[Section]) -> str:
    """Render ``webfetch`` sections; empty sections are skipped."""
    parts = [part for part in map(_render_section, sections) if part]
    return _SECTION_SEPARATOR.join(parts) if parts else NO_CONTENT


def _clean_result_url(value: str) -> str:
    try:
        parts = urllib.parse.urlsplit(value)
    except ValueError:
        return ""
    is_google_internal = parts.hostname == "support.google.com" or (
        parts.hostname in ("google.com", "www.google.com") and parts.path == "/search"
    )
    is_web = parts.scheme in ("http", "https") and bool(parts.hostname)
    return value if is_web and not is_google_internal else ""


def compose_search_results(section: Section) -> str:
    """Render a search-results records section as a numbered list.

    Untitled results are skipped; non-web and search-engine-internal URLs are dropped.
    """
    results = []
    for item in section["items"]:
        title = field(item, "title")
        if not title:
            continue
        lines = [f"[{len(results) + 1}] {title}"]
        url = _clean_result_url(field(item, "url"))
        snippet = field(item, "snippet")
        if url:
            lines.append(f"    URL: {url}")
        if snippet:
            lines.append(f"    Snippet: {snippet}")
        results.append("\n".join(lines))
    return f"## {section['name']}\n\n" + "\n\n".join(results) if results else NO_CONTENT


def paginate(text: str, template_name: str, start_index: int, max_length: int) -> str:
    """Return one page of *text* (in Unicode code points) followed by a window footer."""
    start = min(start_index, len(text))
    end = min(start + max_length, len(text))
    more = f" Use start_index={end} to read more." if end < len(text) else ""
    return (
        f"{text[start:end]}\n\n---\n"
        f'[webfetch: template="{template_name}", showing characters {start} to {end} '
        f"of {len(text)} total.{more}]"
    )
