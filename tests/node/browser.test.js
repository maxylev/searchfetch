import assert from "node:assert/strict";
import { describe, it } from "node:test";
import * as cheerio from "cheerio";
import {
  BrowserManager,
  HttpStatusError,
  MAX_RATE_LIMIT_DELAY_MS,
  NETWORK_RETRY_DELAY_MS,
  RATE_LIMIT_DELAY_MS,
  fetchHtml,
  fetchSourceMarkdown,
  isAccessDenied,
  parseRetryAfterMs,
  retryDelayMs,
} from "../../src/browser.js";
import { createFakeBrowser, page } from "./fake-browser.js";

describe("parseRetryAfterMs", () => {
  it("parses numeric seconds", () => {
    assert.equal(parseRetryAfterMs("5"), 5000);
    assert.equal(parseRetryAfterMs("0"), 0);
  });

  it("defaults to 2000ms for missing or unparseable headers", () => {
    for (const value of [null, undefined, "", "soon"]) {
      assert.equal(parseRetryAfterMs(value), RATE_LIMIT_DELAY_MS);
    }
    assert.equal(RATE_LIMIT_DELAY_MS, 2000);
  });

  it("caps at 30000ms", () => {
    assert.equal(parseRetryAfterMs("999"), MAX_RATE_LIMIT_DELAY_MS);
    assert.equal(MAX_RATE_LIMIT_DELAY_MS, 30_000);
  });

  it("parses HTTP dates relative to now and clamps past dates to zero", () => {
    const now = Date.parse("2026-01-01T00:00:00Z");
    assert.equal(parseRetryAfterMs("Thu, 01 Jan 2026 00:00:10 GMT", now), 10_000);
    assert.equal(parseRetryAfterMs("Wed, 31 Dec 2025 23:00:00 GMT", now), 0);
    assert.equal(parseRetryAfterMs("Fri, 02 Jan 2026 00:00:00 GMT", now), MAX_RATE_LIMIT_DELAY_MS);
  });

  it("clamps negative seconds to zero", () => {
    assert.equal(parseRetryAfterMs("-5"), 0);
  });
});

describe("isAccessDenied", () => {
  const denied = (html) => isAccessDenied(cheerio.load(html));

  it("detects block pages by title", () => {
    assert.equal(denied(page("<p>x</p>", "Access Denied")), true);
    assert.equal(denied(page("<p>x</p>", "Just a moment... verify you are human")), true);
  });

  it("detects long Google unusual-traffic pages by body phrase", () => {
    const filler = "ordinary text ".repeat(200);
    assert.equal(
      denied(
        page(`${filler}Our systems have detected unusual traffic from your computer network.`),
      ),
      true,
    );
  });

  it("detects DuckDuckGo bot challenges", () => {
    assert.equal(denied(page("<p>Unfortunately, bots use DuckDuckGo too.</p>")), true);
  });

  it("only trusts weak phrases on short pages", () => {
    assert.equal(denied(page("<p>Access denied</p>")), true);
    const article = "An article about CAPTCHAs. ".repeat(100);
    assert.equal(denied(page(`<p>${article} Some say access denied pages are common.</p>`)), false);
  });

  it("accepts ordinary pages", () => {
    assert.equal(denied(page("<h1>Hello</h1><p>World</p>", "Hello")), false);
  });
});

describe("retryDelayMs", () => {
  it("retries 429 using Retry-After, falling back to the default", () => {
    assert.equal(retryDelayMs(new HttpStatusError(429, "u", 7000)), 7000);
    assert.equal(retryDelayMs(new HttpStatusError(429, "u")), RATE_LIMIT_DELAY_MS);
  });

  it("does not retry other HTTP statuses", () => {
    for (const status of [400, 403, 404, 500, 503]) {
      assert.equal(retryDelayMs(new HttpStatusError(status, "u")), null);
    }
  });

  it("retries timeouts and transient network or browser errors", () => {
    const timeout = Object.assign(new Error("slow"), { name: "TimeoutError" });
    assert.equal(retryDelayMs(timeout), NETWORK_RETRY_DELAY_MS);
    for (const message of [
      "net::ERR_CONNECTION_RESET",
      "page.goto: ERR_NAME_NOT_RESOLVED",
      "Navigation failed because page was closed",
      "Execution context was destroyed, most likely because of a navigating",
      "Target closed",
      "Connection refused",
      "Browser has been closed",
    ]) {
      assert.equal(retryDelayMs(new Error(message)), NETWORK_RETRY_DELAY_MS, message);
    }
  });

  it("does not retry other errors", () => {
    assert.equal(retryDelayMs(new Error("Access denied: CAPTCHA")), null);
    assert.equal(retryDelayMs(new Error("boom")), null);
    assert.equal(retryDelayMs(undefined), null);
  });
});

describe("HttpStatusError", () => {
  it("describes the failing request", () => {
    const err = new HttpStatusError(404, "https://example.com/x");
    assert.equal(err.message, "HTTP 404 when fetching https://example.com/x");
    assert.equal(err.status, 404);
    assert.equal(err.retryAfterMs, null);
    assert.ok(err instanceof Error);
  });
});

