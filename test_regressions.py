"""Regression coverage for extraction, URL handling and browser resource ownership."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, quote, urlsplit

import pytest
from bs4 import BeautifulSoup

import server


@pytest.mark.parametrize(
    "fixture",
    json.loads(Path("tests/fixtures/extraction.json").read_text()),
    ids=lambda case: case["name"],
)
def test_shared_extraction(fixture):
    template = {
        "remove": [],
        "sections": [
            {
                "name": "Results",
                "selector": fixture["selector"],
                "multiple": True,
                "children": fixture["children"],
            }
        ],
    }
    result = server.extract_template(BeautifulSoup(fixture["html"], "html.parser"), template)
    items = [{key: value for key, value in item.items() if value} for item in result[0]["items"]]
    assert items == fixture["expected"]


def test_url_resolution():
    assert (
        server.resolve_url_template(
            {"url_template": "https://example.com/{query}/{x}/{x}/{missing}"},
            {"query": "{x}", "x": "$&"},
        )
        == "https://example.com/{x}/$&/$&/"
    )
    _, _, url = server._resolve_search_candidate("google-search", "a", "us-en", True)
    assert parse_qs(urlsplit(url).query) == {
        "udm": ["14"],
        "q": ["a"],
        "hl": ["en"],
        "gl": ["us"],
        "safe": ["active"],
    }
    target = "https://example.com/a%2Fb?q=a%26b"
    assert server.apply_transform("/l/?uddg=" + quote(target, safe=""), "decode_ddg_url") == target
    assert (
        server.apply_transform("../other", "resolve_href", "https://example.com/docs/page")
        == "https://example.com/other"
    )
    assert (
        server._resolve_source_url("{url}.md", "https://example.com/docs/?a=1#part")
        == "https://example.com/docs.md?a=1"
    )


def test_repeated_comments_preserve_fields():
    output = server.compose_sections(
        [
            {
                "type": "search_results",
                "name": "Comments",
                "items": [{"Author": "Alice", "Body": "Actual comment"}],
            }
        ]
    )
    assert "**Alice:**" in output
    assert "Actual comment" in output


def test_disconnected_browser_is_relaunched():
    async def run():
        manager = server.BrowserManager()
        manager.browser = Mock(is_connected=Mock(return_value=False))
        replacement = Mock()
        with patch.object(server, "launch_async", AsyncMock(return_value=replacement)) as launch:
            assert await manager.get_browser() is replacement
            launch.assert_awaited_once()

    asyncio.run(run())


def test_context_closed_when_setup_fails():
    async def run():
        context = Mock(
            new_page=AsyncMock(side_effect=RuntimeError("setup failed")), close=AsyncMock()
        )
        browser = Mock(new_context=AsyncMock(return_value=context))
        with pytest.raises(RuntimeError, match="setup failed"):
            await server._new_fetch_page(browser, None, True)
        context.close.assert_awaited_once()

    asyncio.run(run())


def test_context_closed_on_cancellation():
    async def run():
        context = Mock(close=AsyncMock())
        page = Mock(goto=AsyncMock(side_effect=asyncio.CancelledError))
        with (
            patch.object(server.browser_manager, "get_browser", AsyncMock()),
            patch.object(server, "_new_fetch_page", AsyncMock(return_value=(context, page))),
        ):
            with pytest.raises(asyncio.CancelledError):
                await server.fetch_html("https://example.com", None, True)
        context.close.assert_awaited_once()

    asyncio.run(run())


def test_retry_after_zero_is_honored():
    async def run():
        with (
            patch.object(
                server,
                "_fetch_html_once",
                AsyncMock(
                    side_effect=[server.HttpStatusError(429, "https://example.com", 0), "OK"]
                ),
            ),
            patch.object(server.asyncio, "sleep", AsyncMock()) as sleep,
        ):
            assert await server.fetch_html("https://example.com", None, True) == "OK"
            sleep.assert_awaited_once_with(0)

    asyncio.run(run())


@pytest.mark.parametrize(
    "args",
    [
        {"url": "file:///tmp/a"},
        {"url": "https://example.com", "start_index": -1},
        {"url": "https://example.com", "max_length": 0},
        {"url": "https://example.com", "max_length": 1.5},
    ],
)
def test_invalid_fetch_inputs(args):
    with pytest.raises(ValueError):
        asyncio.run(server.webfetch(**args))


def test_source_markdown_rejects_html_and_preserves_literal_tags():
    async def run():
        context = Mock(close=AsyncMock())
        response = Mock(
            status=200,
            headers={"content-type": "text/html"},
            text=AsyncMock(return_value="# HTML impostor"),
        )
        page = Mock(goto=AsyncMock(return_value=response))
        with (
            patch.object(server.browser_manager, "get_browser", AsyncMock()),
            patch.object(server, "_new_fetch_page", AsyncMock(return_value=(context, page))),
        ):
            assert (
                await server._fetch_source_markdown("https://example.com/a.md", None, True) is None
            )
            response.text.assert_not_awaited()
            response.headers = {"content-type": "text/markdown"}
            response.text.return_value = "# Docs\n\nUse `<Widget>` here."
            assert (
                await server._fetch_source_markdown("https://example.com/a.md", None, True)
                == "# Docs\n\nUse `<Widget>` here."
            )
        assert context.close.await_count == 2

    asyncio.run(run())


def test_http_error_is_checked_before_render_wait():
    async def run():
        context = Mock(close=AsyncMock())
        response = Mock(status=404)
        page = Mock(goto=AsyncMock(return_value=response), wait_for_load_state=AsyncMock())
        with (
            patch.object(server.browser_manager, "get_browser", AsyncMock()),
            patch.object(server, "_new_fetch_page", AsyncMock(return_value=(context, page))),
        ):
            with pytest.raises(server.HttpStatusError, match="404"):
                await server.fetch_html("https://example.com/missing", None, True)
        page.wait_for_load_state.assert_not_awaited()
        context.close.assert_awaited_once()

    asyncio.run(run())


def test_invalid_inline_template_is_rejected_before_fetch():
    with pytest.raises(ValueError, match="sections array"):
        asyncio.run(server.webfetch("https://example.com", template='{"name":"broken"}'))


def test_google_safe_search_can_be_disabled():
    _, _, url = server._resolve_search_candidate("google-search", "test", None, False)
    assert parse_qs(urlsplit(url).query)["safe"] == ["off"]


COMPOSITION_CASES = json.loads(Path("tests/fixtures/composition.json").read_text())
TEMPLATE_CASES = json.loads(Path("tests/fixtures/templates.json").read_text())


@pytest.mark.parametrize("case", COMPOSITION_CASES, ids=lambda case: case["name"])
def test_composition_matches_javascript(case):
    assert server.compose_sections(case["sections"]) == case["expected"]


def test_composition_fixture_covers_all_section_types():
    types = {section["type"] for case in COMPOSITION_CASES for section in case["sections"]}
    assert types == {"value", "multiple", "children", "search_results"}


def test_empty_search_results_fall_back_to_sections():
    sections = [
        {"name": "Title", "type": "value", "value": "Fallback content"},
        {"name": "Results", "type": "search_results", "items": []},
    ]
    assert "Fallback content" in server.compose_search_results(sections)


def test_search_results_without_titles_report_no_content():
    sections = [
        {"name": "Results", "type": "search_results", "items": [{"Title": "", "URL": ""}]},
    ]
    assert server.compose_search_results(sections) == "(No content extracted from this page.)"


def test_template_fixtures_cover_every_builtin_template():
    covered = [case["template"] for case in TEMPLATE_CASES]
    assert len(covered) == len(set(covered))
    assert set(covered) == set(server.BUILTIN_TEMPLATES)


@pytest.mark.parametrize("case", TEMPLATE_CASES, ids=lambda case: case["template"])
def test_every_builtin_template_extracts_fixture(case):
    template = server.BUILTIN_TEMPLATES[case["template"]]
    soup = BeautifulSoup(case["html"], "html.parser")
    output = server.compose_sections(server.extract_template(soup, template))
    for expected in case["expect"]:
        assert expected in output, f"{case['template']}: missing {expected!r} in {output!r}"
    for absent in case["absent"]:
        assert absent not in output, f"{case['template']}: unexpected {absent!r} in {output!r}"
