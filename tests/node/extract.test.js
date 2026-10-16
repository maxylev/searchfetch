import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";
import * as cheerio from "cheerio";
import {
  DEFAULT_REMOVE,
  applyTransforms,
  cleanDocument,
  extractSection,
  extractTemplate,
  extractValue,
  selectInScope,
  splitSelectors,
} from "../../src/extract.js";
import { cleanSourceMarkdown, isMarkdown, resolveSourceUrl } from "../../src/markdown.js";

const readFixture = (name) =>
  JSON.parse(readFileSync(new URL(`../fixtures/${name}`, import.meta.url), "utf8"));

const records = (html, children, extra = {}) =>
  extractSection(cheerio.load(html), {
    name: "R",
    selector: extra.selector ?? "li",
    multiple: true,
    children,
    ...extra,
  });

describe("shared extraction fixtures", () => {
  for (const fixture of readFixture("extraction.json")) {
    it(fixture.name, () => {
      const [result] = extractTemplate(cheerio.load(fixture.html), {
        sections: [
          {
            name: "Results",
            selector: fixture.selector,
            multiple: true,
            children: fixture.children,
          },
        ],
      });
      assert.equal(result.type, "records");
      assert.deepEqual(result.items, fixture.expected);
    });
  }
});

describe("splitSelectors", () => {
  it("splits on top-level commas and trims", () => {
    assert.deepEqual(splitSelectors("a, b ,c"), ["a", "b", "c"]);
  });

  it("keeps commas inside functions, brackets, quotes and escapes", () => {
    assert.deepEqual(splitSelectors(":is(a, b), c"), [":is(a, b)", "c"]);
    assert.deepEqual(splitSelectors("p[data-x='a,b'], q"), ["p[data-x='a,b']", "q"]);
    assert.deepEqual(splitSelectors('p[data-x="a,b"],q'), ['p[data-x="a,b"]', "q"]);
    assert.deepEqual(splitSelectors(String.raw`a\,b, c`), [String.raw`a\,b`, "c"]);
  });

  it("keeps empty fallbacks", () => {
    assert.deepEqual(splitSelectors(".x, "), [".x", ""]);
    assert.deepEqual(splitSelectors(), [""]);
  });
});

describe("cleanDocument", () => {
  it("removes default noise, inline styles and data: sources", () => {
    const $ = cheerio.load(
      "<nav>N</nav><script>s</script><p style='color:red'>P<img src='data:x' alt='i'></p>",
    );
    cleanDocument($);
    assert.equal($("nav, script").length, 0);
    assert.equal($("p").attr("style"), undefined);
    assert.equal($("img").attr("src"), undefined);
    assert.ok(DEFAULT_REMOVE.includes("nav"));
  });

  it("honors explicitly empty removal rules", () => {
    const $ = cheerio.load("<nav>Keep</nav>");
    cleanDocument($, []);
    assert.equal($("nav").text(), "Keep");
  });

  it("skips selectors the engine cannot parse", () => {
    const $ = cheerio.load("<p>Keep</p><i>gone</i>");
    assert.doesNotThrow(() => cleanDocument($, ["p:nonexistent-pseudo(", "i"]));
    assert.equal($("p").text(), "Keep");
    assert.equal($("i").length, 0);
  });
});

describe("selectInScope", () => {
  const $ = cheerio.load("<div class='r'><h3>T</h3></div><div class='r'><p>Other</p></div>");

  it("finds descendants, ancestors and the scope itself", () => {
    const scope = $(".r").first();
    assert.equal(selectInScope(scope, "h3").text(), "T");
    assert.equal(selectInScope(scope, ".r").length, 1);
    assert.equal(selectInScope(scope, "").length, 1);
    assert.equal(selectInScope($("h3"), ".r").length, 1);
  });

  it("never borrows from sibling subtrees", () => {
    assert.equal(selectInScope($(".r").eq(1), "h3").length, 0);
  });

  it("falls through the selector list in order", () => {
    assert.equal(selectInScope($(".r").eq(1), "h3, p").text(), "Other");
  });
});

