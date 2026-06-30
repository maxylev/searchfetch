import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import tomllib
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXAMPLE_SEARCH_TEMPLATE = json.dumps(
    {
        "name": "e2e-example-search",
        "url_template": "https://example.com/?q={query}",
        "url_params": {
            "query": {"required": True, "encode": "url"},
        },
        "sections": [
            {
                "name": "Results",
                "selector": "body",
                "multiple": True,
                "children": [
                    {"name": "Title", "selector": "h1", "format": "text"},
                    {
                        "name": "URL",
                        "selector": "a",
                        "format": "attribute",
                        "attribute": "href",
                        "transform": "resolve_href",
                    },
                    {"name": "Snippet", "selector": "p", "format": "text"},
                ],
            },
        ],
    }
)


def project_version() -> str:
    with Path("pyproject.toml").open("rb") as file:
        return tomllib.load(file)["project"]["version"]


async def assert_mcp_server(command: str, args: list[str]) -> None:
    params = StdioServerParameters(command=command, args=args)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            if not {"webfetch", "websearch"} <= names:
                raise AssertionError(f"Expected webfetch and websearch tools, got: {sorted(names)}")

            fetch = await session.call_tool(
                "webfetch",
                {
                    "url": "https://example.com",
                    "max_length": 300,
                    "block_media": True,
                },
            )
            fetch_text = str(getattr(fetch.content[0], "text", "")) if fetch.content else ""
            if fetch.isError or "Example Domain" not in fetch_text:
                raise AssertionError(f"Unexpected webfetch result: {fetch_text[:500]}")

            search = await session.call_tool(
                "websearch",
                {
                    "query": "site:example.com example domain",
                    "engine": EXAMPLE_SEARCH_TEMPLATE,
                    "max_results": 3,
                    "block_media": True,
                },
            )
            search_text = str(getattr(search.content[0], "text", "")) if search.content else ""
            if search.isError or "[1] Example Domain" not in search_text:
                raise AssertionError(f"Unexpected websearch result: {search_text[:500]}")


async def assert_packaged_wheel() -> None:
    with tempfile.TemporaryDirectory(prefix="searchfetch-python-package-e2e-") as temp_dir:
        venv_dir = Path(temp_dir) / ".venv"
        subprocess.run(["uv", "build"], check=True)
        subprocess.run(["uv", "venv", str(venv_dir)], check=True)

        python_bin = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        searchfetch_bin = venv_dir / (
            "Scripts/searchfetch.exe" if sys.platform == "win32" else "bin/searchfetch"
        )
        wheel = Path(f"dist/searchfetch-{project_version()}-py3-none-any.whl")

        subprocess.run(
            ["uv", "pip", "install", "--python", str(python_bin), str(wheel)],
            check=True,
        )
        await assert_mcp_server(str(searchfetch_bin), [])


async def main() -> None:
    await assert_mcp_server("uv", ["run", "python", "./server.py"])
    await assert_packaged_wheel()
    print("Python MCP E2E passed")


if __name__ == "__main__":
    asyncio.run(main())
