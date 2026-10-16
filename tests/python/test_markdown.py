"""Markdown conversion and raw-source handling (``searchfetch.markdown``)."""

import pytest

from searchfetch.markdown import (
    clean_source_markdown,
    collapse_blank_lines,
    html_to_markdown,
    is_markdown,
    resolve_source_url,
)


class TestIsMarkdown:
    @pytest.mark.parametrize(
        "text",
        [
            "# Hello World",
            "See [docs](https://example.com) for more.",
            "```python\nprint('hello')\n```",
            "- item 1\n- item 2",
            "This is **bold** text.",
            "> This is a quote",
            "# Hello\n\nThis is **bold** and [a link](https://example.com).",
            "# Docs\n\nUse `<Widget>` here.",
            "# Few tags\n<b>a</b><i>b</i>",
        ],
    )
    def test_detects_markdown(self, text):
        assert is_markdown(text)

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "Just some plain text without any markdown syntax.",
            "<html><body><h1>Hello</h1></body></html>",
            "# Looks like markdown<p>a</p><p>b</p><p>c</p><p>d</p>",
        ],
    )
    def test_rejects_non_markdown(self, text):
        assert not is_markdown(text)


class TestCleanSourceMarkdown:
    def test_strips_twoslash_cache_lines(self):
        text = "# Title\n\nSome text\n@twoslash-cache: abcdef1234567890abcdef1234567890\nMore text"
        result = clean_source_markdown(text)
        assert "@twoslash-cache:" not in result
        assert result == "# Title\n\nSome text\n\nMore text"

    def test_strips_multiple_twoslash_lines(self):
        text = (
            "# Title\n@twoslash-cache: h1\ncontent\n"
            "@twoslash-cache: h2\n@twoslash-cache: h3\nfooter\n"
        )
        assert clean_source_markdown(text) == "# Title\n\ncontent\n\nfooter"

    def test_only_line_starts_are_stripped(self):
        text = "# T\n\ninline @twoslash-cache: x stays"
        assert clean_source_markdown(text) == text

    def test_unchanged_without_artifacts(self):
        assert (
            clean_source_markdown("# Title\n\nNormal content here.")
            == "# Title\n\nNormal content here."
        )

    def test_collapses_excess_blank_lines(self):
        assert clean_source_markdown("# Title\n\n\n\n\n\nContent\n\n\n\n\n\nFooter") == (
            "# Title\n\nContent\n\nFooter"
        )

    def test_collapse_blank_lines_trims(self):
        assert collapse_blank_lines("\n\na\n\n\n\nb\n\n") == "a\n\nb"


class TestHtmlToMarkdown:
    def test_headings_lists_emphasis_and_links(self):
        html = (
            "<h2>Head</h2><p>Some <strong>bold</strong> and <em>it</em> "
            "<a href='https://e.com'>link</a></p><ul><li>a</li><li>b</li></ul>"
        )
        assert html_to_markdown(html) == (
            "## Head\n\nSome **bold** and *it* [link](https://e.com)\n\n- a\n- b"
        )

    def test_code_blocks(self):
        assert "docker run postgres" in html_to_markdown(
            "<pre><code>docker run postgres</code></pre>"
        )

    def test_empty(self):
        assert html_to_markdown("") == ""


class TestResolveSourceUrl:
    @pytest.mark.parametrize(
        ("source", "url", "expected"),
        [
            (
                "{url}.md",
                "https://solana.com/docs/rpc/http/getslot",
                "https://solana.com/docs/rpc/http/getslot.md",
            ),
            ("{url}.md", "https://example.com/docs/api/", "https://example.com/docs/api.md"),
            ("{url}.md", "https://example.com/docs/?a=1#part", "https://example.com/docs.md?a=1"),
            ("{url}.md", "https://a.com/", "https://a.com/.md"),
            ("{url}.md", "https://a.com", "https://a.com/.md"),
            ("{url}.md", "https://a.com/x#frag", "https://a.com/x.md"),
            (
                "https://raw.example/?u={url}",
                "https://a.com/x",
                "https://raw.example/?u=https://a.com/x",
            ),
        ],
    )
    def test_resolution(self, source, url, expected):
        assert resolve_source_url(source, url) == expected
