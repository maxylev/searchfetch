import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, it } from "node:test";
import {
  BUILTIN_TEMPLATES,
  buildSearchUrl,
  detectTemplate,
  getTemplate,
  loadTemplates,
  mapSearchParams,
  parseInlineTemplate,
  resolvePageTemplate,
  resolveSearchCandidates,
  resolveUrlTemplate,
  validateTemplate,
  validateUrl,
} from "../../src/templates.js";

const section = (extra = {}) => ({ name: "S", selector: "p", ...extra });
const template = (sections, extra = {}) => ({ name: "t", sections, ...extra });

describe("built-in templates", () => {
  const all = [...BUILTIN_TEMPLATES.values()];

  it("loads a non-empty map keyed by unique template names", () => {
    assert.ok(all.length > 0);
    assert.equal(new Set(all.map((t) => t.name)).size, all.length);
    for (const [key, t] of BUILTIN_TEMPLATES) assert.equal(key, t.name);
  });

  it("gives every template a name and a unique numeric order", () => {
    for (const t of all) {
      assert.ok(typeof t.name === "string" && t.name.length > 0);
      assert.equal(typeof t.order, "number", `${t.name} needs an order`);
    }
    assert.equal(new Set(all.map((t) => t.order)).size, all.length);
  });

  it("is sorted by order ascending", () => {
    for (let i = 1; i < all.length; i++) {
      assert.ok(all[i - 1].order <= all[i].order, `${all[i - 1].name} before ${all[i].name}`);
    }
  });

  it("puts github-repo and github-issue first", () => {
    const repo = BUILTIN_TEMPLATES.get("github-repo");
    const issue = BUILTIN_TEMPLATES.get("github-issue");
    for (const t of all) {
      if (t === repo || t === issue) continue;
      assert.ok(repo.order < t.order, `github-repo should precede ${t.name}`);
      assert.ok(issue.order < t.order, `github-issue should precede ${t.name}`);
    }
  });

  it("matches docs-rs before docs-page", () => {
    assert.ok(BUILTIN_TEMPLATES.get("docs-rs").order < BUILTIN_TEMPLATES.get("docs-page").order);
  });

  it("includes every known template", () => {
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
      "duckduckgo-lite-search",
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
    for (const name of names) assert.ok(BUILTIN_TEMPLATES.has(name), `missing '${name}'`);
  });

  it("only references existing fallback templates", () => {
    for (const t of all) {
      if (t.fallback) assert.ok(BUILTIN_TEMPLATES.has(t.fallback), `${t.name} -> ${t.fallback}`);
    }
  });
});