describe("applyTransforms", () => {
  it("strip trims whitespace", () => {
    assert.equal(applyTransforms("  hello  \n", "strip"), "hello");
  });

  it("decode_google_url decodes /url?q= links and leaves others unchanged", () => {
    assert.equal(
      applyTransforms("/url?q=https://example.com/page&sa=U&ved=...", "decode_google_url"),
      "https://example.com/page",
    );
    assert.equal(
      applyTransforms("https://example.com/page", "decode_google_url"),
      "https://example.com/page",
    );
    assert.equal(applyTransforms("/url?sa=U", "decode_google_url"), "/url?sa=U");
  });

  it("decode_ddg_url decodes wrappers exactly once", () => {
    const target = "https://example.com/a%2Fb?q=a%26b";
    const wrapped = `//duckduckgo.com/l/?uddg=${encodeURIComponent(target)}&rut=abc`;
    assert.equal(applyTransforms(wrapped, "decode_ddg_url"), target);
    assert.equal(applyTransforms("https://example.com/", "decode_ddg_url"), "https://example.com/");
    assert.equal(applyTransforms("/l/?rut=abc", "decode_ddg_url"), "/l/?rut=abc");
  });

  it("json_parse pretty-prints valid JSON and keeps invalid text", () => {
    assert.equal(applyTransforms('{"a":1}', "json_parse"), '{\n  "a": 1\n}');
    assert.equal(applyTransforms("not json", "json_parse"), "not json");
  });

  it("resolve_href resolves relative, protocol-relative and absolute links", () => {
    const page = "https://example.com/docs/page";
    assert.equal(
      applyTransforms("/docs/api", "resolve_href", "https://example.com"),
      "https://example.com/docs/api",
    );
    assert.equal(applyTransforms("../other", "resolve_href", page), "https://example.com/other");
    assert.equal(applyTransforms("//other.com/a", "resolve_href", page), "https://other.com/a");
    assert.equal(
      applyTransforms("https://other.com/page", "resolve_href", page),
      "https://other.com/page",
    );
    assert.equal(applyTransforms("#frag", "resolve_href", page), `${page}#frag`);
    assert.equal(applyTransforms("/x", "resolve_href", ""), "/x");
  });

  it("applies a list of transforms in order and tolerates none", () => {
    assert.equal(
      applyTransforms("/url?q=https://example.com/page&sa=U", ["decode_google_url", "strip"]),
      "https://example.com/page",
    );
    assert.equal(applyTransforms("x", undefined), "x");
    assert.equal(applyTransforms("", ["strip", "json_parse"]), "");
  });
});

describe("extractValue", () => {
  const $ = cheerio.load(
    "<div><a href='/x' class='k'>Link <b>bold</b>\n  text</a><pre> raw </pre></div>",
  );

  it("collapses whitespace for text", () => {
    assert.equal(extractValue($("a"), { format: "text" }), "Link bold text");
    assert.equal(extractValue($("a"), {}), "Link bold text");
  });

  it("reads attributes, defaulting to empty", () => {
    assert.equal(extractValue($("a"), { format: "attribute", attribute: "href" }), "/x");
    assert.equal(extractValue($("a"), { format: "attribute", attribute: "nope" }), "");
  });

  it("returns trimmed inner HTML and Markdown", () => {
    assert.equal(extractValue($("pre"), { format: "html" }), "raw");
    assert.equal(extractValue($("a"), { format: "markdown" }), "Link **bold** text");
  });

  it("applies transforms after formatting", () => {
    const section = { format: "attribute", attribute: "href", transform: "resolve_href" };
    assert.equal(extractValue($("a"), section, "https://e.com/p/"), "https://e.com/x");
  });
});

