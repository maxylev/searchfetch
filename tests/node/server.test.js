import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { createServer, VERSION, webfetch, websearch } from "../../src/server.js";
import { resolveSearchCandidates } from "../../src/templates.js";
import { createFakeBrowser, page } from "./fake-browser.js";

const searchUrls = (engine, query = "test query") =>
  resolveSearchCandidates(engine, query, null, null).map((candidate) => candidate.url);

const ddgHtml = (title, href) =>
  page(
    `<div class='result'><div class='result__title'><a href='${href}'>${title}</a></div>` +
      `<div class='result__snippet'>${title} snippet</div></div>`,
  );
const liteHtml = (title, href) => page(`<a class='result-link' href='${href}'>${title}</a>`);

describe("webfetch", () => {
  it("converts unmatched pages to Markdown with the fallback template and footer", async () => {
    const url = "https://example.com/page";
    const browser = createFakeBrowser({
      [url]: { body: page("<nav>NAV</nav><h1>Hello</h1><p>World <img src='x.png'></p>") },
    });
    const output = await webfetch(browser, { url });
    assert.ok(output.startsWith("# Hello\n\nWorld"));
    assert.ok(!output.includes("NAV"));
    assert.match(
      output,
      /\[webfetch: template="auto \(fallback\)", showing characters 0 to \d+ of \d+ total\.\]$/,
    );
    assert.equal(browser.state.closedContexts, 1);
  });

  it("returns the no-content message for empty pages", async () => {
    const url = "https://example.com/empty";
    const output = await webfetch(createFakeBrowser({ [url]: { body: page("") } }), { url });
    assert.ok(output.startsWith("(No content extracted from this page.)"));
  });

  it("extracts with a detected template", async () => {
    const url = "https://docs.example.com/guide";
    const browser = createFakeBrowser({
      [url]: {
        body: page(
          "<nav>NAVNOISE</nav><h1>Getting Started</h1><main role='main'><p>Docs text</p></main>",
        ),
      },
      [`${url}.md`]: { status: 404 },
    });
    const output = await webfetch(browser, { url });
    assert.ok(output.includes("Getting Started"));
    assert.ok(output.includes("Docs text"));
    assert.ok(!output.includes("NAVNOISE"));
    assert.ok(output.includes('template="docs-page"'));
    assert.deepEqual(browser.state.visited, [`${url}.md`, url]);
  });

  it("prefers raw Markdown source when the template has a source_url", async () => {
    const url = "https://docs.example.com/guide";
    const browser = createFakeBrowser({
      [`${url}.md`]: {
        headers: { "content-type": "text/markdown" },
        body: "# Guide\n@twoslash-cache: x\n\nFrom source.",
      },
    });
    const output = await webfetch(browser, { url });
    assert.ok(output.startsWith("# Guide\n\nFrom source."));
    assert.ok(output.includes('template="docs-page"'));
    assert.deepEqual(browser.state.visited, [`${url}.md`]);
  });

  it("falls back to rendering when the source is HTML", async () => {
    const url = "https://docs.example.com/guide";
    const browser = createFakeBrowser({
      [`${url}.md`]: { body: page("<p># not md</p>") },
      [url]: { body: page("<h1>Rendered</h1><main role='main'><p>Rendered body</p></main>") },
    });
    assert.ok((await webfetch(browser, { url })).includes("Rendered body"));
  });

  it("paginates by code points and reports the next start_index", async () => {
    const url = "https://docs.example.com/guide";
    const browser = createFakeBrowser({
      [`${url}.md`]: { headers: { "content-type": "text/markdown" }, body: "# A😀B😀C" },
    });
    const first = await webfetch(browser, { url, max_length: 4 });
    assert.ok(first.startsWith("# A😀\n\n---\n"));
    assert.ok(
      first.includes("showing characters 0 to 4 of 7 total. Use start_index=4 to read more."),
    );
    const rest = await webfetch(browser, { url, start_index: 4, max_length: 100 });
    assert.ok(rest.startsWith("B😀C\n\n---\n"));
    assert.ok(rest.includes("characters 4 to 7 of 7 total.]"));
  });

  it("uses an explicit template name and inline templates", async () => {
    const url = "https://example.com/page";
    const browser = createFakeBrowser({ [url]: { body: page("<p>Raw text</p><b>Bold</b>") } });
    assert.ok((await webfetch(browser, { url, template: "raw" })).includes('template="raw"'));
    const inline = JSON.stringify({ name: "mine", sections: [{ name: "Bold", selector: "b" }] });
    const output = await webfetch(browser, { url, template: inline });
    assert.ok(output.startsWith("## Bold\n\nBold"));
    assert.ok(output.includes('template="mine"'));
  });

  it("reports HTTP errors", async () => {
    const url = "https://example.com/missing";
    await assert.rejects(webfetch(createFakeBrowser({ [url]: { status: 404 } }), { url }), {
      message: `HTTP 404 when fetching ${url}`,
    });
  });

  it("rejects unknown templates and invalid arguments before fetching", async () => {
    const browser = createFakeBrowser();
    const url = "https://example.com/";
    await assert.rejects(webfetch(browser, { url, template: "nope" }), /Unknown template/);
    for (const args of [
      { url: "file:///tmp/a" },
      { url: "ftp://example.com/a" },
      { url: "not a url" },
      { url, start_index: -1 },
      { url, max_length: 0 },
      { url, max_length: 1.5 },
      {},
    ]) {
      await assert.rejects(webfetch(browser, args), `should reject ${JSON.stringify(args)}`);
    }
    assert.equal(browser.state.openContexts, 0);
  });
});

