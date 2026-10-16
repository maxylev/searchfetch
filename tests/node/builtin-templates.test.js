import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";
import * as cheerio from "cheerio";
import { composeSections } from "../../src/compose.js";
import { extractTemplate } from "../../src/extract.js";
import { BUILTIN_TEMPLATES, getTemplate } from "../../src/templates.js";

const cases = JSON.parse(
  readFileSync(new URL("../fixtures/templates.json", import.meta.url), "utf8"),
);

describe("built-in template fixtures", () => {
  it("covers every built-in page and search template exactly once", () => {
    const covered = cases.map((fixture) => fixture.template);
    assert.equal(new Set(covered).size, covered.length, "duplicate fixture");
    assert.deepEqual([...covered].sort(), [...BUILTIN_TEMPLATES.keys()].sort());
    assert.ok(
      [...BUILTIN_TEMPLATES.values()].some((t) => t.url_template),
      "no search template",
    );
    assert.ok(
      [...BUILTIN_TEMPLATES.values()].some((t) => !t.url_template),
      "no page template",
    );
  });

  for (const fixture of cases) {
    it(fixture.template, () => {
      const template = getTemplate(fixture.template);
      const pageUrl = template.url_template
        ? "https://search.example.com/"
        : "https://example.com/page";
      const output = composeSections(
        extractTemplate(cheerio.load(fixture.html), template, { pageUrl }),
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
