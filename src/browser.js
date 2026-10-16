/**
 * Shared stealth browser and page fetching.
 *
 * One CloakBrowser instance is reused; every fetch attempt gets an isolated context so cookies
 * and storage never leak between requests. Mirrors `searchfetch/browser.py`.
 */
import * as cheerio from "cheerio";
import { launch } from "cloakbrowser";
import { cleanSourceMarkdown, isMarkdown } from "./markdown.js";
import { validateUrl } from "./templates.js";

export const NAVIGATION_TIMEOUT_MS = 15_000;
export const SOURCE_TIMEOUT_MS = 10_000;
export const NETWORK_IDLE_TIMEOUT_MS = 5_000;
export const MAX_ATTEMPTS = 2;
export const RATE_LIMIT_DELAY_MS = 2_000;
export const MAX_RATE_LIMIT_DELAY_MS = 30_000;
export const NETWORK_RETRY_DELAY_MS = 500;
export const DEFAULT_BLOCKED_RESOURCES = ["image", "media", "font"];

const LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"];

export class BrowserManager {
  #browser = null;
  #launching = null;
  #launcher;

  /** `launcher` is injectable so tests can avoid starting a real browser. */
  constructor(launcher = launch) {
    this.#launcher = launcher;
  }

  /** Return the connected browser, launching (once, even under concurrency) when needed. */
  async get() {
    if (this.#browser?.isConnected()) return this.#browser;
    this.#launching ??= this.#launcher({ headless: true, humanize: true, args: LAUNCH_ARGS })
      .then((browser) => {
        this.#browser = browser;
        browser.on("disconnected", () => {
          if (this.#browser === browser) this.#browser = null;
        });
        return browser;
      })
      .finally(() => {
        this.#launching = null;
      });
    return this.#launching;
  }

  /** Close the browser, waiting for an in-flight launch so it cannot outlive shutdown. */
  async close() {
    const browser = await (this.#launching ?? this.#browser);
    this.#browser = null;
    await browser?.close();
  }
}

export class HttpStatusError extends Error {
  constructor(status, url, retryAfterMs = null) {
    super(`HTTP ${status} when fetching ${url}`);
    this.name = "HttpStatusError";
    this.status = status;
    this.retryAfterMs = retryAfterMs;
  }
}

/** Parse a Retry-After header (seconds or HTTP date) into a capped delay in milliseconds. */
export function parseRetryAfterMs(value, now = Date.now()) {
  if (!value) return RATE_LIMIT_DELAY_MS;
  const seconds = Number(value);
  const delay = Number.isFinite(seconds) ? seconds * 1000 : Date.parse(value) - now;
  if (!Number.isFinite(delay)) return RATE_LIMIT_DELAY_MS;
  return Math.min(Math.max(delay, 0), MAX_RATE_LIMIT_DELAY_MS);
}

const DENIED_TITLES = [
  "access denied",
  "are you a human",
  "are you a robot",
  "ddos protection",
  "forbidden",
  "one more step",
  "security check",
  "sorry, you have been blocked",
  "unusual traffic",
  "verify you are human",
];
const DENIED_BODY = [
  "bots use duckduckgo too",
  "our systems have detected unusual traffic",
  "sorry, you have been blocked",
  "to continue, please type the characters",
];
/** Only conclusive on short pages; long articles may legitimately mention these phrases. */
const DENIED_SHORT_BODY = ["access denied", "are you a robot", "verify you are human"];
const SHORT_BODY_LENGTH = 1200;

/** Heuristically detect CAPTCHA, bot-check and block pages. */
export function isAccessDenied($) {
  const title = $("title").text().toLowerCase();
  const body = $("body").text().replace(/\s+/g, " ").trim().toLowerCase();
  return (
    DENIED_TITLES.some((phrase) => title.includes(phrase)) ||
    DENIED_BODY.some((phrase) => body.includes(phrase)) ||
    (body.length < SHORT_BODY_LENGTH && DENIED_SHORT_BODY.some((phrase) => body.includes(phrase)))
  );
}

const TRANSIENT_ERROR =
  /net::|ERR_|navigation|navigating|connection|target closed|browser has been closed/i;

/** Rate limits, timeouts and network/browser failures are worth one more attempt. */
export function retryDelayMs(err) {
  if (err instanceof HttpStatusError) {
    return err.status === 429 ? (err.retryAfterMs ?? RATE_LIMIT_DELAY_MS) : null;
  }
  if (err?.name === "TimeoutError" || TRANSIENT_ERROR.test(err?.message ?? "")) {
    return NETWORK_RETRY_DELAY_MS;
  }
  return null;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** Open a page in a fresh context with template cookies and resource blocking applied. */
async function openPage(browser, template, blockMedia) {
  const context = await browser.newContext();
  try {
    if (template?.cookies?.length) await context.addCookies(template.cookies);
    const page = await context.newPage();
    const blocked = new Set(template?.block_resources ?? DEFAULT_BLOCKED_RESOURCES);
    if (blockMedia && blocked.size > 0) {
      await page.route("**/*", (route) =>
        blocked.has(route.request().resourceType()) ? route.abort() : route.continue(),
      );
    }
    return { context, page };
  } catch (err) {
    await context.close();
    throw err;
  }
}

async function fetchHtmlOnce(browserManager, url, template, blockMedia) {
  const { context, page } = await openPage(await browserManager.get(), template, blockMedia);
  try {
    // Check the status before waiting for rendering so a slow page cannot hide an HTTP error.
    const response = await page.goto(url, {
      waitUntil: "domcontentloaded",
      timeout: NAVIGATION_TIMEOUT_MS,
    });
    const status = response?.status() ?? 0;
    if (status >= 400) {
      const retryAfter =
        status === 429 ? parseRetryAfterMs(response.headers()["retry-after"]) : null;
      throw new HttpStatusError(status, url, retryAfter);
    }
    try {
      await page.waitForLoadState("networkidle", { timeout: NETWORK_IDLE_TIMEOUT_MS });
    } catch (err) {
      // Long polling must not prevent extraction of an already loaded document.
      if (err.name !== "TimeoutError") throw err;
    }
    const html = await page.content();
    if (isAccessDenied(cheerio.load(html))) {
      throw new Error(`Access denied: CAPTCHA or block page detected at ${url}.`);
    }
    return html;
  } finally {
    await context.close();
  }
}

/** Render `url` and return its HTML, retrying rate limits and transient failures once. */
export async function fetchHtml(browserManager, url, template, blockMedia) {
  validateUrl(url);
  for (let attempt = 1; ; attempt++) {
    try {
      return await fetchHtmlOnce(browserManager, url, template, blockMedia);
    } catch (err) {
      const delay = retryDelayMs(err);
      if (attempt >= MAX_ATTEMPTS || delay === null) throw err;
      await sleep(delay);
    }
  }
}

/**
 * Best-effort fetch of a raw Markdown source (e.g. `page.md`). Returns the cleaned Markdown, or
 * null when the source is missing, is HTML, or does not look like Markdown.
 */
export async function fetchSourceMarkdown(browserManager, url, template, blockMedia) {
  validateUrl(url);
  let opened;
  try {
    opened = await openPage(await browserManager.get(), template, blockMedia);
    const response = await opened.page.goto(url, {
      waitUntil: "domcontentloaded",
      timeout: SOURCE_TIMEOUT_MS,
    });
    if (!response || response.status() >= 400) return null;
    const contentType = (response.headers()["content-type"] ?? "").toLowerCase();
    if (contentType.includes("text/html") || contentType.includes("application/xhtml+xml")) {
      return null;
    }
    const text = cleanSourceMarkdown(await response.text());
    return isMarkdown(text) ? text : null;
  } catch {
    return null;
  } finally {
    await opened?.context.close();
  }
}