describe("websearch", () => {
  it("returns numbered results from the primary engine without a note", async () => {
    const [ddg] = searchUrls("duckduckgo");
    const browser = createFakeBrowser({
      [ddg]: { body: ddgHtml("First", "/l/?uddg=https%3A%2F%2Fexample.com%2Fone") },
    });
    const output = await websearch(browser, { query: "test query" });
    assert.equal(
      output,
      "## Results\n\n[1] First\n    URL: https://example.com/one\n    Snippet: First snippet",
    );
    assert.deepEqual(browser.state.visited, [ddg]);
  });

  it("limits results with max_results", async () => {
    const [ddg] = searchUrls("duckduckgo");
    const body = page(
      [1, 2, 3]
        .map(
          (n) =>
            `<div class='result'><div class='result__title'><a href='https://e.com/${n}'>R${n}</a></div></div>`,
        )
        .join(""),
    );
    const output = await websearch(createFakeBrowser({ [ddg]: { body } }), {
      query: "test query",
      max_results: 2,
    });
    assert.ok(output.includes("[2] R2"));
    assert.ok(!output.includes("R3"));
  });

  it("notes the fallback engine when the primary fails", async () => {
    const [ddg, lite] = searchUrls("duckduckgo");
    const browser = createFakeBrowser({
      [ddg]: { status: 404 },
      [lite]: { body: liteHtml("Lite result", "https://example.com/lite") },
    });
    const output = await websearch(browser, { query: "test query" });
    assert.ok(
      output.startsWith(
        `[websearch: duckduckgo-search failed: HTTP 404 when fetching ${ddg}. ` +
          "Results are from duckduckgo-lite-search.]\n\n## Results\n\n[1] Lite result",
      ),
      output,
    );
  });

  it("treats a page without results as a failure and falls back", async () => {
    const [ddg, lite] = searchUrls("duckduckgo");
    const browser = createFakeBrowser({
      [ddg]: { body: page("<p>Nothing here</p>") },
      [lite]: { body: liteHtml("Lite result", "https://example.com/lite") },
    });
    const output = await websearch(browser, { query: "test query" });
    assert.ok(output.includes("duckduckgo-search failed: no results extracted."));
    assert.ok(output.includes("Results are from duckduckgo-lite-search."));
  });

  it("walks the whole Google chain", async () => {
    const [google, ddg, lite] = searchUrls("google");
    const browser = createFakeBrowser({
      [google]: { status: 404 },
      [ddg]: { status: 404 },
      [lite]: { body: liteHtml("Deep fallback", "https://example.com/deep") },
    });
    const output = await websearch(browser, { query: "test query", engine: "google" });
    assert.ok(output.includes("google-search failed"));
    assert.ok(output.includes("duckduckgo-search failed"));
    assert.ok(output.includes("Results are from duckduckgo-lite-search."));
    assert.deepEqual(browser.state.visited, [google, ddg, lite]);
  });

  it("combines every failure when all engines fail", async () => {
    const [ddg, lite] = searchUrls("duckduckgo");
    const browser = createFakeBrowser({ [ddg]: { status: 404 }, [lite]: { status: 404 } });
    await assert.rejects(websearch(browser, { query: "test query" }), (err) => {
      assert.equal(
        err.message,
        `duckduckgo-search failed: HTTP 404 when fetching ${ddg}; ` +
          `duckduckgo-lite-search failed: HTTP 404 when fetching ${lite}`,
      );
      return true;
    });
  });

  it("uses a single attempt for inline engines", async () => {
    const url = "https://s.test/?q=test%20query";
    const inline = JSON.stringify({
      name: "mine",
      url_template: "https://s.test/?q={query}",
      url_params: { query: { encode: "url" } },
      sections: [
        {
          name: "Results",
          selector: "li",
          multiple: true,
          children: [{ name: "Title", selector: "" }],
        },
      ],
    });
    const browser = createFakeBrowser({ [url]: { body: page("<ul><li>Hit</li></ul>") } });
    assert.equal(
      await websearch(browser, { query: "test query", engine: inline }),
      "## Results\n\n[1] Hit",
    );
  });

  it("rejects invalid arguments", async () => {
    const browser = createFakeBrowser();
    for (const args of [{}, { query: "  " }, { query: "q", max_results: 0 }]) {
      await assert.rejects(websearch(browser, args));
    }
    await assert.rejects(websearch(browser, { query: "q", engine: "nope" }), /Unknown template/);
  });
});

