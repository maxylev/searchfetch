"""Template-driven extraction from a parsed HTML document (BeautifulSoup).

Mirrors ``src/extract.js``; shared fixtures in ``tests/fixtures/`` keep them in sync.
"""

import json
import re
import urllib.parse
from collections.abc import Callable, Iterable
from typing import Any

from bs4 import BeautifulSoup, Tag
from soupsieve import SelectorSyntaxError
from soupsieve import match as matches_selector

from searchfetch.markdown import html_to_markdown
from searchfetch.templates import Template

#: Removed before extraction when a template does not declare its own ``remove`` list.
DEFAULT_REMOVE = ["script", "style", "svg", "nav", "footer", "noscript", "iframe", ".advertisement"]

#: Removed before converting a page that matched no template.
FALLBACK_REMOVE = [
    *DEFAULT_REMOVE,
    *("header", "aside", "img", "picture", "video", "audio", "canvas", "map", "area", "dialog"),
]

Section = dict[str, Any]


def split_selectors(value: str = "") -> list[str]:
    """Split a selector list into ordered fallbacks on top-level commas.

    Commas inside CSS functions, attribute brackets, quoted strings, and escapes are preserved.
    """
    selectors = []
    start = depth = 0
    quote = None
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == "," and depth == 0:
            selectors.append(value[start:index].strip())
            start = index + 1
    selectors.append(value[start:].strip())
    return selectors


def clean_document(soup: BeautifulSoup, remove: Iterable[str] = DEFAULT_REMOVE) -> None:
    """Remove noise elements, inline styles, and embedded images from *soup* in place."""
    for selector in remove:
        try:
            matches = soup.select(selector)
        except SelectorSyntaxError:
            continue  # Selector support differs between runtimes; skip unsupported cleanup rules.
        for tag in matches:
            tag.decompose()
    for tag in soup.select("[style]"):
        del tag["style"]
    for tag in soup.select("[src^='data:']"):
        del tag["src"]


def _select_in_document(soup: BeautifulSoup, selector_list: str) -> list[Tag]:
    """First fallback that matches anywhere; an empty fallback selects ``<body>``."""
    for selector in split_selectors(selector_list):
        if not selector:
            return [soup.body or soup]
        matches = soup.select(selector)
        if matches:
            return matches
    return []


def select_in_scope(scope: Tag, selector_list: str) -> list[Tag]:
    """First fallback that matches inside *scope* or on *scope*/its ancestors.

    Never searches sibling subtrees, so a record cannot borrow a field from its neighbor. An
    empty fallback selects *scope* itself.
    """
    for selector in split_selectors(selector_list):
        if not selector:
            return [scope]
        descendants = scope.select(selector)
        if descendants:
            return descendants
        node = scope
        while isinstance(node, Tag) and not isinstance(node, BeautifulSoup):
            if matches_selector(selector, node):
                return [node]
            node = node.parent
    return []


def _decode_google_url(value: str, _page_url: str) -> str:
    if not value.startswith("/url?"):
        return value
    return urllib.parse.parse_qs(value.removeprefix("/url?")).get("q", [value])[0]


def _decode_ddg_url(value: str, _page_url: str) -> str:
    if "/l/?" not in value:
        return value
    return urllib.parse.parse_qs(value.split("?", 1)[1]).get("uddg", [value])[0]


def _json_parse(value: str, _page_url: str) -> str:
    try:
        return json.dumps(json.loads(value), indent=2, ensure_ascii=False)
    except json.JSONDecodeError:
        return value


def _resolve_href(value: str, page_url: str) -> str:
    return urllib.parse.urljoin(page_url, value) if page_url else value


_TRANSFORM_FUNCTIONS: dict[str, Callable[[str, str], str]] = {
    "strip": lambda value, _page_url: value.strip(),
    "decode_google_url": _decode_google_url,
    "decode_ddg_url": _decode_ddg_url,
    "json_parse": _json_parse,
    "resolve_href": _resolve_href,
}


def apply_transforms(value: str, transform: str | list[str] | None, page_url: str = "") -> str:
    names = [] if transform is None else [transform] if isinstance(transform, str) else transform
    for name in names:
        if not value:
            break
        value = _TRANSFORM_FUNCTIONS[name](value, page_url)
    return value


def extract_value(element: Tag, section: Section, page_url: str = "") -> str:
    """Extract one field value from *element*."""
    fmt = section.get("format", "text")
    if fmt == "markdown":
        value = html_to_markdown(element.decode_contents())
    elif fmt == "attribute":
        raw = element.get(section["attribute"])
        value = " ".join(raw) if isinstance(raw, list) else (raw or "")
    elif fmt == "html":
        value = element.decode_contents().strip()
    else:
        value = re.sub(r"\s+", " ", element.get_text()).strip()
    return apply_transforms(value, section.get("transform"), page_url)


def _extract_fields(scope: Tag, section: Section, page_url: str) -> dict[str, str]:
    """Extract child fields from one record element; empty fields are omitted."""
    fields = {}
    for child in section["children"]:
        matches = select_in_scope(scope, child.get("selector") or "")
        if not matches:
            if child.get("required"):
                raise ValueError(
                    f"Required field '{child['name']}' not found in section '{section['name']}'."
                )
            continue
        value = extract_value(matches[0], child, page_url)
        if value:
            fields[child["name"]] = value
    return fields


def extract_section(
    soup: BeautifulSoup, section: Section, page_url: str = "", limit: int | None = None
) -> Section:
    """Extract one top-level section. Result shapes:

    - ``{name, type: "value", value}``: single element, no children
    - ``{name, type: "list", items: [str]}``: ``multiple``, no children
    - ``{name, type: "group", fields: {}}``: single element with children
    - ``{name, type: "records", items: [{}]}``: ``multiple`` with children
    """
    name = section["name"]
    has_children = bool(section.get("children"))
    matches = _select_in_document(soup, section.get("selector") or "")
    if not matches and section.get("required"):
        raise ValueError(f"Required section '{name}' not found on page.")

    if not section.get("multiple"):
        if has_children:
            fields = _extract_fields(matches[0], section, page_url) if matches else {}
            return {"name": name, "type": "group", "fields": fields}
        value = extract_value(matches[0], section, page_url) if matches else ""
        return {"name": name, "type": "value", "value": value}

    elements = matches[: limit or section.get("max_items")]
    if has_children:
        records = (_extract_fields(element, section, page_url) for element in elements)
        return {"name": name, "type": "records", "items": [r for r in records if r]}
    values = (extract_value(element, section, page_url).strip() for element in elements)
    return {"name": name, "type": "list", "items": [v for v in values if v]}


def extract_template(
    soup: BeautifulSoup, template: Template, page_url: str = "", max_results: int | None = None
) -> list[Section]:
    """Clean *soup* and extract every template section.

    *max_results* overrides ``max_items`` of the first records section (the search results of a
    search template).
    """
    clean_document(soup, template.get("remove", DEFAULT_REMOVE))
    limit_pending = max_results is not None
    results = []
    for section in template["sections"]:
        limit = None
        if limit_pending and section.get("multiple") and section.get("children"):
            limit, limit_pending = max_results, False
        results.append(extract_section(soup, section, page_url, limit))
    return results
