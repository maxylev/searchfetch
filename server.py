import asyncio
import re
import urllib.parse

from bs4 import BeautifulSoup, Tag
from cloakbrowser import launch_async
from markdownify import markdownify as md
from mcp.server.fastmcp import FastMCP
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

mcp = FastMCP("searchfetch")


class BrowserManager:
    def __init__(self):
        self.browser = None
        self._lock = asyncio.Lock()

    async def get_browser(self):
        async with self._lock:
            if self.browser and self.browser.is_connected:
                return self.browser

            self.browser = await launch_async(
                headless=True,
                humanize=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            return self.browser

    async def close(self):
        if self.browser:
            await self.browser.close()
            self.browser = None


browser_manager = BrowserManager()


def get_google_region_params(region: str) -> str:
    if not region or region == "wt-wt":
        return "hl=en&gl=us"
    parts = region.split("-")
    if len(parts) == 2:
        return f"gl={parts[0]}&hl={parts[1]}"
    return f"gl={region}&hl=en"


def _get_string_attr(tag: Tag, attr: str) -> str:
    val = tag.get(attr)
    if isinstance(val, list):
        return str(val[0]) if val else ""
    return str(val or "")


@mcp.tool()
async def websearch(
    query: str,
    engine: str = "duckduckgo",
    max_results: int = 10,
    region: str = "wt-wt",
    safe_search: str = "-1",
) -> str:
    """
    Search the web using DuckDuckGo or Google. Returns a clean list of titles, URLs, and snippets. Excellent for researching general knowledge, news, and finding URLs.

    Args:
        query: The search query string.
        engine: Search engine to use. Can be "duckduckgo" or "google". Default is "duckduckgo".
        max_results: Maximum number of results to return. Default is 10.
        region: Region and language code to localize search results (e.g., "us-en", "uk-en", "de-de"). For DuckDuckGo it maps directly. For Google, 'us' is country code and 'en' is language. Default is "wt-wt" (global/US English).
        safe_search: Safe search filtering mode. "-1" for Moderate, "1" for Strict, "-2" for Off. Default is "-1". Note: Only applies to DuckDuckGo.
    """
    try:
        browser = await browser_manager.get_browser()
        context = await browser.new_context()

        await context.add_cookies(
            [
                {
                    "name": "CONSENT",
                    "value": "YES+cb.20250101-01-p0.en+FX+999",
                    "domain": ".google.com",
                    "path": "/",
                }
            ]
        )

        page = await context.new_page()

        async def route_handler(route):
            if route.request.resource_type in ["image", "media", "font", "stylesheet"]:
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", route_handler)

        if engine.lower() == "google":
            search_url = f"https://www.google.com/search?udm=web&udm=14&q={urllib.parse.quote(query)}&{get_google_region_params(region)}"
        else:
            search_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}&kl={urllib.parse.quote(region)}&kp={urllib.parse.quote(safe_search)}"

        try:
            await page.goto(search_url, wait_until="networkidle", timeout=15000)
        except PlaywrightTimeoutError:
            pass

        content = await page.content()
        soup = BeautifulSoup(content, "html.parser")
        results = []

        if engine.lower() == "google":
            for h3 in soup.find_all("h3"):
                if len(results) >= max_results:
                    break

                if not isinstance(h3, Tag):
                    continue

                link_el = h3.find_parent("a")
                if not link_el:
                    link_el = h3.find("a")

                if not isinstance(link_el, Tag):
                    continue

                link = _get_string_attr(link_el, "href")

                if not link or (
                    link.startswith("/") and not link.startswith("/url?q=")
                ):
                    continue
                if "google.com/search" in link or "support.google.com" in link:
                    continue

                if link.startswith("/url?q="):
                    try:
                        link = urllib.parse.unquote(
                            link.split("/url?q=")[1].split("&")[0]
                        )
                    except Exception:
                        pass

                title = h3.get_text(strip=True)
                if not title:
                    continue

                snippet = ""
                parent = h3.parent
                while parent and getattr(parent, "name", None) != "body":
                    if isinstance(parent, Tag):
                        snippet_el = parent.select_one(
                            "div.VwiC3b, div[style*='-webkit-line-clamp'], div.yXK7lf, div.Uroaid"
                        )
                        if snippet_el:
                            snippet = re.sub(
                                r"\s+", " ", snippet_el.get_text(strip=True)
                            )
                            break
                    parent = parent.parent

                if link.startswith("http"):
                    if not any(link in r for r in results):
                        results.append(
                            f"[{len(results) + 1}] {title}\n    URL: {link}\n    Summary: {snippet}"
                        )
        else:
            for el in soup.select(".result"):
                if len(results) >= max_results:
                    break

                if not isinstance(el, Tag):
                    continue

                title_el = el.select_one(".result__title a")
                if not title_el:
                    continue

                link = _get_string_attr(title_el, "href")
                if "/l/?uddg=" in link:
                    try:
                        params = dict(
                            urllib.parse.parse_qsl(urllib.parse.urlsplit(link).query)
                        )
                        link = urllib.parse.unquote(str(params.get("uddg", link)))
                    except Exception:
                        pass

                snippet_el = el.select_one(".result__snippet")
                snippet = (
                    re.sub(r"\s+", " ", snippet_el.get_text()).strip()
                    if snippet_el
                    else ""
                )

                if link.startswith("http"):
                    results.append(
                        f"[{len(results) + 1}] {title_el.get_text(strip=True)}\n    URL: {link}\n    Summary: {snippet}"
                    )

        await context.close()

        if not results:
            return f"No results found on {engine}. The engine may have shown a captcha, or the query returned nothing."

        return f"Found {len(results)} search results on {engine}:\n\n" + "\n\n".join(
            results
        )

    except Exception as e:
        return f"An error occurred while executing the search: {str(e)}"