describe("loadTemplates", () => {
  const withDirectory = (files, check) => {
    const dir = mkdtempSync(join(tmpdir(), "searchfetch-templates-"));
    try {
      for (const [file, content] of Object.entries(files)) writeFileSync(join(dir, file), content);
      check(dir);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  };
  const json = (value) => JSON.stringify(value);

  it("sorts by order, with unordered templates last", () => {
    withDirectory(
      {
        "a.json": json({ name: "a", sections: [] }),
        "b.json": json({ name: "b", order: 2, sections: [] }),
        "c.json": json({ name: "c", order: 1, sections: [] }),
      },
      (dir) => assert.deepEqual([...loadTemplates(dir).keys()], ["c", "b", "a"]),
    );
  });

  it("rejects duplicate names, invalid JSON and empty directories", () => {
    withDirectory(
      {
        "a.json": json({ name: "x", sections: [] }),
        "b.json": json({ name: "x", sections: [] }),
      },
      (dir) => assert.throws(() => loadTemplates(dir), /Duplicate template 'x'/),
    );
    withDirectory({ "a.json": "{nope" }, (dir) =>
      assert.throws(() => loadTemplates(dir), /Invalid JSON in template file/),
    );
    withDirectory({ "readme.txt": "x" }, (dir) =>
      assert.throws(() => loadTemplates(dir), /No template JSON files/),
    );
  });
});

describe("validateTemplate", () => {
  it("accepts a minimal valid template and returns it", () => {
    const valid = template([section({ format: "attribute", attribute: "href" })]);
    assert.equal(validateTemplate(valid), valid);
  });

  const invalid = {
    "non-object template": [null, /must be a JSON object/],
    "missing name": [{ sections: [] }, /non-empty "name"/],
    "missing sections": [{ name: "t" }, /"sections" list/],
    "section without name": [template([{ selector: "p" }]), /non-empty "name"/],
    "unknown format": [template([section({ format: "xml" })]), /unknown format 'xml'/],
    "attribute format without attribute": [
      template([section({ format: "attribute" })]),
      /requires an "attribute"/,
    ],
    "unknown transform": [template([section({ transform: "shout" })]), /unknown transform 'shout'/],
    "unknown transform in list": [
      template([section({ transform: ["strip", "shout"] })]),
      /unknown transform 'shout'/,
    ],
    "zero max_items": [template([section({ max_items: 0 })]), /max_items/],
    "fractional max_items": [template([section({ max_items: 1.5 })]), /max_items/],
    "string max_items": [template([section({ max_items: "3" })]), /max_items/],
    "non-string selector": [template([section({ selector: 5 })]), /"selector" must be a string/],
    "invalid URL regex": [template([], { url_patterns: ["("] }), /invalid URL pattern/],
    "non-list url_patterns": [template([], { url_patterns: "x" }), /url_patterns/],
    "non-list children": [template([section({ children: {} })]), /"children" must be a list/],
    "nested children": [
      template([section({ children: [section({ children: [section()] })] })]),
      /cannot be nested/,
    ],
    "invalid child": [template([section({ children: [{ selector: "p" }] })]), /non-empty "name"/],
  };
  for (const [name, [value, message]] of Object.entries(invalid)) {
    it(`rejects ${name}`, () => assert.throws(() => validateTemplate(value), message));
  }
});

describe("parseInlineTemplate", () => {
  it("parses JSON and defaults the name to 'custom'", () => {
    const parsed = parseInlineTemplate('{"sections":[{"name":"S","selector":"p"}]}');
    assert.equal(parsed.name, "custom");
    assert.equal(parseInlineTemplate('{"name":"mine","sections":[]}').name, "mine");
  });

  it("reports invalid JSON and invalid templates", () => {
    assert.throws(() => parseInlineTemplate("{nope"), /Invalid inline JSON template/);
    assert.throws(() => parseInlineTemplate('{"sections":"x"}'), /Inline template: .*"sections"/);
    assert.throws(() => parseInlineTemplate("[1]"), /must be a JSON object/);
  });
});

describe("lookup", () => {
  it("getTemplate returns known templates and throws for unknown ones", () => {
    assert.equal(getTemplate("docs-rs").name, "docs-rs");
    assert.throws(() => getTemplate("nonexistent-template"), /Unknown template/);
  });

  it("resolvePageTemplate handles auto, names and inline JSON", () => {
    assert.equal(
      resolvePageTemplate("https://docs.rs/tokio/latest/tokio/", "auto").name,
      "docs-rs",
    );
    assert.equal(resolvePageTemplate("https://example.com/", "auto"), null);
    assert.equal(resolvePageTemplate("https://example.com/", "raw").name, "raw");
    const inline = resolvePageTemplate("https://example.com/", ' {"sections":[]}');
    assert.equal(inline.name, "custom");
    assert.throws(() => resolvePageTemplate("https://example.com/", "nope"), /Unknown template/);
  });

  it("validateUrl accepts only http(s) URLs with a hostname", () => {
    assert.equal(validateUrl("https://example.com/a"), "https://example.com/a");
    assert.throws(() => validateUrl("not a url"), /Invalid URL/);
    assert.throws(() => validateUrl("file:///tmp/a"), /HTTP or HTTPS/);
  });
});

describe("URL pattern detection", () => {
  const cases = [
    ["https://docs.rs/tokio/latest/tokio/", "docs-rs"],
    ["https://docs.rs/serde/latest/serde/", "docs-rs"],
    ["https://docs.rs/solana/latest/solana/", "docs-rs"],
    ["https://github.com/maxylev/searchfetch", "github-repo"],
    ["https://github.com/maxylev/searchfetch/issues/1", "github-issue"],
    ["https://www.npmjs.com/package/react", "npm-package"],
    ["https://pypi.org/project/requests/", "pypi-package"],
    ["https://crates.io/crates/serde", "crates-package"],
    ["https://hub.docker.com/_/postgres", "docker-hub"],
    ["https://docs.python.org/3/library/os.html", "docs-page"],
    ["https://example.com/random-page", null],
  ];
  for (const [url, expected] of cases) {
    it(`${url} -> ${expected}`, () => assert.equal(detectTemplate(url)?.name ?? null, expected));
  }

  it("honors a custom template map", () => {
    const custom = new Map([["x", { name: "x", url_patterns: ["^https://x\\.test/"] }]]);
    assert.equal(detectTemplate("https://x.test/a", custom).name, "x");
    assert.equal(detectTemplate("https://docs.rs/a/b/c/", custom), null);
  });
});

describe("resolveUrlTemplate", () => {
  const base = { name: "test", url_template: "https://example.com/{path}" };

  it("uses provided values, then defaults", () => {
    const withDefault = { ...base, url_params: { path: { default: "home" } } };
    assert.equal(resolveUrlTemplate(withDefault, { path: "about" }), "https://example.com/about");
    assert.equal(resolveUrlTemplate(withDefault, {}), "https://example.com/home");
    assert.equal(resolveUrlTemplate(withDefault, { path: null }), "https://example.com/home");
  });

  it("throws for missing required params and templates without url_template", () => {
    const required = { ...base, url_params: { path: { required: true } } };
    assert.throws(() => resolveUrlTemplate(required, {}), /Required URL parameter/);
    assert.throws(() => resolveUrlTemplate({ name: "n" }, {}), /no url_template/);
  });

  it("URL-encodes only when encode is 'url'", () => {
    const encoded = {
      name: "t",
      url_template: "https://e.com/?q={query}",
      url_params: { query: { encode: "url" } },
    };
    assert.equal(
      resolveUrlTemplate(encoded, { query: "hello world&x" }),
      "https://e.com/?q=hello%20world%26x",
    );
    assert.equal(
      resolveUrlTemplate({ ...encoded, url_params: {} }, { query: "a b" }),
      "https://e.com/?q=a b",
    );
  });

  it("substitutes each original placeholder once and blanks unknown ones", () => {
    assert.equal(
      resolveUrlTemplate(
        { name: "test", url_template: "https://example.com/{query}/{x}/{x}/{missing}" },
        { query: "{x}", x: "$&" },
      ),
      "https://example.com/{x}/$&/$&/",
    );
  });

  it("ignores inherited object properties as params", () => {
    assert.equal(resolveUrlTemplate(base, {}), "https://example.com/");
    assert.equal(resolveUrlTemplate({ ...base, url_template: "x/{toString}" }, {}), "x/");
  });
});

describe("mapSearchParams", () => {
  it("maps DuckDuckGo region and safe search", () => {
    assert.deepEqual(mapSearchParams("duckduckgo", "q", "us-en", null), {
      query: "q",
      kl: "us-en",
    });
    assert.equal(mapSearchParams("duckduckgo", "q", null, true).kp, "1");
    assert.equal(mapSearchParams("duckduckgo", "q", null, false).kp, "-2");
  });

  it("maps Google regions to gl and hl", () => {
    const params = mapSearchParams("google", "test query", "us-en", null);
    assert.deepEqual(params, { query: "test query", gl: "us", hl: "en" });
    assert.equal(mapSearchParams("google", "t", "pt-br", null).hl, "br");
    assert.deepEqual(mapSearchParams("google", "t", "de", null), { query: "t", gl: "de" });
  });

  it("adds nothing when options are null or the engine is unknown", () => {
    assert.deepEqual(mapSearchParams("duckduckgo", "q", null, null), { query: "q" });
    assert.deepEqual(mapSearchParams("google", "q", null, true), { query: "q" });
    assert.deepEqual(mapSearchParams("other", "q", "us-en", true), { query: "q" });
  });
});

describe("buildSearchUrl", () => {
  const google = () => getTemplate("google-search");

  it("maps Google region onto the URL", () => {
    const url = new URL(buildSearchUrl(google(), "a b", "us-en", null));
    assert.equal(url.searchParams.get("q"), "a b");
    assert.equal(url.searchParams.get("hl"), "en");
    assert.equal(url.searchParams.get("gl"), "us");
    assert.equal(url.searchParams.has("safe"), false);
  });

  it("appends safe=active or safe=off for Google", () => {
    assert.equal(
      new URL(buildSearchUrl(google(), "t", "us-en", true)).searchParams.get("safe"),
      "active",
    );
    assert.equal(
      new URL(buildSearchUrl(google(), "t", null, false)).searchParams.get("safe"),
      "off",
    );
  });

  it("uses a ? separator when the URL has no query string", () => {
    const custom = { name: "c", engine: "google", url_template: "https://g.test/{query}" };
    assert.equal(buildSearchUrl(custom, "x", null, true), "https://g.test/x?safe=active");
  });

  it("uses kp for DuckDuckGo instead of a safe parameter", () => {
    const url = new URL(buildSearchUrl(getTemplate("duckduckgo-search"), "t", "de-de", true));
    assert.equal(url.searchParams.get("kl"), "de-de");
    assert.equal(url.searchParams.get("kp"), "1");
    assert.equal(url.searchParams.has("safe"), false);
  });

  it("rejects templates that are not search templates", () => {
    assert.throws(
      () => buildSearchUrl(getTemplate("raw"), "q", null, null),
      /not a search template/,
    );
  });
});

describe("resolveSearchCandidates", () => {
  const names = (candidates) => candidates.map((c) => c.template.name);

  it("chains google -> google-search, duckduckgo-search, duckduckgo-lite-search", () => {
    const candidates = resolveSearchCandidates("google", "q", "us-en", null);
    assert.deepEqual(names(candidates), [
      "google-search",
      "duckduckgo-search",
      "duckduckgo-lite-search",
    ]);
    assert.match(candidates[0].url, /google\.com/);
    assert.match(candidates[1].url, /html\.duckduckgo\.com/);
    assert.match(candidates[2].url, /lite\.duckduckgo\.com/);
  });

  it("chains duckduckgo into two entries", () => {
    assert.deepEqual(names(resolveSearchCandidates("duckduckgo", "q", null, null)), [
      "duckduckgo-search",
      "duckduckgo-lite-search",
    ]);
  });

  it("accepts template names directly", () => {
    assert.deepEqual(names(resolveSearchCandidates("duckduckgo-lite-search", "q", null, null)), [
      "duckduckgo-lite-search",
    ]);
  });

  it("passes the region to every engine in the chain", () => {
    const [, ddg] = resolveSearchCandidates("google", "q", "de-de", true);
    assert.equal(new URL(ddg.url).searchParams.get("kl"), "de-de");
    assert.equal(new URL(ddg.url).searchParams.get("kp"), "1");
  });

  it("uses a single entry for inline templates without a fallback", () => {
    const inline = JSON.stringify({
      name: "mine",
      url_template: "https://s.test/?q={query}",
      url_params: { query: { encode: "url" } },
      sections: [],
    });
    const candidates = resolveSearchCandidates(inline, "a b", null, null);
    assert.deepEqual(names(candidates), ["mine"]);
    assert.equal(candidates[0].url, "https://s.test/?q=a%20b");
  });

  it("follows an inline template's fallback to a built-in", () => {
    const inline = JSON.stringify({
      name: "mine",
      url_template: "https://s.test/?q={query}",
      fallback: "duckduckgo-lite-search",
      sections: [],
    });
    assert.deepEqual(names(resolveSearchCandidates(inline, "q", null, null)), [
      "mine",
      "duckduckgo-lite-search",
    ]);
  });

  it("stops when a fallback repeats an already visited template name", () => {
    const inline = JSON.stringify({
      name: "duckduckgo-search",
      url_template: "https://s.test/?q={query}",
      fallback: "duckduckgo-search",
      sections: [],
    });
    assert.deepEqual(names(resolveSearchCandidates(inline, "q", null, null)), [
      "duckduckgo-search",
    ]);
  });

  it("rejects unknown engines and non-search templates", () => {
    assert.throws(
      () => resolveSearchCandidates("bing", "q", null, null),
      /Unknown template 'bing'/,
    );
    assert.throws(() => resolveSearchCandidates("raw", "q", null, null), /not a search template/);
  });
});
