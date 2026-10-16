"""Browser lifecycle, fetching and retry rules (``searchfetch.browser``) without a real browser."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from bs4 import BeautifulSoup
from conftest import FakeWeb, Reply
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from searchfetch import browser
from searchfetch.browser import (
    BrowserManager,
    HttpStatusError,
    fetch_html,
    fetch_source_markdown,
    is_access_denied,
    parse_retry_after,
    retry_delay,
)

URL = "https://example.com/page"


class TestBrowserManager:
    @pytest.fixture
    def launch(self, monkeypatch):
        mock = AsyncMock(
            side_effect=lambda **_: Mock(is_connected=Mock(return_value=True), close=AsyncMock())
        )
        monkeypatch.setattr(browser, "launch_async", mock)
        return mock

    def test_launches_once_and_reuses_a_connected_browser(self, launch):
        async def run():
            manager = BrowserManager()
            first = await manager.get()
            assert await manager.get() is first
            launch.assert_awaited_once()
            assert launch.await_args.kwargs["headless"] is True

        asyncio.run(run())

    def test_disconnected_browser_is_relaunched(self, launch):
        async def run():
            manager = BrowserManager()
            first = await manager.get()
            first.is_connected.return_value = False
            second = await manager.get()
            assert second is not first
            assert launch.await_count == 2

        asyncio.run(run())

    def test_concurrent_gets_launch_one_browser(self, launch):
        async def run():
            manager = BrowserManager()
            browsers = await asyncio.gather(*(manager.get() for _ in range(5)))
            assert len({id(b) for b in browsers}) == 1
            launch.assert_awaited_once()

        asyncio.run(run())

    def test_close_closes_the_browser_and_allows_relaunch(self, launch):
        async def run():
            manager = BrowserManager()
            first = await manager.get()
            await manager.close()
            first.close.assert_awaited_once()
            await manager.close()  # idempotent
            first.close.assert_awaited_once()
            assert await manager.get() is not first

        asyncio.run(run())

    def test_close_without_a_browser(self, launch):
        asyncio.run(BrowserManager().close())
        launch.assert_not_awaited()


class TestParseRetryAfter:
    NOW = datetime(2015, 10, 21, 7, 27, 50, tzinfo=UTC)

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, 2.0),
            ("", 2.0),
            ("5", 5.0),
            ("1.5", 1.5),
            ("0", 0.0),
            ("-3", 0.0),
            ("999", 30.0),
            ("nan", 2.0),
            ("inf", 2.0),
            ("soon", 2.0),
            ("Wed, 21 Oct 2015 07:28:00 GMT", 10.0),
            ("Wed, 21 Oct 2015 07:27:00 GMT", 0.0),
            ("Wed, 21 Oct 2015 09:00:00 GMT", 30.0),
            ("Wed, 99 Foo 2015 07:28:00 GMT", 2.0),
        ],
    )
    def test_parse(self, value, expected):
        assert parse_retry_after(value, self.NOW) == expected

    def test_http_date_defaults_to_the_current_time(self):
        assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0


def page(body: str, title: str = "") -> BeautifulSoup:
    head = f"<head><title>{title}</title></head>" if title else ""
    return BeautifulSoup(f"<html>{head}<body>{body}</body></html>", "html.parser")


class TestIsAccessDenied:
    @pytest.mark.parametrize(
        "title",
        [
            "Access Denied",
            "Are you a human?",
            "Attention Required! Are you a robot",
            "DDoS protection by Cloudflare",
            "403 Forbidden",
            "One more step",
            "Security Check",
            "Sorry, you have been blocked",
            "Unusual traffic from your computer network",
            "Verify you are human",
        ],
    )
    def test_denied_titles(self, title):
        assert is_access_denied(page("<p>content</p>", title))

    @pytest.mark.parametrize(
        "body",
        [
            "Bots use DuckDuckGo too. Please complete the challenge.",
            "Our systems have detected unusual traffic from your computer network.",
            "Sorry, you have been blocked",
            "To continue, please type the characters below",
            "Access denied",
            "Please verify you are human",
            "Are you a robot?",
        ],
    )
    def test_denied_bodies(self, body):
        assert is_access_denied(page(f"<p>{body}</p>"))

    def test_detects_long_google_unusual_traffic_page(self):
        body = (
            "ordinary text " * 200
            + "Our systems have detected unusual traffic from your computer network."
        )
        assert is_access_denied(page(body))

    def test_short_phrases_are_ignored_on_long_pages(self):
        article = "A long article about web security. " * 60 + " Access denied is a common error."
        assert len(article) > 1200
        assert not is_access_denied(page(f"<p>{article}</p>", "Web security guide"))

    @pytest.mark.parametrize(
        "doc",
        [page("<h1>Hello</h1><p>Regular page.</p>", "Hello"), BeautifulSoup("", "html.parser")],
    )
    def test_normal_pages(self, doc):
        assert not is_access_denied(doc)


class TestRetryDelay:
    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (HttpStatusError(429, URL), 2.0),
            (HttpStatusError(429, URL, 0.0), 0.0),
            (HttpStatusError(429, URL, 7.0), 7.0),
            (HttpStatusError(404, URL), None),
            (HttpStatusError(403, URL), None),
            (HttpStatusError(500, URL), None),
            (PlaywrightTimeoutError("Timeout 15000ms exceeded"), 0.5),
            (RuntimeError("net::ERR_CONNECTION_RESET at https://x"), 0.5),
            (RuntimeError("Page.goto: Target closed"), 0.5),
            (RuntimeError("Navigation failed because page was closed"), 0.5),
            (RuntimeError("Browser has been closed"), 0.5),
            (RuntimeError(f"Access denied: CAPTCHA or block page detected at {URL}."), None),
            (ValueError("bad input"), None),
        ],
    )
    def test_classification(self, error, expected):
        assert retry_delay(error) == expected

    def test_http_status_error_carries_details(self):
        error = HttpStatusError(429, URL, 3.0)
        assert (error.status, error.retry_after) == (429, 3.0)
        assert str(error) == f"HTTP 429 when fetching {URL}"


def fetch(web: FakeWeb, url=URL, template=None, block_media=True):
    return asyncio.run(fetch_html(web.manager, url, template, block_media))


class TestFetchHtml:
    def test_returns_rendered_html_and_closes_the_context(self):
        web = FakeWeb({URL: Reply(html="<html><body>hi</body></html>")})
        assert fetch(web) == "<html><body>hi</body></html>"
        [context] = web.contexts
        context.close.assert_awaited_once()
        goto = web.pages[0].goto
        assert goto.await_args.args == (URL,)
        assert goto.await_args.kwargs["wait_until"] == "domcontentloaded"

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://x.com", "example.com", ""])
    def test_invalid_url_is_rejected_before_launching_anything(self, url):
        web = FakeWeb()
        with pytest.raises(ValueError, match="HTTP or HTTPS"):
            fetch(web, url)
        web.manager.get.assert_not_awaited()

    def test_template_cookies_are_added(self):
        cookies = [{"name": "a", "value": "b", "url": URL}]
        web = FakeWeb({URL: Reply()})
        fetch(web, template={"cookies": cookies})
        web.contexts[0].add_cookies.assert_awaited_once_with(cookies)

    def test_no_cookies_without_template(self):
        web = FakeWeb({URL: Reply()})
        fetch(web)
        web.contexts[0].add_cookies.assert_not_awaited()

    @pytest.mark.parametrize(
        ("template", "block_media", "blocks"),
        [
            (None, True, True),
            ({}, True, True),
            (None, False, False),
            ({"block_resources": []}, True, False),
        ],
    )
    def test_resource_blocking_setup(self, template, block_media, blocks):
        web = FakeWeb({URL: Reply()})
        fetch(web, template=template, block_media=block_media)
        assert web.pages[0].route.await_count == int(blocks)

    @pytest.mark.parametrize(
        ("blocked", "resource_type", "aborted"),
        [
            (None, "image", True),
            (None, "media", True),
            (None, "font", True),
            (None, "script", False),
            (None, "document", False),
            (["stylesheet"], "stylesheet", True),
            (["stylesheet"], "image", False),
        ],
    )
    def test_route_handler_blocks_configured_resource_types(self, blocked, resource_type, aborted):
        web = FakeWeb({URL: Reply()})
        template = None if blocked is None else {"block_resources": blocked}
        fetch(web, template=template)
        pattern, handler = web.pages[0].route.await_args.args
        assert pattern == "**/*"
        route = Mock(
            request=Mock(resource_type=resource_type), abort=AsyncMock(), continue_=AsyncMock()
        )
        asyncio.run(handler(route))
        assert route.abort.await_count == int(aborted)
        assert route.continue_.await_count == int(not aborted)

    def test_context_closed_when_page_setup_fails(self):
        web = FakeWeb()
        web.new_page_error = RuntimeError("setup failed")
        with pytest.raises(RuntimeError, match="setup failed"):
            fetch(web)
        web.contexts[0].close.assert_awaited_once()

    def test_context_closed_when_adding_cookies_fails(self):
        web = FakeWeb({URL: Reply()})

        async def run():
            context = await web.browser.new_context()
            context.add_cookies.side_effect = RuntimeError("bad cookie")
            web.browser.new_context = AsyncMock(return_value=context)
            with pytest.raises(RuntimeError, match="bad cookie"):
                await fetch_html(web.manager, URL, {"cookies": [{}]}, True)
            assert context.close.await_count == 1

        asyncio.run(run())

    def test_context_closed_on_cancellation(self):
        web = FakeWeb({URL: asyncio.CancelledError()})
        with pytest.raises(asyncio.CancelledError):
            fetch(web)
        web.contexts[0].close.assert_awaited_once()

    def test_http_error_is_checked_before_the_render_wait(self, sleep):
        web = FakeWeb({URL: Reply(status=404)})
        with pytest.raises(HttpStatusError, match="404") as info:
            fetch(web)
        assert info.value.status == 404
        page = web.pages[0]
        page.wait_for_load_state.assert_not_awaited()
        page.content.assert_not_awaited()
        web.contexts[0].close.assert_awaited_once()
        sleep.assert_not_awaited()
        assert len(web.visited) == 1

    def test_network_idle_timeout_is_ignored(self):
        web = FakeWeb({URL: Reply(html="<html><body>loaded</body></html>")})

        async def run():
            context = await web.browser.new_context()
            page = await context.new_page()
            page.wait_for_load_state.side_effect = PlaywrightTimeoutError("idle")
            context.new_page = AsyncMock(return_value=page)
            web.browser.new_context = AsyncMock(return_value=context)
            return await fetch_html(web.manager, URL, None, True)

        assert "loaded" in asyncio.run(run())

    def test_access_denied_page_raises_without_retrying(self, sleep):
        web = FakeWeb({URL: Reply(html="<html><head><title>Access Denied</title></head></html>")})
        with pytest.raises(RuntimeError, match="Access denied"):
            fetch(web)
        sleep.assert_not_awaited()
        assert len(web.visited) == 1

    def test_missing_response_counts_as_status_zero(self):
        web = FakeWeb({URL: Reply()})

        async def run():
            context = await web.browser.new_context()
            page = await context.new_page()
            page.goto = AsyncMock(return_value=None)
            page.content = AsyncMock(return_value="<html><body>x</body></html>")
            context.new_page = AsyncMock(return_value=page)
            web.browser.new_context = AsyncMock(return_value=context)
            return await fetch_html(web.manager, URL, None, True)

        assert "x" in asyncio.run(run())


class TestFetchHtmlRetries:
    def test_retry_after_zero_is_honored(self, sleep):
        web = FakeWeb(
            {URL: [Reply(status=429, headers={"retry-after": "0"}), Reply(html="<body>OK</body>")]}
        )
        assert "OK" in fetch(web)
        sleep.assert_awaited_once_with(0.0)
        assert len(web.contexts) == 2
        assert all(c.close.await_count == 1 for c in web.contexts)

    def test_rate_limit_without_header_uses_default_delay(self, sleep):
        web = FakeWeb({URL: [Reply(status=429), Reply(html="<body>OK</body>")]})
        fetch(web)
        sleep.assert_awaited_once_with(2.0)

    def test_retry_after_http_status_is_capped(self, sleep):
        web = FakeWeb({URL: [Reply(status=429, headers={"retry-after": "600"}), Reply()]})
        fetch(web)
        sleep.assert_awaited_once_with(30.0)

    def test_gives_up_after_the_second_rate_limit(self, sleep):
        web = FakeWeb({URL: Reply(status=429)})
        with pytest.raises(HttpStatusError) as info:
            fetch(web)
        assert info.value.status == 429
        assert len(web.visited) == 2
        sleep.assert_awaited_once()

    def test_transient_network_error_is_retried(self, sleep):
        web = FakeWeb(
            {URL: [RuntimeError("net::ERR_CONNECTION_RESET"), Reply(html="<body>OK</body>")]}
        )
        assert "OK" in fetch(web)
        sleep.assert_awaited_once_with(0.5)
        assert web.contexts[0].close.await_count == 1

    def test_timeout_is_retried_once(self, sleep):
        web = FakeWeb({URL: PlaywrightTimeoutError("Timeout")})
        with pytest.raises(PlaywrightTimeoutError):
            fetch(web)
        assert len(web.visited) == 2
        sleep.assert_awaited_once_with(0.5)

    def test_server_errors_are_not_retried(self, sleep):
        web = FakeWeb({URL: Reply(status=500)})
        with pytest.raises(HttpStatusError, match="500"):
            fetch(web)
        assert len(web.visited) == 1
        sleep.assert_not_awaited()


def source(web: FakeWeb, url="https://example.com/a.md", template=None):
    return asyncio.run(fetch_source_markdown(web.manager, url, template, True))


class TestFetchSourceMarkdown:
    def reply(self, text, content_type="text/markdown", status=200):
        return Reply(status=status, headers={"content-type": content_type}, text=text)

    def test_returns_cleaned_markdown(self):
        web = FakeWeb(
            {
                "https://example.com/a.md": self.reply(
                    "# Docs\n@twoslash-cache: x\n\nUse `<Widget>` here."
                )
            }
        )
        assert source(web) == "# Docs\n\nUse `<Widget>` here."
        web.contexts[0].close.assert_awaited_once()

    @pytest.mark.parametrize(
        "content_type", ["text/html", "text/html; charset=utf-8", "application/xhtml+xml"]
    )
    def test_html_responses_are_rejected_without_reading_the_body(self, content_type):
        web = FakeWeb({"https://example.com/a.md": self.reply("# HTML impostor", content_type)})
        assert source(web) is None
        web.contexts[0].close.assert_awaited_once()

    def test_html_body_is_never_read(self):
        web = FakeWeb({"https://example.com/a.md": self.reply("# x", "text/html")})
        text_mocks = []
        original = web._new_page

        def tracking_page():
            page = original()
            real_goto = page.goto.side_effect

            async def goto(*args, **kwargs):
                response = await real_goto(*args, **kwargs)
                text_mocks.append(response.text)
                return response

            page.goto = AsyncMock(side_effect=goto)
            return page

        web._new_page = tracking_page
        assert source(web) is None
        text_mocks[0].assert_not_awaited()

    @pytest.mark.parametrize("status", [404, 500])
    def test_http_errors_return_none(self, status):
        web = FakeWeb({"https://example.com/a.md": self.reply("# Gone", status=status)})
        assert source(web) is None

    def test_plain_text_that_is_not_markdown_returns_none(self):
        web = FakeWeb({"https://example.com/a.md": self.reply("just words", "text/plain")})
        assert source(web) is None

    def test_navigation_errors_return_none_and_close_the_context(self):
        web = FakeWeb({"https://example.com/a.md": RuntimeError("net::ERR_FAILED")})
        assert source(web) is None
        web.contexts[0].close.assert_awaited_once()

    def test_setup_errors_return_none_and_close_the_context(self):
        web = FakeWeb()
        web.new_page_error = RuntimeError("boom")
        assert source(web) is None
        web.contexts[0].close.assert_awaited_once()

    def test_browser_launch_errors_return_none(self):
        web = FakeWeb()
        web.manager.get.side_effect = RuntimeError("no browser")
        assert source(web) is None

    def test_invalid_url_is_rejected(self):
        with pytest.raises(ValueError, match="HTTP or HTTPS"):
            source(FakeWeb(), url="file:///x.md")

    def test_cancellation_propagates_and_closes_the_context(self):
        web = FakeWeb({"https://example.com/a.md": asyncio.CancelledError()})
        with pytest.raises(asyncio.CancelledError):
            source(web)
        web.contexts[0].close.assert_awaited_once()