@mcp.tool()
async def webfetch(
    url: str,
    format: str = "markdown",
    start_index: int = 0,
    max_length: int = 10000,
    block_media: bool = True,
) -> str:
    """
    Fetch and extract the main text content from any webpage. Fully executes JavaScript to load React/SPAs and aggressively strips images/media (including base64) to save context tokens.

    Args:
        url: The full URL of the webpage to fetch (must start with http/https).
        format: Output format. Set to "markdown", "clean_html", or "raw_html". Default is "markdown" (highly recommended to save context tokens).
        start_index: Character offset to start reading from for pagination. Use this if a document is too large to fit in the context window. Default is 0.
        max_length: Maximum characters to return per request. Default is 10000.
        block_media: Block images, videos, and fonts entirely at the network layer to drastically speed up page loads and dodge tracking pixels. Default is True.
    """
    try:
        browser = await browser_manager.get_browser()
        context = await browser.new_context()
        page = await context.new_page()

        if block_media:

            async def route_handler(route):
                if route.request.resource_type in ["image", "media", "font"]:
                    await route.abort()
                else:
                    await route.continue_()

            await page.route("**/*", route_handler)

        try:
            await page.goto(url, wait_until="networkidle", timeout=15000)
        except PlaywrightTimeoutError:
            pass

        content = await page.content()
        await context.close()

        if format == "raw_html":
            final_content = content
        else:
            soup = BeautifulSoup(content, "html.parser")

            for tag in soup(
                [
                    "script",
                    "style",
                    "nav",
                    "header",
                    "footer",
                    "noscript",
                    "iframe",
                    "svg",
                    "aside",
                    ".advertisement",
                    "img",
                    "picture",
                    "video",
                    "audio",
                    "canvas",
                    "map",
                    "area",
                    "dialog",
                ]
            ):
                tag.decompose()

            for tag in soup.find_all(True):
                if isinstance(tag, Tag):
                    tag.attrs.pop("style", None)
                    src = _get_string_attr(tag, "src")
                    if src.startswith("data:image"):
                        tag.attrs.pop("src", None)

            if format == "clean_html":
                final_content = str(soup)
            else:
                final_content = md(str(soup), heading_style="ATX")
                final_content = re.sub(r"\n{3,}", "\n\n", final_content).strip()

        total_length = len(final_content)
        paginated_text = final_content[start_index : start_index + max_length]

        metadata = f"\n\n---\n[Document Info: Showing characters {start_index} to {start_index + len(paginated_text)} of {total_length} total."
        if start_index + max_length < total_length:
            metadata += f" Use start_index={start_index + max_length} to read more."
        metadata += "]"

        return paginated_text + metadata

    except Exception as e:
        return f"An error occurred while fetching the URL: {str(e)}"


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