describe("BrowserManager", () => {
  const fakeBrowser = (events = []) => {
    const handlers = {};
    return {
      connected: true,
      isConnected() {
        return this.connected;
      },
      on: (event, handler) => {
        handlers[event] = handler;
      },
      handlers,
      close: async () => {
        events.push("closed");
      },
    };
  };

  it("launches once under concurrency and reuses the connected browser", async () => {
    let launches = 0;
    const browser = fakeBrowser();
    const manager = new BrowserManager(async () => {
      launches++;
      return browser;
    });
    const [a, b] = await Promise.all([manager.get(), manager.get()]);
    assert.equal(a, browser);
    assert.equal(b, browser);
    assert.equal(await manager.get(), browser);
    assert.equal(launches, 1);
  });

  it("relaunches after the browser disconnects", async () => {
    const browsers = [fakeBrowser(), fakeBrowser()];
    const manager = new BrowserManager(async () => browsers.shift());
    const first = await manager.get();
    first.connected = false;
    first.handlers.disconnected();
    const second = await manager.get();
    assert.notEqual(second, first);
  });

  it("passes headless launch options to the launcher", async () => {
    let options;
    const manager = new BrowserManager(async (value) => {
      options = value;
      return fakeBrowser();
    });
    await manager.get();
    assert.equal(options.headless, true);
    assert.ok(Array.isArray(options.args));
  });

  it("close waits for an in-flight launch", async () => {
    const events = [];
    let release;
    const manager = new BrowserManager(
      () =>
        new Promise((resolve) => {
          release = () => resolve(fakeBrowser(events));
        }),
    );
    const getting = manager.get();
    const closing = manager.close().then(() => events.push("close resolved"));
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(events, []);
    release();
    await Promise.all([getting, closing]);
    assert.deepEqual(events, ["closed", "close resolved"]);
  });

  it("close is a no-op before any launch", async () => {
    await new BrowserManager(async () => assert.fail("must not launch")).close();
  });
});

describe("fetchHtml", () => {
  const url = "https://example.com/a";

  it("returns rendered HTML and closes its context", async () => {
    const browser = createFakeBrowser({ [url]: { body: page("<p>Hi</p>") } });
    assert.match(await fetchHtml(browser, url, null, true), /<p>Hi<\/p>/);
    assert.deepEqual(browser.state.visited, [url]);
    assert.equal(browser.state.closedContexts, 1);
    assert.equal(browser.state.routed, 1);
  });

  it("only blocks resources when block_media is true and the list is non-empty", async () => {
    const browser = createFakeBrowser({ [url]: { body: page("x") } });
    await fetchHtml(browser, url, null, false);
    await fetchHtml(browser, url, { block_resources: [] }, true);
    assert.equal(browser.state.routed, 0);
  });

  it("applies template cookies", async () => {
    const browser = createFakeBrowser({ [url]: { body: page("x") } });
    const cookie = { name: "a", value: "b", url };
    await fetchHtml(browser, url, { cookies: [cookie] }, true);
    assert.deepEqual(browser.state.cookies, [cookie]);
  });

  it("throws HttpStatusError for 4xx without retrying", async () => {
    const browser = createFakeBrowser({ [url]: { status: 404 } });
    await assert.rejects(
      fetchHtml(browser, url, null, true),
      (err) => err instanceof HttpStatusError && err.status === 404,
    );
    assert.equal(browser.state.visited.length, 1);
    assert.equal(browser.state.closedContexts, 1);
  });

  it("throws on access-denied pages without retrying", async () => {
    const browser = createFakeBrowser({ [url]: { body: page("x", "Access Denied") } });
    await assert.rejects(
      fetchHtml(browser, url, null, true),
      /Access denied: CAPTCHA or block page/,
    );
    assert.equal(browser.state.visited.length, 1);
  });

  it("rejects invalid URLs before opening a browser", async () => {
    const browser = createFakeBrowser();
    await assert.rejects(fetchHtml(browser, "file:///etc/passwd", null, true), /HTTP or HTTPS/);
    assert.equal(browser.state.openContexts, 0);
  });
});

describe("fetchSourceMarkdown", () => {
  const url = "https://example.com/docs.md";
  const fetchSource = (route) =>
    fetchSourceMarkdown(createFakeBrowser({ [url]: route }), url, null, true);

  it("returns cleaned Markdown", async () => {
    const markdown = await fetchSource({
      headers: { "content-type": "text/markdown" },
      body: "# Title\n@twoslash-cache: abc\n\n\n\nBody",
    });
    assert.equal(markdown, "# Title\n\nBody");
  });

  it("returns null for HTML, errors, and text that is not Markdown", async () => {
    assert.equal(await fetchSource({ body: "# Looks like Markdown" }), null);
    assert.equal(
      await fetchSource({ headers: { "content-type": "application/xhtml+xml" }, body: "# T" }),
      null,
    );
    assert.equal(
      await fetchSource({ status: 404, headers: { "content-type": "text/plain" } }),
      null,
    );
    assert.equal(
      await fetchSource({ headers: { "content-type": "text/plain" }, body: "plain words" }),
      null,
    );
    assert.equal(await fetchSource({ error: new Error("net::ERR_FAILED") }), null);
  });

  it("closes the context even when navigation fails", async () => {
    const browser = createFakeBrowser({ [url]: { error: new Error("net::ERR_FAILED") } });
    await fetchSourceMarkdown(browser, url, null, true);
    assert.equal(browser.state.closedContexts, 1);
  });
});
