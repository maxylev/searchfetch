"""MCP tools and server wiring (``searchfetch.server``) against a fake browser."""

import asyncio
import json
import tomllib
from pathlib import Path

import pytest
from conftest import FakeWeb, Reply

from searchfetch import __version__
from searchfetch.browser import HttpStatusError
from searchfetch.compose import NO_CONTENT
from searchfetch.server import PAGE_TEMPLATES, create_server, webfetch, websearch

ROOT = Path(__file__).resolve().parents[2]

DDG_URL = "https://html.duckduckgo.com/html/?q=rust&kl=wt-wt&kp=-1"
LITE_URL = "https://lite.duckduckgo.com/lite/?q=rust&kl=wt-wt&kp=-1"
GOOGLE_URL = "https://www.google.com/search?udm=14&q=rust&hl=en&gl=us"


def ddg_html(count: int) -> str:
    results = "".join(
        f'<div class="result"><h2 class="result__title"><a href="//duckduckgo.com/l/?uddg='
        f'https%3A%2F%2Fexample.com%2F{i}&rut=x">Title {i}</a></h2>'
        f'<a class="result__snippet">Snippet {i}</a></div>'
        for i in range(1, count + 1)
    )
    return f"<html><body>{results}</body></html>"


def lite_html(count: int) -> str:
    links = "".join(
        f'<a class="result-link" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2F{i}">'
        f"Lite {i}</a>"
        for i in range(1, count + 1)
    )
    return f"<html><body>{links}</body></html>"


def search(web: FakeWeb, query="rust", **kwargs):
    return asyncio.run(websearch(web.manager, query, **kwargs))


def fetch(web: FakeWeb, url, **kwargs):
    return asyncio.run(webfetch(web.manager, url, **kwargs))


class TestWebsearch:
    def test_returns_numbered_results_with_decoded_urls(self):
        web = FakeWeb({DDG_URL: Reply(html=ddg_html(2))})
        assert search(web) == (
            "## Results\n\n"
            "[1] Title 1\n    URL: https://example.com/1\n    Snippet: Snippet 1\n\n"
            "[2] Title 2\n    URL: https://example.com/2\n    Snippet: Snippet 2"
        )
        assert web.visited == [DDG_URL]

    def test_query_is_stripped_and_encoded(self):
        web = FakeWeb(
            {
                "https://html.duckduckgo.com/html/?q=a%26b%20c&kl=wt-wt&kp=-1": Reply(
                    html=ddg_html(1)
                )
            }
        )
        assert "[1] Title 1" in search(web, "  a&b c  ")

    def test_max_results_limits_the_results(self):
        web = FakeWeb({DDG_URL: Reply(html=ddg_html(5))})
        output = search(web, max_results=2)
        assert "[2] Title 2" in output
        assert "[3]" not in output

    def test_max_results_can_exceed_the_template_default(self):
        web = FakeWeb({DDG_URL: Reply(html=ddg_html(12))})
        assert "[12] Title 12" in search(web, max_results=12)
        assert "[11]" not in search(FakeWeb({DDG_URL: Reply(html=ddg_html(12))}))

    def test_region_and_safe_search_reach_the_url(self):
        url = "https://html.duckduckgo.com/html/?q=rust&kl=de-de&kp=1"
        web = FakeWeb({url: Reply(html=ddg_html(1))})
        search(web, region="de-de", safe_search=True)
        assert web.visited == [url]

    def test_google_engine(self):
        web = FakeWeb(
            {
                GOOGLE_URL: Reply(
                    html=(
                        '<html><body><div class="g"><a href="/url?q=https://example.com/g&sa=U">'
                        "<h3>G title</h3></a></div></body></html>"
                    )
                )
            }
        )
        output = search(web, engine="google")
        assert web.visited == [GOOGLE_URL]
        assert output == "## Results\n\n[1] G title\n    URL: https://example.com/g"

    def test_falls_back_to_the_lite_endpoint(self):
        web = FakeWeb({DDG_URL: Reply(status=503), LITE_URL: Reply(html=lite_html(2))})
        output = search(web)
        assert output.startswith(
            "[websearch: duckduckgo-search failed: HTTP 503 when fetching "
            f"{DDG_URL}. Results are from duckduckgo-lite-search.]\n\n## Results"
        )
        assert "[1] Lite 1\n    URL: https://example.org/1" in output
        assert web.visited == [DDG_URL, LITE_URL]

    def test_empty_results_trigger_the_fallback(self):
        web = FakeWeb(
            {
                DDG_URL: Reply(html="<html><body><p>nothing</p></body></html>"),
                LITE_URL: Reply(html=lite_html(1)),
            }
        )
        output = search(web)
        assert "duckduckgo-search failed: no results extracted" in output
        assert "Results are from duckduckgo-lite-search.]" in output

    def test_fallback_note_lists_every_failure_in_a_longer_chain(self):
        web = FakeWeb(
            {
                GOOGLE_URL: Reply(status=500),
                DDG_URL: Reply(status=502),
                LITE_URL: Reply(html=lite_html(1)),
            }
        )
        output = search(web, engine="google")
        assert output.startswith("[websearch: google-search failed: HTTP 500")
        assert "; duckduckgo-search failed: HTTP 502" in output
        assert "Results are from duckduckgo-lite-search.]" in output

    def test_all_engines_failing_raises_with_every_reason(self):
        web = FakeWeb({DDG_URL: Reply(status=503), LITE_URL: Reply(status=500)})
        with pytest.raises(RuntimeError) as info:
            search(web)
        message = str(info.value)
        assert message.startswith("duckduckgo-search failed: HTTP 503")
        assert "; duckduckgo-lite-search failed: HTTP 500" in message

    def test_inline_engine_template(self):
        inline = json.dumps(
            {
                "name": "mine",
                "url_template": "https://s.example/?q={query}",
                "url_params": {"query": {"encode": "url"}},
                "sections": [
                    {
                        "name": "Hits",
                        "selector": "li",
                        "multiple": True,
                        "children": [
                            {"name": "Title", "selector": "a"},
                            {
                                "name": "URL",
                                "selector": "a",
                                "format": "attribute",
                                "attribute": "href",
                            },
                        ],
                    }
                ],
            }
        )
        html = "<ul><li><a href='https://x.example/1'>One</a></li></ul>"
        web = FakeWeb({"https://s.example/?q=rust%20lang": Reply(html=html)})
        assert search(web, "rust lang", engine=inline) == (
            "## Hits\n\n[1] One\n    URL: https://x.example/1"
        )

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"query": "   "}, "query must not be empty"),
            ({"max_results": 0}, "max_results"),
            ({"max_results": -1}, "max_results"),
            ({"max_results": True}, "max_results"),
            ({"max_results": 1.5}, "max_results"),
            ({"engine": "bing"}, "Unknown template 'bing'"),
            ({"engine": "raw"}, "not a search template"),
            ({"engine": '{"name": "x"}'}, "Invalid|sections"),
        ],
    )
    def test_invalid_inputs_are_rejected_before_any_fetch(self, kwargs, message):
        web = FakeWeb()
        with pytest.raises(ValueError, match=message):
            search(web, **{"query": "rust", **kwargs})
        web.manager.get.assert_not_awaited()