describe("createServer", () => {
  async function connect(browser) {
    const server = createServer(browser);
    const client = new Client({ name: "test", version: "0.0.0" });
    const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
    await Promise.all([server.connect(serverTransport), client.connect(clientTransport)]);
    return { client, server };
  }

  it("exposes the version from package.json", () => {
    assert.match(VERSION, /^\d+\.\d+\.\d+/);
  });

  it("registers websearch and webfetch as read-only tools", async () => {
    const { client } = await connect(createFakeBrowser());
    const { tools } = await client.listTools();
    assert.deepEqual(tools.map((t) => t.name).sort(), ["webfetch", "websearch"]);
    for (const tool of tools) assert.equal(tool.annotations.readOnlyHint, true);
    await client.close();
  });

  it("serves webfetch results over MCP", async () => {
    const url = "https://example.com/page";
    const { client } = await connect(createFakeBrowser({ [url]: { body: page("<h1>Hi</h1>") } }));
    const result = await client.callTool({ name: "webfetch", arguments: { url } });
    assert.ok(!result.isError);
    assert.ok(result.content[0].text.startsWith("# Hi"));
    await client.close();
  });

  it("returns tool errors for fetch and search failures", async () => {
    const url = "https://example.com/missing";
    const { client } = await connect(createFakeBrowser({ [url]: { status: 404 } }));
    const fetched = await client.callTool({ name: "webfetch", arguments: { url } });
    assert.equal(fetched.isError, true);
    assert.equal(fetched.content[0].text, `Fetch Error: HTTP 404 when fetching ${url}`);
    const searched = await client.callTool({ name: "websearch", arguments: { query: "q" } });
    assert.equal(searched.isError, true);
    assert.match(searched.content[0].text, /^Search Error: duckduckgo-search failed/);
    await client.close();
  });

  it("rejects invalid URLs and pagination at the schema level", async () => {
    const { client } = await connect(createFakeBrowser());
    for (const args of [
      { url: "file:///tmp/a" },
      { url: "https://example.com", start_index: -1 },
      { url: "https://example.com", max_length: 0 },
      { url: "https://example.com", max_length: 1.5 },
    ]) {
      const result = await client
        .callTool({ name: "webfetch", arguments: args })
        .catch((err) => ({ isError: true, content: [{ text: String(err) }] }));
      assert.equal(result.isError, true, JSON.stringify(args));
    }
    await client.close();
  });
});
