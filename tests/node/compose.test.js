import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";
import {
  NO_CONTENT,
  composeSearchResults,
  composeSections,
  field,
  paginate,
} from "../../src/compose.js";

const compositionCases = JSON.parse(
  readFileSync(new URL("../fixtures/composition.json", import.meta.url), "utf8"),
);

describe("shared composition fixtures", () => {
  for (const fixture of compositionCases) {
    it(fixture.name, () => assert.equal(composeSections(fixture.sections), fixture.expected));
  }

  it("covers every section type", () => {
    const types = new Set(compositionCases.flatMap((c) => c.sections.map((s) => s.type)));
    assert.deepEqual([...types].sort(), ["group", "list", "records", "value"]);
  });
});

describe("composeSections", () => {
  it("returns the no-content message for no sections", () => {
    assert.equal(composeSections([]), NO_CONTENT);
    assert.equal(NO_CONTENT, "(No content extracted from this page.)");
  });

  it("skips empty sections of every type", () => {
    const sections = [
      { name: "A", type: "value", value: "  " },
      { name: "B", type: "list", items: ["", " "] },
      { name: "C", type: "group", fields: { X: "" } },
      { name: "D", type: "records", items: [{ X: "" }] },
      { name: "E", type: "value", value: "kept" },
    ];
    assert.equal(composeSections(sections), "## E\n\nkept");
  });

  it("rejects unknown section types", () => {
    assert.throws(() => composeSections([{ name: "X", type: "children" }]), /Unknown section type/);
  });
});

describe("field", () => {
  it("matches keys case-insensitively in the order requested", () => {
    assert.equal(field({ BODY: " b ", Comment: "c" }, "comment", "body"), "c");
    assert.equal(field({ BODY: " b " }, "comment", "body"), "b");
  });

  it("skips empty values and returns '' when nothing matches", () => {
    assert.equal(field({ author: "", user: "u" }, "author", "user"), "u");
    assert.equal(field({ a: null }, "a"), "");
    assert.equal(field({}, "a"), "");
  });
});

describe("composeSearchResults", () => {
  const results = (items) => composeSearchResults({ name: "Results", items });

  it("numbers results with URL and snippet lines", () => {
    assert.equal(
      results([
        { Title: "One", URL: "https://example.com/1", Snippet: "first" },
        { Title: "Two", URL: "http://example.com/2" },
      ]),
      "## Results\n\n[1] One\n    URL: https://example.com/1\n    Snippet: first\n\n" +
        "[2] Two\n    URL: http://example.com/2",
    );
  });

  it("reports no content when nothing has a title", () => {
    assert.equal(results([]), NO_CONTENT);
    assert.equal(results([{ Title: "", URL: "https://example.com" }]), NO_CONTENT);
  });

  it("skips untitled results without leaving gaps in numbering", () => {
    const output = results([{ Title: "" }, { Title: "Real" }]);
    assert.ok(output.includes("[1] Real"));
  });

  it("drops non-http and relative URLs but keeps the result", () => {
    const output = results([
      { Title: "Rel", URL: "/internal/page", Snippet: "desc" },
      { Title: "Mail", URL: "mailto:a@b.c" },
      { Title: "Js", URL: "javascript:alert(1)" },
    ]);
    assert.ok(!output.includes("URL:"));
    assert.ok(output.includes("[1] Rel"));
    assert.ok(output.includes("Snippet: desc"));
    assert.ok(output.includes("[3] Js"));
  });

  it("drops Google internal links", () => {
    const output = results([
      { Title: "Search", URL: "https://www.google.com/search?q=test" },
      { Title: "Bare", URL: "https://google.com/search?q=test" },
      { Title: "Support", URL: "https://support.google.com/websearch" },
      { Title: "Maps", URL: "https://www.google.com/maps" },
    ]);
    assert.equal(output.match(/URL:/g).length, 1);
    assert.ok(output.includes("URL: https://www.google.com/maps"));
  });
});

describe("paginate", () => {
  it("returns the window followed by a footer", () => {
    assert.equal(
      paginate("abcdef", "raw", 0, 6),
      'abcdef\n\n---\n[webfetch: template="raw", showing characters 0 to 6 of 6 total.]',
    );
  });

  it("advertises the next start_index when more text remains", () => {
    const output = paginate("A".repeat(200), "raw", 0, 100);
    assert.ok(output.includes("showing characters 0 to 100 of 200 total."));
    assert.ok(output.includes("Use start_index=100 to read more."));
  });

  it("counts Unicode code points and never splits surrogate pairs", () => {
    const output = paginate("A😀B", "raw", 1, 1);
    assert.ok(output.startsWith("😀\n\n---"));
    assert.ok(output.includes("characters 1 to 2 of 3 total."));
    assert.ok(output.includes("start_index=2"));
  });

  it("clamps offsets past the end", () => {
    const output = paginate("abc", "raw", 20, 1);
    assert.ok(output.startsWith("\n\n---"));
    assert.ok(output.includes("characters 3 to 3 of 3"));
    assert.ok(!output.includes("start_index="));
  });
});
