"""HTML-to-Markdown conversion and detection of raw Markdown sources."""

import re
import urllib.parse

from markdownify import markdownify

_BLANK_LINES = re.compile(r"\n{3,}")
_HTML_TAG = re.compile(r"<\w+[^>]*>")
_MARKDOWN_SIGNALS = [
    re.compile(pattern, re.MULTILINE)
    for pattern in (
        r"^#{1,6}\s+\S",
        r"\[.+?\]\(.+?\)",
        r"```\w*\n",
        r"^\s*[-*+]\s+\S",
        r"\*\*[^*]+\*\*",
        r"^>\s+\S",
    )
]


def collapse_blank_lines(text: str) -> str:
    return _BLANK_LINES.sub("\n\n", text).strip()


def html_to_markdown(html: str) -> str:
    return collapse_blank_lines(
        markdownify(html, heading_style="ATX", bullets="-", strong_em_symbol="*")
    )


def is_markdown(text: str) -> bool:
    """Heuristic: text with Markdown syntax and at most three HTML tags."""
    if not text or len(_HTML_TAG.findall(text)) > 3:
        return False
    return any(pattern.search(text) for pattern in _MARKDOWN_SIGNALS)


def clean_source_markdown(text: str) -> str:
    """Drop build-tool artifacts (e.g. Twoslash caches) and redundant blank lines."""
    return collapse_blank_lines(re.sub(r"^@twoslash-cache:.*$", "", text, flags=re.MULTILINE))


def resolve_source_url(source_template: str, url: str) -> str:
    """Resolve a template ``source_url``.

    ``{url}.md`` appends ``.md`` to the path (keeping the query and dropping the fragment); any
    other ``{url}`` placeholder is replaced verbatim.
    """
    if source_template == "{url}.md":
        parts = urllib.parse.urlsplit(url)
        path = parts.path.rstrip("/").removeprefix("/")
        return parts._replace(path=f"/{path}.md", fragment="").geturl()
    return source_template.replace("{url}", url)
