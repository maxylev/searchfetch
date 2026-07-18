import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const packageJson = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
const exampleSearchTemplate = JSON.stringify({
  name: "e2e-example-search",
  url_template: "https://example.com/?q={query}",
  url_params: {
    query: { required: true, encode: "url" },
  },
  sections: [
    {
      name: "Results",
      selector: "body",
      multiple: true,
      children: [
        { name: "Title", selector: "h1", format: "text" },
        {
          name: "URL",
          selector: "a",
          format: "attribute",
          attribute: "href",
          transform: "resolve_href",
        },
        { name: "Snippet", selector: "p", format: "text" },
      ],
    },
  ],
});

async function withClient(command, args, callback) {
  const transport = new StdioClientTransport({ command, args });
  const client = new Client({ name: "searchfetch-node-e2e", version: "0.0.0" });

  try {
    await client.connect(transport);
    return await callback(client);
  } finally {
    await client.close().catch(() => {});
  }
}

async function assertMcpServer(command, args) {
  await withClient(command, args, async (client) => {
    const tools = await client.listTools();
    const names = new Set(tools.tools?.map((tool) => tool.name));

    if (!names.has("webfetch") || !names.has("websearch")) {
      throw new Error(`Expected webfetch and websearch tools, got: ${[...names].join(", ")}`);
    }

    const fetchResult = await client.callTool({
      name: "webfetch",
      arguments: {
        url: "https://example.com",
        max_length: 300,
        block_media: true,
      },
    });
    const fetchText = fetchResult.content?.[0]?.text ?? "";
    if (fetchResult.isError || !fetchText.includes("Example Domain")) {
      throw new Error(`Unexpected webfetch result: ${fetchText.slice(0, 500)}`);
    }

    const rawFetchResult = await client.callTool({
      name: "webfetch",
      arguments: {
        url: "https://example.com",
        template: "raw",
        max_length: 300,
        block_media: true,
      },
    });
    const rawFetchText = rawFetchResult.content?.[0]?.text ?? "";
    if (rawFetchResult.isError || !rawFetchText.includes('template="raw"')) {
      throw new Error(`Unexpected raw webfetch result: ${rawFetchText.slice(0, 500)}`);
    }

    const searchResult = await client.callTool({
      name: "websearch",
      arguments: {
        query: "site:example.com example domain",
        engine: exampleSearchTemplate,
        max_results: 3,
        block_media: true,
      },
    });
    const searchText = searchResult.content?.[0]?.text ?? "";
    if (searchResult.isError || !searchText.includes("[1] Example Domain")) {
      throw new Error(`Unexpected websearch result: ${searchText.slice(0, 500)}`);
    }
  });
}

async function assertPackedNpmBin() {
  const tempDir = mkdtempSync(join(tmpdir(), "searchfetch-node-package-e2e-"));
  try {
    execFileSync("npm", ["pack", "--pack-destination", tempDir], { stdio: "inherit" });
    execFileSync("npm", ["init", "-y"], { cwd: tempDir, stdio: "ignore" });
    execFileSync("npm", ["pkg", "set", "overrides.encoding-sniffer=1.0.2"], {
      cwd: tempDir,
      stdio: "ignore",
    });
    execFileSync("npm", ["install", `./searchfetch-${packageJson.version}.tgz`], {
      cwd: tempDir,
      stdio: "inherit",
    });
    await assertMcpServer(join(tempDir, "node_modules", ".bin", "searchfetch"), []);
  } finally {
    rmSync(tempDir, { recursive: true, force: true });
  }
}

await assertMcpServer("node", ["./index.js"]);
await assertPackedNpmBin();
console.log("Node MCP E2E passed");