class TestWebfetch:
    def test_template_extraction_and_footer(self):
        url = "https://www.npmjs.com/package/react"
        html = "<html><body><h1>react</h1></body></html>"
        web = FakeWeb({url: Reply(html=html)})
        output = fetch(web, url)
        assert output.startswith("## Package\n\nreact\n\n---\n")
        assert 'template="npm-package"' in output

    def test_unmatched_urls_use_the_generic_fallback(self):
        url = "https://example.com/blog/post"
        html = (
            "<html><body><header>H</header><nav>menu</nav><h1>Post</h1><p>Body <b>text</b></p>"
            "<img src='x.png'><footer>foot</footer></body></html>"
        )
        output = fetch(FakeWeb({url: Reply(html=html)}), url)
        assert output.startswith("# Post\n\nBody **text**\n\n---\n")
        assert 'template="auto (fallback)"' in output
        for noise in ("menu", "foot", "x.png", "H\n"):
            assert noise not in output.split("\n\n---\n")[0]

    def test_empty_fallback_page(self):
        url = "https://example.com/empty"
        output = fetch(FakeWeb({url: Reply(html="<html><body></body></html>")}), url)
        assert output.startswith(NO_CONTENT)

    def test_explicit_template_overrides_detection(self):
        url = "https://docs.rs/tokio/latest/tokio/"
        html = "<html><body><nav>menu</nav><p>raw text</p></body></html>"
        output = fetch(FakeWeb({url: Reply(html=html)}), url, template="raw")
        assert 'template="raw"' in output
        assert "menu" in output  # raw keeps everything

    def test_inline_template(self):
        url = "https://example.com/x"
        template = json.dumps({"sections": [{"name": "H", "selector": "h1"}]})
        output = fetch(FakeWeb({url: Reply(html="<h1>Hello</h1>")}), url, template=template)
        assert output.startswith("## H\n\nHello\n\n---\n")
        assert 'template="custom"' in output

    def test_required_section_missing_is_an_error(self):
        url = "https://example.com/x"
        template = json.dumps({"sections": [{"name": "H", "selector": "h1", "required": True}]})
        with pytest.raises(ValueError, match="Required section 'H'"):
            fetch(FakeWeb({url: Reply(html="<p>none</p>")}), url, template=template)

    def test_pagination(self):
        url = "https://example.com/long"
        web = FakeWeb({url: Reply(html="<html><body><p>" + "x" * 50 + "</p></body></html>")})
        first = fetch(web, url, max_length=20)
        assert first.startswith("x" * 20 + "\n\n---\n")
        assert "showing characters 0 to 20 of 50 total. Use start_index=20 to read more." in first
        last = fetch(web, url, start_index=40, max_length=20)
        assert last.startswith("x" * 10 + "\n")
        assert last.endswith("showing characters 40 to 50 of 50 total.]")

    def test_http_error_surfaces(self):
        url = "https://example.com/missing"
        with pytest.raises(HttpStatusError, match="HTTP 404 when fetching"):
            fetch(FakeWeb({url: Reply(status=404)}), url)

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            ({"url": "file:///tmp/a"}, "HTTP or HTTPS"),
            ({"url": "https://example.com", "start_index": -1}, "start_index"),
            ({"url": "https://example.com", "start_index": 1.5}, "start_index"),
            ({"url": "https://example.com", "start_index": True}, "start_index"),
            ({"url": "https://example.com", "max_length": 0}, "max_length"),
            ({"url": "https://example.com", "max_length": 1.5}, "max_length"),
            ({"url": "https://example.com", "max_length": True}, "max_length"),
            ({"url": "https://example.com", "template": "nope"}, "Unknown template"),
            ({"url": "https://example.com", "template": '{"name":"broken"}'}, '"sections" list'),
            ({"url": "https://example.com", "template": "{bad"}, "Invalid inline JSON"),
        ],
    )
    def test_invalid_inputs_are_rejected_before_any_fetch(self, args, message):
        web = FakeWeb()
        with pytest.raises(ValueError, match=message):
            fetch(web, **args)
        web.manager.get.assert_not_awaited()


