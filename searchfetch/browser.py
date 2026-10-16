"""Shared stealth browser and page fetching.

One CloakBrowser instance is reused; every fetch attempt gets an isolated context so cookies and
storage never leak between requests. Mirrors ``src/browser.js``.
"""

import asyncio
import contextlib
import math
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup
from cloakbrowser import launch_async
from playwright.async_api import Browser, BrowserContext, Page, Route
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from searchfetch.markdown import clean_source_markdown, is_markdown
from searchfetch.templates import Template, validate_url

NAVIGATION_TIMEOUT_MS = 15_000
SOURCE_TIMEOUT_MS = 10_000
NETWORK_IDLE_TIMEOUT_MS = 5_000
MAX_ATTEMPTS = 2
RATE_LIMIT_DELAY = 2.0
MAX_RATE_LIMIT_DELAY = 30.0
NETWORK_RETRY_DELAY = 0.5
DEFAULT_BLOCKED_RESOURCES = ["image", "media", "font"]

_LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"]


class BrowserManager:
    """Lazily launch one browser and relaunch it after a disconnect."""

    def __init__(self) -> None:
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> Browser:
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                self._browser = await launch_async(headless=True, humanize=True, args=_LAUNCH_ARGS)
            return self._browser

    async def close(self) -> None:
        """Close the browser, waiting for an in-flight launch so it cannot outlive shutdown."""
        async with self._lock:
            browser, self._browser = self._browser, None
        if browser is not None:
            await browser.close()


class HttpStatusError(RuntimeError):
    def __init__(self, status: int, url: str, retry_after: float | None = None) -> None:
        super().__init__(f"HTTP {status} when fetching {url}")
        self.status = status
        self.retry_after = retry_after


def parse_retry_after(value: str | None, now: datetime | None = None) -> float:
    """Parse a Retry-After header (seconds or HTTP date) into a capped delay in seconds."""
    if not value:
        return RATE_LIMIT_DELAY
    try:
        delay = float(value)
    except ValueError:
        try:
            delay = (parsedate_to_datetime(value) - (now or datetime.now(UTC))).total_seconds()
        except (TypeError, ValueError):
            return RATE_LIMIT_DELAY
    if not math.isfinite(delay):
        return RATE_LIMIT_DELAY
    return min(max(delay, 0.0), MAX_RATE_LIMIT_DELAY)


_DENIED_TITLES = (
    "access denied",
    "are you a human",
    "are you a robot",
    "ddos protection",
    "forbidden",
    "one more step",
    "security check",
    "sorry, you have been blocked",
    "unusual traffic",
    "verify you are human",
)
_DENIED_BODY = (
    "bots use duckduckgo too",
    "our systems have detected unusual traffic",
    "sorry, you have been blocked",
    "to continue, please type the characters",
)
#: Only conclusive on short pages; long articles may legitimately mention these phrases.
_DENIED_SHORT_BODY = ("access denied", "are you a robot", "verify you are human")
_SHORT_BODY_LENGTH = 1200


def is_access_denied(soup: BeautifulSoup) -> bool:
    """Heuristically detect CAPTCHA, bot-check and block pages."""
    title = soup.title.get_text().lower() if soup.title else ""
    body = re.sub(r"\s+", " ", soup.body.get_text()).strip().lower() if soup.body else ""
    return (
        any(phrase in title for phrase in _DENIED_TITLES)
        or any(phrase in body for phrase in _DENIED_BODY)
        or (len(body) < _SHORT_BODY_LENGTH and any(phrase in body for phrase in _DENIED_SHORT_BODY))
    )


_TRANSIENT_ERROR = re.compile(
    r"net::|ERR_|navigation|navigating|connection|target closed|browser has been closed",
    re.IGNORECASE,
)


def retry_delay(exc: BaseException) -> float | None:
    """Rate limits, timeouts and network/browser failures are worth one more attempt."""
    if isinstance(exc, HttpStatusError):
        if exc.status != 429:
            return None
        return RATE_LIMIT_DELAY if exc.retry_after is None else exc.retry_after
    if isinstance(exc, PlaywrightTimeoutError) or _TRANSIENT_ERROR.search(str(exc)):
        return NETWORK_RETRY_DELAY
    return None


async def _open_page(
    browser: Browser, template: Template | None, block_media: bool
) -> tuple[BrowserContext, Page]:
    """Open a page in a fresh context with template cookies and resource blocking applied."""
    context = await browser.new_context()
    try:
        if template and template.get("cookies"):
            await context.add_cookies(template["cookies"])
        page = await context.new_page()
        blocked = set((template or {}).get("block_resources", DEFAULT_BLOCKED_RESOURCES))
        if block_media and blocked:

            async def route(route: Route) -> None:
                if route.request.resource_type in blocked:
                    await route.abort()
                else:
                    await route.continue_()

            await page.route("**/*", route)
        return context, page
    except BaseException:
        await context.close()
        raise


async def _fetch_html_once(
    manager: BrowserManager, url: str, template: Template | None, block_media: bool
) -> str:
    context, page = await _open_page(await manager.get(), template, block_media)
    try:
        # Check the status before waiting for rendering so a slow page cannot hide an HTTP error.
        response = await page.goto(
            url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS
        )
        status = response.status if response else 0
        if status >= 400:
            retry_after = (
                parse_retry_after(response.headers.get("retry-after")) if status == 429 else None
            )
            raise HttpStatusError(status, url, retry_after)
        # Long polling must not prevent extraction of an already loaded document.
        with contextlib.suppress(PlaywrightTimeoutError):
            await page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS)
        html = await page.content()
        if is_access_denied(BeautifulSoup(html, "html.parser")):
            raise RuntimeError(f"Access denied: CAPTCHA or block page detected at {url}.")
        return html
    finally:
        await context.close()


async def fetch_html(
    manager: BrowserManager, url: str, template: Template | None, block_media: bool
) -> str:
    """Render *url* and return its HTML, retrying rate limits and transient failures once."""
    validate_url(url)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await _fetch_html_once(manager, url, template, block_media)
        except Exception as exc:
            delay = retry_delay(exc)
            if attempt >= MAX_ATTEMPTS or delay is None:
                raise
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")


async def fetch_source_markdown(
    manager: BrowserManager, url: str, template: Template | None, block_media: bool
) -> str | None:
    """Best-effort fetch of a raw Markdown source (e.g. ``page.md``).

    Returns the cleaned Markdown, or ``None`` when the source is missing, is HTML, or does not
    look like Markdown.
    """
    validate_url(url)
    context = None
    try:
        context, page = await _open_page(await manager.get(), template, block_media)
        response = await page.goto(url, wait_until="domcontentloaded", timeout=SOURCE_TIMEOUT_MS)
        if response is None or response.status >= 400:
            return None
        content_type = response.headers.get("content-type", "").lower()
        if "text/html" in content_type or "application/xhtml+xml" in content_type:
            return None
        text = clean_source_markdown(await response.text())
        return text if is_markdown(text) else None
    except Exception:
        return None
    finally:
        if context is not None:
            await context.close()
