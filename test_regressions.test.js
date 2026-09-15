import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as cheerio from "cheerio";
import {
  applyRemove,
  applyTransform,
  composeSections,
  composeSearchResults,
  extractTemplate,
  getTemplateByName,
  resolveUrlTemplate,
  resolveSearchTemplate,
  resolveSourceUrl,
  BrowserManager,
  TEMPLATE_MAP,
  server,
} from "./index.js";

const cases = JSON.parse(
  readFileSync(new URL("./tests/fixtures/extraction.json", import.meta.url)),
);
describe("shared extraction regressions", () => {
  for (const fixture of cases) {
    it(fixture.name, () => {
      const results = extractTemplate(
        cheerio.load(fixture.html),
        {
          sections: [
            {
              name: "Results",
              selector: fixture.selector,
              multiple: true,
              children: fixture.children,
            },
          ],
        },
        {},
      );
      assert.deepEqual(results[0].items, fixture.expected);
    });
  }
  it("max_results replaces the template limit", () => {
    const results = extractTemplate(
      cheerio.load("<p>A</p><p>B</p>"),
      {
        sections: [
          {
            name: "Results",
            selector: "p",
            multiple: true,
            max_items: 1,
            children: [{ name: "Title", selector: "", format: "text" }],
          },
        ],
      },
      { isWebsearch: true, maxResultsOverride: 2 },
    );
    assert.equal(results[0].items.length, 2);
  });
  it("honors explicitly empty removal rules", () => {
    const $ = cheerio.load("<nav>Keep</nav>");
    applyRemove($, { remove: [] });
    assert.equal($("nav").text(), "Keep");
  });
});

describe("URL regressions", () => {
  it("substitutes each original placeholder once", () => {
    assert.equal(
      resolveUrlTemplate(
        { name: "test", url_template: "https://example.com/{query}/{x}/{x}/{missing}" },
        { query: "{x}", x: "$&" },
      ),
      "https://example.com/{x}/$&/$&/",
    );
    const url = new URL(resolveSearchTemplate("google", "a", "us-en", true).url);
    assert.equal(url.searchParams.get("hl"), "en");
    assert.equal(url.searchParams.get("gl"), "us");
    assert.equal(url.searchParams.get("safe"), "active");
  });
  it("decodes DDG wrappers exactly once", () => {
    const target = "https://example.com/a%2Fb?q=a%26b";
    assert.equal(
      applyTransform("/l/?uddg=" + encodeURIComponent(target), "decode_ddg_url"),
      target,
    );
  });
  it("resolves relative, protocol-relative and fragment links", () => {
    assert.equal(
      applyTransform("../other", "resolve_href", "https://example.com/docs/page"),
      "https://example.com/other",
    );
    assert.equal(
      applyTransform("//other.com/a", "resolve_href", "https://example.com"),
      "https://other.com/a",
    );
  });
  it("appends markdown suffix to path, preserving query", () => {
    assert.equal(
      resolveSourceUrl("{url}.md", "https://example.com/docs/?a=1#part"),
      "https://example.com/docs.md?a=1",
    );
  });
});

it("browser close waits for an in-flight launch", async () => {
  const manager = new BrowserManager();
  let closed = false;
  manager.launchPromise = Promise.resolve({
    close: async () => {
      closed = true;
    },
  });
  await manager.close();
  assert.equal(closed, true);
});

it("tool schemas reject invalid pagination and non-web URLs", () => {
  const fetch = server._registeredTools.webfetch.inputSchema;
  for (const args of [
    { url: "file:///tmp/a" },
    { url: "https://example.com", start_index: -1 },
    { url: "https://example.com", max_length: 0 },
    { url: "https://example.com", max_length: 1.5 },
  ]) {
    assert.equal(fetch.safeParse(args).success, false);
  }
});

it("pagination counts Unicode code points and clamps past the end", async () => {
  const { paginate } = await import("./lib/output.js");
  assert.ok(paginate("A😀B", "raw", 1, 1).startsWith("😀\n\n---"));
  assert.ok(paginate("A😀B", "raw", 1, 1).includes("start_index=2"));
  assert.ok(paginate("abc", "raw", 20, 1).includes("characters 3 to 3 of 3"));
});

it("Google safe search can be explicitly disabled", () => {
  assert.equal(
    new URL(resolveSearchTemplate("google-search", "test", null, false).url).searchParams.get(
      "safe",
    ),
    "off",
  );
});

const compositionCases = JSON.parse(
  readFileSync(new URL("./tests/fixtures/composition.json", import.meta.url), "utf8"),
);

function toExtracted(sections) {
  return sections.map((section) => {
    switch (section.type) {
      case "value":
        return { type: "value", section: { name: section.name }, text: section.value };
      case "multiple":
        return { type: "list", section: { name: section.name }, items: section.items };
      case "children": {
        const items = {};
        for (const child of section.children) {
          if (String(child.value ?? "").trim()) items[child.name] = child.value;
        }
        return { type: "children", section: { name: section.name }, items };
      }
      case "search_results":
        return {
          type: "children-multiple",
          section: { name: section.name },
          items: section.items,
        };
      default:
        throw new Error(`Unsupported fixture section type: ${section.type}`);
    }
  });
}

function composedBody(sections) {
  const text = composeSections(toExtracted(sections), null, 0, Number.MAX_SAFE_INTEGER);
  return text.replace(/\n\n---\n\[webfetch: [\s\S]*\]$/, "");
}

describe("shared composition parity", () => {
  for (const fixture of compositionCases) {
    it(fixture.name, () => {
      assert.equal(composedBody(fixture.sections), fixture.expected);
    });
  }

  it("covers every section shape", () => {
    const types = new Set(compositionCases.flatMap((c) => c.sections.map((s) => s.type)));
    assert.deepEqual([...types].sort(), ["children", "multiple", "search_results", "value"]);
  });

  it("empty search results fall back to sections", () => {
    const extracted = [
      { type: "value", section: { name: "Title" }, text: "Fallback content" },
      { type: "children-multiple", section: { name: "Results" }, items: [] },
    ];
    assert.ok(composeSearchResults(extracted).includes("Fallback content"));
  });

  it("search results without titles report no content", () => {
    const extracted = [
      {
        type: "children-multiple",
        section: { name: "Results" },
        items: [{ Title: "", URL: "" }],
      },
    ];
    assert.equal(composeSearchResults(extracted), "(No content extracted from this page.)");
  });
});

const templateCases = JSON.parse(
  readFileSync(new URL("./tests/fixtures/templates.json", import.meta.url), "utf8"),
);

describe("built-in template fixtures", () => {
  it("covers every built-in template", () => {
    const covered = templateCases.map((fixture) => fixture.template);
    assert.equal(new Set(covered).size, covered.length);
    assert.deepEqual([...new Set(covered)].sort(), [...TEMPLATE_MAP.keys()].sort());
  });

  for (const fixture of templateCases) {
    it(fixture.template, () => {
      const template = getTemplateByName(fixture.template);
      const $ = cheerio.load(fixture.html);
      applyRemove($, template);
      const output = composeSections(
        extractTemplate($, template, {}),
        template,
        0,
        Number.MAX_SAFE_INTEGER,
      );
      for (const expected of fixture.expect ?? []) {
        assert.ok(output.includes(expected), `${fixture.template}: missing ${expected}`);
      }
      for (const absent of fixture.absent ?? []) {
        assert.ok(!output.includes(absent), `${fixture.template}: found unwanted ${absent}`);
      }
    });
  }
});
