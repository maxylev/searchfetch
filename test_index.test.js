/**
 * Unit tests for searchfetch index.js helpers.
 *
 * Run with: node --test test_index.test.js
 *
 * These tests verify pure helper functions without starting the MCP server.
 * No browser, no network — just logic correctness.
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  loadBuiltinTemplates,
  getTemplateByName,
  detectTemplateByUrl,
  resolveUrlTemplate,
  resolveEngineToTemplateName,
  mapSearchParams,
  isMarkdownContent,
  stripSourceMarkdown,
  resolveSourceUrl,
  parseRetryAfterMs,
  applyTransform,
  composeSections,
  composeSearchResults,
  isMainModule,
  BUILTIN_TEMPLATES,
  TEMPLATE_MAP,
} from "./index.js";

// ---------------------------------------------------------------------------
// 1. Template loading
// ---------------------------------------------------------------------------

describe("Template loading", () => {
  it("BUILTIN_TEMPLATES is a non-empty array", () => {
    assert.ok(Array.isArray(BUILTIN_TEMPLATES));
    assert.ok(BUILTIN_TEMPLATES.length > 0);
  });

  it("TEMPLATE_MAP has entries matching BUILTIN_TEMPLATES", () => {
    assert.equal(TEMPLATE_MAP.size, BUILTIN_TEMPLATES.length);
    for (const t of BUILTIN_TEMPLATES) {
      assert.ok(TEMPLATE_MAP.has(t.name), `Missing template: ${t.name}`);
    }
  });

  it("every template has a name and order", () => {
    for (const t of BUILTIN_TEMPLATES) {
      assert.ok(typeof t.name === "string" && t.name.length > 0);
      assert.ok(typeof t.order === "number");
    }
  });

  it("templates are sorted by order ascending", () => {
    for (let i = 1; i < BUILTIN_TEMPLATES.length; i++) {
      assert.ok(
        BUILTIN_TEMPLATES[i - 1].order <= BUILTIN_TEMPLATES[i].order,
        `Templates not sorted: ${BUILTIN_TEMPLATES[i - 1].name} (${BUILTIN_TEMPLATES[i - 1].order}) vs ${BUILTIN_TEMPLATES[i].name} (${BUILTIN_TEMPLATES[i].order})`,
      );
    }
  });

  it("github-repo and github-issue have lowest orders", () => {
    const ghRepo = TEMPLATE_MAP.get("github-repo");
    const ghIssue = TEMPLATE_MAP.get("github-issue");
    assert.ok(ghRepo);
    assert.ok(ghIssue);
    for (const t of BUILTIN_TEMPLATES) {
      if (t.name === "github-repo" || t.name === "github-issue") continue;
      assert.ok(ghRepo.order < t.order, `github-repo should precede ${t.name}`);
      assert.ok(ghIssue.order < t.order, `github-issue should precede ${t.name}`);
    }
  });

  it("docs-rs comes before docs-page", () => {
    const docsRs = TEMPLATE_MAP.get("docs-rs");
    const docsPage = TEMPLATE_MAP.get("docs-page");
    assert.ok(docsRs);
    assert.ok(docsPage);
    assert.ok(docsRs.order < docsPage.order, "docs-rs should precede docs-page");
  });

  it("known templates exist", () => {
    const names = [
      "docs-rs",
      "docker-hub",
      "docs-page",
      "crates-package",
      "npm-package",
      "pypi-package",
      "github-repo",
      "github-issue",
      "duckduckgo-search",
      "google-search",
      "wikipedia",
      "reddit",
      "mdn-web-docs",
      "gitlab",
      "youtube",
      "devto",
      "go-pkg",
      "javadoc",
      "raw",
    ];
    for (const name of names) {
      assert.ok(TEMPLATE_MAP.has(name), `Expected template '${name}' to exist`);
    }
  });
});

// ---------------------------------------------------------------------------
// 2. Template lookup
// ---------------------------------------------------------------------------

describe("Template lookup", () => {
  it("getTemplateByName returns template for known name", () => {
    const t = getTemplateByName("docs-rs");
    assert.equal(t.name, "docs-rs");
  });

  it("getTemplateByName throws for unknown name", () => {
    assert.throws(() => getTemplateByName("nonexistent-template"), /Unknown template/);
  });
});

// ---------------------------------------------------------------------------
// 3. URL template resolution
// ---------------------------------------------------------------------------

describe("URL template resolution", () => {
  it("resolves simple placeholders", () => {
    const tmpl = {
      name: "test",
      url_template: "https://example.com/{path}",
      url_params: { path: { default: "home" } },
    };
    const url = resolveUrlTemplate(tmpl, { path: "about" });
    assert.equal(url, "https://example.com/about");
  });

  it("uses default values when param not provided", () => {
    const tmpl = {
      name: "test",
      url_template: "https://example.com/{page}",
      url_params: { page: { default: "index" } },
    };
    const url = resolveUrlTemplate(tmpl, {});
    assert.equal(url, "https://example.com/index");
  });

  it("throws for missing required params", () => {
    const tmpl = {
      name: "test",
      url_template: "https://example.com/{query}",
      url_params: { query: { required: true } },
    };
    assert.throws(() => resolveUrlTemplate(tmpl, {}), /Required URL parameter/);
  });

  it("URL-encodes when encode is 'url'", () => {
    const tmpl = {
      name: "test",
      url_template: "https://example.com/?q={query}",
      url_params: { query: { encode: "url" } },
    };
    const url = resolveUrlTemplate(tmpl, { query: "hello world" });
    assert.equal(url, "https://example.com/?q=hello%20world");
  });
});

// ---------------------------------------------------------------------------
// 4. Engine → template name resolution
// ---------------------------------------------------------------------------

describe("Engine-to-template resolution", () => {
  it('maps "duckduckgo" to "duckduckgo-search"', () => {
    assert.equal(resolveEngineToTemplateName("duckduckgo"), "duckduckgo-search");
  });

  it('maps "google" to "google-search"', () => {
    assert.equal(resolveEngineToTemplateName("google"), "google-search");
  });

  it("passes through unknown engine names", () => {
    assert.equal(resolveEngineToTemplateName("custom-engine"), "custom-engine");
  });
});

// ---------------------------------------------------------------------------
// 5. URL pattern matching (auto template detection)
// ---------------------------------------------------------------------------

describe("URL pattern matching", () => {
  it("detects docs.rs URLs", () => {
    const urls = ["https://docs.rs/tokio/latest/tokio/", "https://docs.rs/serde/latest/serde/"];
    for (const url of urls) {
      const t = detectTemplateByUrl(url);
      assert.ok(t, `URL ${url} should match a template`);
      assert.equal(t.name, "docs-rs");
    }
  });

  it("detects GitHub repo URLs", () => {
    const t = detectTemplateByUrl("https://github.com/maxylev/searchfetch");
    assert.ok(t);
    assert.equal(t.name, "github-repo");
  });

  it("detects GitHub issue/PR URLs", () => {
    const t = detectTemplateByUrl("https://github.com/maxylev/searchfetch/issues/1");
    assert.ok(t);
    assert.equal(t.name, "github-issue");
  });

  it("detects npm package URLs", () => {
    const t = detectTemplateByUrl("https://www.npmjs.com/package/react");
    assert.ok(t);
    assert.equal(t.name, "npm-package");
  });

  it("detects PyPI package URLs", () => {
    const t = detectTemplateByUrl("https://pypi.org/project/requests/");
    assert.ok(t);
    assert.equal(t.name, "pypi-package");
  });

  it("detects crates.io URLs", () => {
    const t = detectTemplateByUrl("https://crates.io/crates/serde");
    assert.ok(t);
    assert.equal(t.name, "crates-package");
  });

  it("detects Docker Hub URLs", () => {
    const t = detectTemplateByUrl("https://hub.docker.com/_/postgres");
    assert.ok(t);
    assert.equal(t.name, "docker-hub");
  });

  it("detects docs-page URLs", () => {
    const t = detectTemplateByUrl("https://docs.python.org/3/library/os.html");
    assert.ok(t);
    assert.equal(t.name, "docs-page");
  });

  it("returns null for unmatched URLs", () => {
    const t = detectTemplateByUrl("https://example.com/random-page");
    assert.equal(t, null);
  });

  it("docs.rs matches before docs-page", () => {
    // docs.rs URLs also match docs-page patterns, but docs-rs should match first
    const t = detectTemplateByUrl("https://docs.rs/solana/latest/solana/");
    assert.ok(t);
    assert.equal(t.name, "docs-rs", "docs.rs should match docs-rs, not docs-page");
  });
});

// ---------------------------------------------------------------------------
// 6. Search parameter mapping
// ---------------------------------------------------------------------------

describe("Search parameter mapping", () => {
  it("maps DuckDuckGo region (kl parameter)", () => {
    const params = mapSearchParams("duckduckgo", "test query", "us-en", null);
    assert.equal(params.query, "test query");
    assert.equal(params.kl, "us-en");
  });

  it("maps DuckDuckGo safe search on (kp=1)", () => {
    const params = mapSearchParams("duckduckgo", "test", null, true);
    assert.equal(params.kp, "1");
  });

  it("maps DuckDuckGo safe search off (kp=-2)", () => {
    const params = mapSearchParams("duckduckgo", "test", null, false);
    assert.equal(params.kp, "-2");
  });

  it("maps Google region (hl/gl parameters)", () => {
    const params = mapSearchParams("google", "test query", "us-en", null);
    assert.equal(params.query, "test query");
    assert.equal(params.hl, "us");
    assert.equal(params.gl, "en");
  });

  it("maps Google region with single component", () => {
    const params = mapSearchParams("google", "test", "de", null);
    assert.equal(params.hl, "de");
    assert.equal(params.gl, "de");
  });

  it("no region/safe_search when null", () => {
    const params = mapSearchParams("duckduckgo", "test", null, null);
    assert.equal(params.query, "test");
    assert.equal(params.kl, undefined);
    assert.equal(params.kp, undefined);
  });
});

// ---------------------------------------------------------------------------
// 7. Transforms
// ---------------------------------------------------------------------------

describe("Transforms", () => {
  it("strip transform trims whitespace", () => {
    assert.equal(applyTransform("  hello  \n", "strip"), "hello");
  });

  it("decode_google_url decodes /url?q= links", () => {
    const input = "/url?q=https://example.com/page&sa=U&ved=...";
    const result = applyTransform(input, "decode_google_url");
    assert.equal(result, "https://example.com/page");
  });

  it("decode_google_url leaves non-google URLs unchanged", () => {
    const input = "https://example.com/page";
    assert.equal(applyTransform(input, "decode_google_url"), input);
  });

  it("resolve_href resolves relative URLs", () => {
    assert.equal(
      applyTransform("/docs/api", "resolve_href", "https://example.com"),
      "https://example.com/docs/api",
    );
  });

  it("resolve_href leaves absolute URLs unchanged", () => {
    assert.equal(
      applyTransform("https://other.com/page", "resolve_href", "https://example.com"),
      "https://other.com/page",
    );
  });

  it("applyTransform handles array of transforms", () => {
    const input = "/url?q=https://example.com/page&sa=U";
    const result = applyTransform(input, ["decode_google_url", "strip"]);
    assert.equal(result, "https://example.com/page");
  });
});

// ---------------------------------------------------------------------------
// 8. Markdown content detection
// ---------------------------------------------------------------------------

describe("Markdown content detection", () => {
  it("detects headings", () => {
    assert.ok(isMarkdownContent("# Hello World"));
  });

  it("detects links", () => {
    assert.ok(isMarkdownContent("See [docs](https://example.com) for more."));
  });

  it("detects code fences", () => {
    assert.ok(isMarkdownContent("```python\nprint('hello')\n```"));
  });

  it("detects bold", () => {
    assert.ok(isMarkdownContent("This is **bold** text."));
  });

  it("detects blockquote", () => {
    assert.ok(isMarkdownContent("> This is a quote"));
  });

  it("detects unordered lists", () => {
    assert.ok(isMarkdownContent("- item 1\n- item 2"));
  });

  it("rejects HTML", () => {
    assert.ok(!isMarkdownContent("<html><body><h1>Hello</h1></body></html>"));
  });

  it("rejects empty string", () => {
    assert.ok(!isMarkdownContent(""));
  });

  it("rejects plain text", () => {
    assert.ok(!isMarkdownContent("Just some plain text."));
  });
});

// ---------------------------------------------------------------------------
// 8.1. Twoslash stripping
// ---------------------------------------------------------------------------

describe("Twoslash stripping", () => {
  it("strips twoslash cache lines", () => {
    const input =
      "# Title\n\nSome text\n@twoslash-cache: abcdef1234567890abcdef1234567890\nMore text";
    const result = stripSourceMarkdown(input);
    assert.ok(!result.includes("@twoslash-cache:"));
    assert.ok(result.includes("# Title"));
    assert.ok(result.includes("More text"));
  });

  it("strips multiple twoslash lines", () => {
    const input = [
      "# Title",
      "@twoslash-cache: hash1",
      "content",
      "@twoslash-cache: hash2",
      "@twoslash-cache: hash3",
      "footer",
    ].join("\n");
    const result = stripSourceMarkdown(input);
    assert.ok(!result.includes("@twoslash-cache:"));
    assert.equal(result, "# Title\n\ncontent\n\nfooter");
  });

  it("collapses excess blank lines", () => {
    const input = "# Title\n\n\n\n\n\nContent\n\n\n\n\n\nFooter";
    const result = stripSourceMarkdown(input);
    assert.ok(!result.includes("\n\n\n\n"));
    assert.equal(result, "# Title\n\nContent\n\nFooter");
  });
});

// ---------------------------------------------------------------------------
// 9. Source URL resolution
// ---------------------------------------------------------------------------

describe("Source URL resolution", () => {
  it("resolves {url}.md pattern", () => {
    const result = resolveSourceUrl("{url}.md", "https://example.com/docs/api");
    assert.equal(result, "https://example.com/docs/api.md");
  });

  it("strips trailing slash before appending .md", () => {
    const result = resolveSourceUrl("{url}.md", "https://example.com/docs/api/");
    assert.equal(result, "https://example.com/docs/api.md");
  });

  it("resolves generic {url} replacement", () => {
    const result = resolveSourceUrl(
      "https://raw.example.com/{url}",
      "https://example.com/docs/api",
    );
    assert.equal(result, "https://raw.example.com/https://example.com/docs/api");
  });
});

// ---------------------------------------------------------------------------
// 10. Parse retry-after
// ---------------------------------------------------------------------------

describe("parseRetryAfterMs", () => {
  it("parses numeric seconds", () => {
    assert.equal(parseRetryAfterMs("5"), 5000);
  });

  it("defaults to 2000ms for missing header", () => {
    assert.equal(parseRetryAfterMs(null), 2000);
    assert.equal(parseRetryAfterMs(undefined), 2000);
    assert.equal(parseRetryAfterMs(""), 2000);
  });

  it("caps at 30000ms", () => {
    assert.equal(parseRetryAfterMs("999"), 30000);
  });
});

// ---------------------------------------------------------------------------
// 11. Composition helpers (no cheerio/HTML needed)
// ---------------------------------------------------------------------------

describe("Composition helpers", () => {
  it("composeSections handles empty results", () => {
    const result = composeSections([], null, 0, 10000);
    assert.ok(result.includes("(No content extracted"));
  });

  it("composeSections emits value sections as markdown headings", () => {
    const extracted = [
      {
        type: "value",
        section: { name: "Title" },
        text: "Hello World",
      },
    ];
    const result = composeSections(extracted, null, 0, 10000);
    assert.ok(result.includes("## Title"));
    assert.ok(result.includes("Hello World"));
  });

  it("composeSections includes pagination metadata", () => {
    const extracted = [
      {
        type: "value",
        section: { name: "Content" },
        text: "A".repeat(200),
      },
    ];
    const result = composeSections(extracted, null, 0, 100);
    assert.ok(result.includes("showing characters 0 to"));
    assert.ok(result.includes("Use start_index="));
  });

  it("composeSearchResults falls back to sections when no results", () => {
    const extracted = [];
    const result = composeSearchResults(extracted);
    assert.ok(result.includes("(No content extracted"));
  });

  it("composeSearchResults filters non-http URLs", () => {
    const extracted = [
      {
        type: "children-multiple",
        section: { name: "Results" },
        items: [{ Title: "Test 1", URL: "/internal/page", Snippet: "desc" }],
      },
    ];
    const result = composeSearchResults(extracted);
    // No http URL, so URL line should be absent
    assert.ok(!result.includes("URL:"));
    assert.ok(result.includes("[1] Test 1"));
  });

  it("composeSearchResults filters Google internal links", () => {
    const extracted = [
      {
        type: "children-multiple",
        section: { name: "Results" },
        items: [
          {
            Title: "Test",
            URL: "https://www.google.com/search?q=test",
            Snippet: "desc",
          },
        ],
      },
    ];
    const result = composeSearchResults(extracted);
    assert.ok(!result.includes("URL:"));
  });
});

// ---------------------------------------------------------------------------
// 12. Server does NOT start on import (import guard verification)
// ---------------------------------------------------------------------------

describe("Import without run", () => {
  it("importing index.js does not start the MCP server", () => {
    // If we get here without the server starting (no error about stdio),
    // the import guard is working. We can also check that exported symbols exist.
    assert.ok(typeof loadBuiltinTemplates === "function");
    assert.ok(Array.isArray(BUILTIN_TEMPLATES));
  });

  it("all expected exports are present", () => {
    const expected = [
      "loadBuiltinTemplates",
      "getTemplateByName",
      "detectTemplateByUrl",
      "resolveUrlTemplate",
      "resolveEngineToTemplateName",
      "mapSearchParams",
      "isMarkdownContent",
      "stripSourceMarkdown",
      "resolveSourceUrl",
      "parseRetryAfterMs",
      "isAccessDenied",
      "applyTransform",
      "composeSections",
      "composeSearchResults",
      "genericFallback",
      "isMainModule",
      "BUILTIN_TEMPLATES",
      "TEMPLATE_MAP",
    ];
    for (const name of expected) {
      assert.ok(imported[name] !== undefined, `Expected export '${name}' not found`);
    }
  });
});

// ---------------------------------------------------------------------------
// 13. Main guard: realpath-based argv[1] check for npx / symlink support
// ---------------------------------------------------------------------------

describe("Main guard (realpath-based)", () => {
  const indexFile = fileURLToPath(new URL("./index.js", import.meta.url).href);
  const indexRealPath = realpathSync(indexFile);

  it("returns true for direct execution path", () => {
    assert.equal(isMainModule(indexFile, indexRealPath), true);
  });

  it("returns true for npm/npx bin symlink path", () => {
    const tmp = mkdtempSync(join(tmpdir(), "searchfetch-guard-"));
    const link = join(tmp, "searchfetch");

    try {
      symlinkSync(indexFile, link);
      assert.equal(isMainModule(link, indexRealPath), true);
    } finally {
      rmSync(tmp, { recursive: true, force: true });
    }
  });

  it("returns false for a different executable path", () => {
    const tmp = mkdtempSync(join(tmpdir(), "searchfetch-guard-"));
    const otherFile = join(tmp, "other.js");

    try {
      writeFileSync(otherFile, "// not the searchfetch entrypoint\n", "utf8");
      assert.equal(isMainModule(otherFile, indexRealPath), false);
    } finally {
      rmSync(tmp, { recursive: true, force: true });
    }
  });

  it("returns false for missing or unresolvable argv[1]", () => {
    assert.equal(isMainModule(undefined, indexRealPath), false);
    assert.equal(
      isMainModule(join(tmpdir(), "definitely-missing-searchfetch-entry"), indexRealPath),
      false,
    );
  });
});

// Re-import everything for the export-check in the test above
import * as imported from "./index.js";