class TestWebfetchSourceMarkdown:
    URL = "https://example.com/docs/guide"
    SOURCE = "https://example.com/docs/guide.md"
    MARKDOWN = "# Guide\n\nUse **this**."

    def md(self, **kwargs):
        return Reply(headers={"content-type": "text/markdown"}, text=self.MARKDOWN, **kwargs)

    def test_source_markdown_is_preferred_over_rendering(self):
        web = FakeWeb({self.SOURCE: self.md(), self.URL: Reply(html="<h1>Rendered</h1>")})
        output = fetch(web, self.URL)
        assert output.startswith(self.MARKDOWN + "\n\n---\n")
        assert 'template="docs-page"' in output
        assert web.visited == [self.SOURCE]

    def test_source_markdown_is_paginated(self):
        web = FakeWeb({self.SOURCE: self.md()})
        output = fetch(web, self.URL, start_index=2, max_length=5)
        assert output.startswith("Guide\n\n---\n")
        assert "showing characters 2 to 7 of" in output

    @pytest.mark.parametrize(
        "source",
        [
            Reply(status=404),
            Reply(headers={"content-type": "text/html"}, text="# not markdown"),
            Reply(headers={"content-type": "text/plain"}, text="plain words"),
        ],
    )
    def test_falls_back_to_rendering_when_the_source_is_unusable(self, source):
        html = "<html><body><main><h1>Rendered</h1><p>Page body</p></main></body></html>"
        web = FakeWeb({self.SOURCE: source, self.URL: Reply(html=html)})
        output = fetch(web, self.URL)
        assert "Rendered" in output
        assert 'template="docs-page"' in output
        assert web.visited == [self.SOURCE, self.URL]

    def test_root_url_source(self):
        web = FakeWeb({"https://a.com/.md": self.md()})
        output = fetch(web, "https://a.com/", template="docs-page")
        assert web.visited == ["https://a.com/.md"]
        assert output.startswith(self.MARKDOWN)


class TestPackage:
    def test_version_matches_project_metadata(self):
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        assert __version__ == pyproject["project"]["version"] == package["version"]

    def test_page_templates_exclude_search_templates(self):
        assert "raw" in PAGE_TEMPLATES
        assert not {"google-search", "duckduckgo-search", "duckduckgo-lite-search"} & set(
            PAGE_TEMPLATES
        )


class TestCreateServer:
    @staticmethod
    def tools(server):
        return {tool.name: tool for tool in asyncio.run(server.list_tools())}

    def test_registers_exactly_the_websearch_and_webfetch_tools(self):
        assert set(self.tools(create_server(FakeWeb().manager))) == {"websearch", "webfetch"}

    def test_tools_are_read_only_and_open_world(self):
        for tool in self.tools(create_server(FakeWeb().manager)).values():
            assert tool.annotations.read_only_hint is True
            assert tool.annotations.open_world_hint is True

    def test_input_schemas(self):
        tools = self.tools(create_server(FakeWeb().manager))
        search_schema, fetch_schema = (
            tools["websearch"].input_schema,
            tools["webfetch"].input_schema,
        )
        assert search_schema["required"] == ["query"]
        assert set(search_schema["properties"]) == {
            "query",
            "engine",
            "region",
            "safe_search",
            "max_results",
            "block_media",
        }
        assert fetch_schema["required"] == ["url"]
        assert set(fetch_schema["properties"]) == {
            "url",
            "template",
            "start_index",
            "max_length",
            "block_media",
        }
        assert search_schema["properties"]["query"]["minLength"] == 1

    def test_tool_call_uses_the_given_manager(self):
        url = "https://example.com/blog"
        web = FakeWeb({url: Reply(html="<html><body><p>Hello tool</p></body></html>")})
        server = create_server(web.manager)
        result = asyncio.run(server.call_tool("webfetch", {"url": url}))
        assert "Hello tool" in str(result)
        web.manager.get.assert_awaited()
