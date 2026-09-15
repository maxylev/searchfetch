import asyncio
import json
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

TEMPLATE_CASES = json.loads(Path("tests/fixtures/templates.json").read_text())
TEMPLATE_FIXTURES = {f"/fixture/{case['template']}": case["html"] for case in TEMPLATE_CASES}
PAGE_FIXTURE = Path("tests/fixtures/page.html").read_bytes()


def search_template(base_url: str):
    return json.dumps(
        {
            "name": "e2e-example-search",
            "url_template": base_url + "/?q={query}",
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
    return json.loads(Path("package.json").read_text())["version"]


async def assert_mcp_server(
    command: str, args: list[str], base_url: str, cwd: Path | None = None
) -> None:
    params = StdioServerParameters(command=command, args=args, cwd=cwd)
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
                    "url": base_url,
                    "max_length": 300,
                    "block_media": True,
                },
            )
            fetch_text = str(getattr(fetch.content[0], "text", "")) if fetch.content else ""
            if fetch.is_error or "Example Domain" not in fetch_text:
                raise AssertionError(f"Unexpected webfetch result: {fetch_text[:500]}")

            raw_fetch = await session.call_tool(
                "webfetch",
                {
                    "url": base_url,
                    "template": "raw",
                    "max_length": 300,
                    "block_media": True,
                },
            )
            raw_fetch_text = (
                str(getattr(raw_fetch.content[0], "text", "")) if raw_fetch.content else ""
            )
            if raw_fetch.is_error or 'template="raw"' not in raw_fetch_text:
                raise AssertionError(f"Unexpected raw webfetch result: {raw_fetch_text[:500]}")

            search = await session.call_tool(
                "websearch",
                {
                    "query": "site:example.com example domain",
                    "engine": search_template(base_url),
                    "max_results": 3,
                    "block_media": True,
                },
            )
            search_text = str(getattr(search.content[0], "text", "")) if search.content else ""
            if base_url + "/destination" not in search_text:
                raise AssertionError("Relative URL was not resolved")
            if search.is_error or "[1] Example Domain" not in search_text:
                raise AssertionError(f"Unexpected websearch result: {search_text[:500]}")

            await assert_all_templates(session, base_url)


async def assert_all_templates(session: ClientSession, base_url: str) -> None:
    for case in TEMPLATE_CASES:
        result = await session.call_tool(
            "webfetch",
            {
                "url": f"{base_url}/fixture/{case['template']}",
                "template": case["template"],
                "max_length": 20000,
                "block_media": True,
            },
        )
        text = str(getattr(result.content[0], "text", "")) if result.content else ""
        if result.is_error:
            raise AssertionError(f"{case['template']}: {text[:300]}")
        for expected in case["expect"]:
            if expected not in text:
                raise AssertionError(f"{case['template']}: missing {expected!r}")
        for absent in case["absent"]:
            if absent in text:
                raise AssertionError(f"{case['template']}: found unwanted {absent!r}")


async def assert_packaged_wheel(base_url: str) -> None:
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
        project_dir = Path(temp_dir) / "project"
        (project_dir / "templates").mkdir(parents=True)
        await assert_mcp_server(str(searchfetch_bin), [], base_url, cwd=project_dir)


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(404 if self.path == "/missing" else 200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        fixture = TEMPLATE_FIXTURES.get(self.path)
        self.wfile.write(fixture.encode("utf-8") if fixture is not None else PAGE_FIXTURE)

    def log_message(self, _format, *args):
        pass


async def main(base_url: str) -> None:
    await assert_mcp_server(sys.executable, ["./server.py"], base_url)
    await assert_packaged_wheel(base_url)
    print("Python MCP E2E passed")


if __name__ == "__main__":
    with ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler) as http_server:
        thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        thread.start()
        try:
            asyncio.run(main(f"http://127.0.0.1:{http_server.server_port}"))
        finally:
            http_server.shutdown()
            thread.join()
