"""MCP server exposing the ``websearch`` and ``webfetch`` tools. Mirrors ``src/server.js``."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from bs4 import BeautifulSoup
from cloakbrowser import ensure_binary
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from searchfetch import __version__
from searchfetch.browser import BrowserManager, fetch_html, fetch_source_markdown
from searchfetch.compose import NO_CONTENT, compose_search_results, compose_sections, paginate
from searchfetch.extract import FALLBACK_REMOVE, clean_document, extract_template
from searchfetch.markdown import html_to_markdown, resolve_source_url
from searchfetch.templates import (
    BUILTIN_TEMPLATES,
    resolve_page_template,
    resolve_search_candidates,
    validate_url,
)

PAGE_TEMPLATES = [name for name, t in BUILTIN_TEMPLATES.items() if not t.get("url_template")]

_TOOL_ANNOTATIONS = ToolAnnotations(readOnlyHint=True, openWorldHint=True)

Query = Annotated[str, Field(min_length=1, description="The search query.")]
Engine = Annotated[
    str,
    Field(
        description="'duckduckgo' (default), 'google', a search template name, or an inline "
        "JSON search template. Built-in engines fall back to DuckDuckGo when unavailable."
    ),
]
Region = Annotated[
    str | None,
    Field(
        description="Region-language code such as 'us-en' or 'de-de'. DuckDuckGo uses it as-is; "
        "Google maps it to country (gl) and language (hl). Default: engine default."
    ),
]
SafeSearch = Annotated[
    bool | None,
    Field(description="Enable (true) or disable (false) safe search. Default: engine default."),
]
MaxResults = Annotated[
    int, Field(strict=True, gt=0, description="Maximum results to return. Default: 10.")
]
BlockMedia = Annotated[
    bool, Field(description="Block images, media and fonts at the network layer. Default: true.")
]
Url = Annotated[str, Field(description="Absolute http(s) URL of the page to fetch.")]
TemplateArg = Annotated[
    str,
    Field(
        description=f"'auto' (detect from the URL), a template name ({', '.join(PAGE_TEMPLATES)}), "
        "or an inline JSON template. 'raw' converts the whole page with minimal filtering."
    ),
]
StartIndex = Annotated[
    int,
    Field(
        strict=True,
        ge=0,
        description="Offset in characters (Unicode code points) for pagination. Default: 0.",
    ),
]
MaxLength = Annotated[
    int, Field(strict=True, gt=0, description="Maximum characters to return. Default: 10000.")
]


async def websearch(
    manager: BrowserManager,
    query: str,
    engine: str = "duckduckgo",
    region: str | None = None,
    safe_search: bool | None = None,
    max_results: int = 10,
    block_media: bool = True,
) -> str:
    query = query.strip()
    if not query:
        raise ValueError("query must not be empty.")
    if type(max_results) is not int or max_results < 1:
        raise ValueError("max_results must be a positive integer.")

    failures = []
    for template, url in resolve_search_candidates(engine, query, region, safe_search):
        try:
            html = await fetch_html(manager, url, template, block_media)
            sections = extract_template(
                BeautifulSoup(html, "html.parser"), template, url, max_results=max_results
            )
            results = next((s for s in sections if s["type"] == "records"), None)
            output = compose_search_results(results) if results else NO_CONTENT
            if output == NO_CONTENT:
                raise RuntimeError("no results extracted")
        except Exception as exc:
            failures.append(f"{template['name']} failed: {exc}")
            continue
        if not failures:
            return output
        return (
            f"[websearch: {'; '.join(failures)}. Results are from {template['name']}.]\n\n{output}"
        )
    raise RuntimeError("; ".join(failures))


async def webfetch(
    manager: BrowserManager,
    url: str,
    template: str = "auto",
    start_index: int = 0,
    max_length: int = 10_000,
    block_media: bool = True,
) -> str:
    validate_url(url)
    if type(start_index) is not int or start_index < 0:
        raise ValueError("start_index must be a non-negative integer.")
    if type(max_length) is not int or max_length < 1:
        raise ValueError("max_length must be a positive integer.")
    resolved = resolve_page_template(url, template)
    template_name = resolved["name"] if resolved else "auto (fallback)"

    if resolved and resolved.get("source_url"):
        source_url = resolve_source_url(resolved["source_url"], url)
        markdown = await fetch_source_markdown(manager, source_url, resolved, block_media)
        if markdown is not None:
            return paginate(markdown, template_name, start_index, max_length)

    soup = BeautifulSoup(await fetch_html(manager, url, resolved, block_media), "html.parser")
    if resolved:
        output = compose_sections(extract_template(soup, resolved, url))
    else:
        clean_document(soup, FALLBACK_REMOVE)
        body = soup.body or soup
        output = html_to_markdown(body.decode_contents()) or NO_CONTENT
    return paginate(output, template_name, start_index, max_length)


def create_server(manager: BrowserManager | None = None) -> MCPServer:
    """Create the MCP server; its lifespan downloads the browser and closes it on shutdown."""
    manager = manager or BrowserManager()

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[None]:
        await asyncio.to_thread(ensure_binary)
        try:
            yield
        finally:
            await manager.close()

    server = MCPServer("searchfetch", version=__version__, lifespan=lifespan)

    @server.tool(
        name="websearch",
        title="Web Search",
        description="Search the web with DuckDuckGo or Google and return numbered results with "
        "titles, URLs and snippets.",
        annotations=_TOOL_ANNOTATIONS,
    )
    async def websearch_tool(
        query: Query,
        engine: Engine = "duckduckgo",
        region: Region = None,
        safe_search: SafeSearch = None,
        max_results: MaxResults = 10,
        block_media: BlockMedia = True,
    ) -> str:
        try:
            return await websearch(
                manager, query, engine, region, safe_search, max_results, block_media
            )
        except Exception as exc:
            raise ToolError(f"Search Error: {exc}") from exc

    @server.tool(
        name="webfetch",
        title="Web Fetch",
        description="Fetch a web page in a JavaScript-capable stealth browser and return its main "
        "content as Markdown, with images, scripts and styles stripped. Long output is paginated.",
        annotations=_TOOL_ANNOTATIONS,
    )
    async def webfetch_tool(
        url: Url,
        template: TemplateArg = "auto",
        start_index: StartIndex = 0,
        max_length: MaxLength = 10_000,
        block_media: BlockMedia = True,
    ) -> str:
        try:
            return await webfetch(manager, url, template, start_index, max_length, block_media)
        except Exception as exc:
            raise ToolError(f"Fetch Error: {exc}") from exc

    return server


def main() -> None:
    with contextlib.suppress(BrokenPipeError, KeyboardInterrupt):
        create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