describe("extractSection result types", () => {
  const html = "<ul><li><b>A</b> <i>1</i></li><li><b>B</b></li><li></li></ul><h1>Title</h1>";

  it("returns a value", () => {
    const result = extractSection(cheerio.load(html), { name: "T", selector: "h1" });
    assert.deepEqual(result, { name: "T", type: "value", value: "Title" });
  });

  it("returns an empty value when nothing matches", () => {
    const result = extractSection(cheerio.load(html), { name: "T", selector: ".none" });
    assert.deepEqual(result, { name: "T", type: "value", value: "" });
  });

  it("uses <body> for an empty selector", () => {
    const result = extractSection(cheerio.load("<p>Hi</p>"), { name: "B", selector: "" });
    assert.equal(result.value, "Hi");
  });

  it("returns a list and omits empty items", () => {
    const result = extractSection(cheerio.load(html), {
      name: "L",
      selector: "li",
      multiple: true,
    });
    assert.deepEqual(result, { name: "L", type: "list", items: ["A 1", "B"] });
  });

  it("returns a group with empty fields omitted", () => {
    const result = extractSection(cheerio.load(html), {
      name: "G",
      selector: "li",
      children: [
        { name: "First", selector: "b" },
        { name: "Second", selector: "i" },
        { name: "Missing", selector: "u" },
      ],
    });
    assert.deepEqual(result, { name: "G", type: "group", fields: { First: "A", Second: "1" } });
  });

  it("returns an empty group when the section is missing", () => {
    const result = extractSection(cheerio.load(html), {
      name: "G",
      selector: ".none",
      children: [{ name: "F", selector: "b" }],
    });
    assert.deepEqual(result, { name: "G", type: "group", fields: {} });
  });

  it("returns records and drops records without any field", () => {
    const result = records(html, [
      { name: "Name", selector: "b" },
      { name: "Count", selector: "i" },
    ]);
    assert.deepEqual(result, {
      name: "R",
      type: "records",
      items: [{ Name: "A", Count: "1" }, { Name: "B" }],
    });
  });

  it("falls back through selector lists at the section level", () => {
    const result = extractSection(cheerio.load(html), { name: "T", selector: ".none, h1" });
    assert.equal(result.value, "Title");
  });

  it("limits items with max_items", () => {
    const result = records(html, [{ name: "Name", selector: "b" }], { max_items: 1 });
    assert.deepEqual(result.items, [{ Name: "A" }]);
    const list = extractSection(cheerio.load(html), {
      name: "L",
      selector: "li",
      multiple: true,
      max_items: 1,
    });
    assert.deepEqual(list.items, ["A 1"]);
  });
});

describe("required sections and fields", () => {
  it("throws when a required section is missing", () => {
    assert.throws(
      () =>
        extractSection(cheerio.load("<p>x</p>"), {
          name: "Need",
          selector: ".none",
          required: true,
        }),
      /Required section 'Need' not found/,
    );
  });

  it("does not throw when a required section is present", () => {
    const result = extractSection(cheerio.load("<p>x</p>"), {
      name: "N",
      selector: "p",
      required: true,
    });
    assert.equal(result.value, "x");
  });

  it("throws when a required field is missing from a record", () => {
    assert.throws(
      () => records("<li><i>1</i></li>", [{ name: "Name", selector: "b", required: true }]),
      /Required field 'Name' not found in section 'R'/,
    );
  });

  it("treats a present but empty required field as satisfied", () => {
    const result = records("<li><b></b><i>1</i></li>", [
      { name: "Name", selector: "b", required: true },
      { name: "Count", selector: "i" },
    ]);
    assert.deepEqual(result.items, [{ Count: "1" }]);
  });
});

describe("extractTemplate", () => {
  const html = "<p>A</p><p>B</p><p>C</p><div><span>x</span><span>y</span><span>z</span></div>";
  const first = {
    name: "First",
    selector: "p",
    multiple: true,
    max_items: 1,
    children: [{ name: "T", selector: "" }],
  };
  const second = {
    name: "Second",
    selector: "span",
    multiple: true,
    max_items: 1,
    children: [{ name: "T", selector: "" }],
  };

  it("applies max_items without an override", () => {
    const results = extractTemplate(cheerio.load(html), { sections: [first, second] });
    assert.deepEqual(
      results.map((r) => r.items.length),
      [1, 1],
    );
  });

  it("maxResults overrides only the first records section", () => {
    const results = extractTemplate(
      cheerio.load(html),
      { sections: [first, second] },
      { maxResults: 2 },
    );
    assert.deepEqual(results[0].items, [{ T: "A" }, { T: "B" }]);
    assert.deepEqual(results[1].items, [{ T: "x" }]);
  });

  it("ignores non-records sections when choosing the section to override", () => {
    const list = { name: "List", selector: "p", multiple: true, max_items: 1 };
    const results = extractTemplate(
      cheerio.load(html),
      { sections: [list, first] },
      { maxResults: 3 },
    );
    assert.equal(results[0].items.length, 1);
    assert.equal(results[1].items.length, 3);
  });

  it("removes template noise first, using DEFAULT_REMOVE only when remove is absent", () => {
    const page = "<nav>Nav</nav><p>Body</p>";
    const value = { name: "V", selector: "nav" };
    assert.equal(extractTemplate(cheerio.load(page), { sections: [value] })[0].value, "");
    assert.equal(
      extractTemplate(cheerio.load(page), { sections: [value], remove: [] })[0].value,
      "Nav",
    );
    assert.equal(
      extractTemplate(cheerio.load(page), { sections: [value], remove: ["nav"] })[0].value,
      "",
    );
  });

  it("passes pageUrl to resolve_href transforms", () => {
    const section = {
      name: "Links",
      selector: "a",
      multiple: true,
      children: [
        {
          name: "URL",
          selector: "",
          format: "attribute",
          attribute: "href",
          transform: "resolve_href",
        },
      ],
    };
    const [result] = extractTemplate(
      cheerio.load("<a href='/a'>x</a>"),
      { sections: [section] },
      { pageUrl: "https://e.com/p" },
    );
    assert.deepEqual(result.items, [{ URL: "https://e.com/a" }]);
  });
});

describe("markdown helpers", () => {
  it("isMarkdown detects Markdown syntax", () => {
    for (const text of [
      "# Hello World",
      "See [docs](https://example.com) for more.",
      "```python\nprint('hello')\n```",
      "This is **bold** text.",
      "> This is a quote",
      "- item 1\n- item 2",
    ]) {
      assert.ok(isMarkdown(text), text);
    }
  });

  it("isMarkdown rejects HTML, empty and plain text", () => {
    assert.ok(!isMarkdown("<html><body><h1>Hello</h1></body></html>"));
    assert.ok(!isMarkdown(""));
    assert.ok(!isMarkdown("Just some plain text."));
  });

  it("isMarkdown allows a few inline tags but not many", () => {
    assert.ok(isMarkdown("# Title\n\nText with <b>bold</b> tag"));
    assert.ok(!isMarkdown("# Title\n<p>a</p><p>b</p><p>c</p><p>d</p>"));
  });

  it("cleanSourceMarkdown strips twoslash cache lines", () => {
    const input = [
      "# Title",
      "@twoslash-cache: hash1",
      "content",
      "@twoslash-cache: hash2",
      "@twoslash-cache: hash3",
      "footer",
    ].join("\n");
    const result = cleanSourceMarkdown(input);
    assert.ok(!result.includes("@twoslash-cache:"));
    assert.equal(result, "# Title\n\ncontent\n\nfooter");
  });

  it("cleanSourceMarkdown collapses excess blank lines", () => {
    assert.equal(
      cleanSourceMarkdown("# Title\n\n\n\n\n\nContent\n\n\n\n\n\nFooter"),
      "# Title\n\nContent\n\nFooter",
    );
  });

  it("resolveSourceUrl appends .md to the path", () => {
    assert.equal(
      resolveSourceUrl("{url}.md", "https://example.com/docs/api"),
      "https://example.com/docs/api.md",
    );
    assert.equal(
      resolveSourceUrl("{url}.md", "https://example.com/docs/api/"),
      "https://example.com/docs/api.md",
    );
  });

  it("resolveSourceUrl keeps the query, drops the fragment and handles the root path", () => {
    assert.equal(
      resolveSourceUrl("{url}.md", "https://example.com/docs/?a=1#part"),
      "https://example.com/docs.md?a=1",
    );
    assert.equal(resolveSourceUrl("{url}.md", "https://a.com/"), "https://a.com/.md");
  });

  it("resolveSourceUrl replaces other {url} placeholders verbatim", () => {
    assert.equal(
      resolveSourceUrl("https://raw.example.com/{url}", "https://example.com/docs/api"),
      "https://raw.example.com/https://example.com/docs/api",
    );
  });
});
