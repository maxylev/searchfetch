#!/usr/bin/env node

// === IMPORTS =============================================================
import { readdirSync, readFileSync, realpathSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { launch, ensureBinary } from "cloakbrowser";
import * as cheerio from "cheerio";
import TurndownService from "turndown";

// === BROWSER MANAGER =====================================================
class BrowserManager {
  constructor() {
    this.browser = null;
    this.launchPromise = null;
  }

  async getBrowser() {
    if (this.browser && this.browser.isConnected()) return this.browser;
    if (this.launchPromise) return this.launchPromise;

    this.launchPromise = launch({
      headless: true,
      humanize: true,
      args: [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
      ],
    })
      .then((browser) => {
        this.browser = browser;
        this.browser.on("disconnected", () => {
          this.browser = null;
        });
        return browser;
      })
      .catch((err) => {
        throw err;
      })
      .finally(() => {
        this.launchPromise = null;
      });

    return this.launchPromise;
  }

  async close() {
    if (this.browser) {
      await this.browser.close();
      this.browser = null;
    }
  }
}

const browserManager = new BrowserManager();

const cleanup = async () => {
  await browserManager.close();
  process.exit(0);
};

// === TURNDOWN ============================================================
const turndown = new TurndownService({
  headingStyle: "atx",
  codeBlockStyle: "fenced",
  emDelimiter: "*",
});

// === BUILT-IN TEMPLATES (loaded from templates/*.json) ====================

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const TEMPLATES_DIR = join(__dirname, "templates");

function loadBuiltinTemplates() {
  let files;
  try {
    files = readdirSync(TEMPLATES_DIR);
  } catch (err) {
    throw new Error(`Cannot read templates directory '${TEMPLATES_DIR}': ${err.message}`);
  }

  const jsonFiles = files.filter((f) => f.endsWith(".json")).sort();

  if (jsonFiles.length === 0) {
    throw new Error(`No template JSON files found in '${TEMPLATES_DIR}'`);
  }

  const templates = [];
  for (const file of jsonFiles) {
    const filePath = join(TEMPLATES_DIR, file);
    const content = readFileSync(filePath, "utf-8");
    let template;
    try {
      template = JSON.parse(content);
    } catch (err) {
      throw new Error(`Invalid JSON in template file '${filePath}': ${err.message}`);
    }
    if (!template.name || typeof template.name !== "string") {
      throw new Error(`Template file '${filePath}' is missing a valid "name" field`);
    }
    templates.push(template);
  }

  // Sort by "order" field for deterministic URL-pattern matching
  templates.sort((a, b) => (a.order ?? 999) - (b.order ?? 999));

  return templates;
}

const BUILTIN_TEMPLATES = loadBuiltinTemplates();

// === TEMPLATE LOOKUP =====================================================
const TEMPLATE_MAP = new Map();
for (const t of BUILTIN_TEMPLATES) {
  TEMPLATE_MAP.set(t.name, t);
}

function getTemplateByName(name) {
  const t = TEMPLATE_MAP.get(name);
  if (!t) {
    const names = [...TEMPLATE_MAP.keys()].join(", ");
    throw new Error(`Unknown template '${name}'. Available: ${names}`);
  }
  return t;
}

function detectTemplateByUrl(url) {
  for (const template of BUILTIN_TEMPLATES) {
    if (!template.url_patterns) continue;
    for (const pattern of template.url_patterns) {
      try {
        if (new RegExp(pattern).test(url)) {
          return template;
        }
      } catch (_invalidRegex) {
        // Invalid URL patterns are tolerated — templates may contain
        // broad patterns that don't compile correctly in every context.
      }
    }
  }
  return null;
}

// === URL TEMPLATE RESOLUTION =============================================

function resolveUrlTemplate(template, providedParams) {
  const urlParams = template.url_params || {};
  let url = template.url_template;
  if (!url) return null;

  let match;
  const re = /\{(\w+)\}/g;
  while ((match = re.exec(url)) !== null) {
    const name = match[1];
    const def = urlParams[name] || {};

    let value;
    if (
      name in providedParams &&
      providedParams[name] !== null &&
      providedParams[name] !== undefined
    ) {
      value = String(providedParams[name]);
    } else if (def.default !== undefined) {
      value = String(def.default);
    } else if (def.required) {
      throw new Error(
        `Required URL parameter '${name}' not provided for template '${template.name}'.`,
      );
    } else {
      value = "";
    }

    if (def.encode === "url") {
      value = encodeURIComponent(value);
    }

    url = url.replace(match[0], value);
  }

  // Remove any remaining unreplaced placeholders
  url = url
    .replace(/\{\w+\}/g, "")
    .replace(/&{2,}/g, "&")
    .replace(/\?&/, "?");

  return url;
}

// === SEARCH PARAM MAPPING ================================================

function resolveEngineToTemplateName(engine) {
  if (engine === "duckduckgo") return "duckduckgo-search";
  if (engine === "google") return "google-search";
  return engine;
}

function mapSearchParams(engine, query, region, safeSearch) {
  const params = { query };
  const resolved = resolveEngineToTemplateName(engine);

  if (resolved === "duckduckgo-search") {
    if (region !== null && region !== undefined) {
      params.kl = region;
    }
    if (safeSearch === true) {
      params.kp = "1";
    } else if (safeSearch === false) {
      params.kp = "-2";
    }
  } else if (resolved === "google-search") {
    if (region !== null && region !== undefined) {
      const parts = region.split("-");
      params.hl = parts[0];
      params.gl = parts.length > 1 ? parts[1] : parts[0];
    }
  }

  return params;
}

// === FETCH ===============================================================

const FETCH_MAX_ATTEMPTS = 2;
const HTTP_429_RETRY_DELAY_MS = 2000;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function parseRetryAfterMs(value) {
  if (!value) return HTTP_429_RETRY_DELAY_MS;
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) {
    return Math.min(seconds * 1000, 30000);
  }
  const dateMs = Date.parse(value);
  if (Number.isFinite(dateMs)) {
    return Math.min(Math.max(dateMs - Date.now(), 0), 30000);
  }
  return HTTP_429_RETRY_DELAY_MS;
}

function makeHttpStatusError(status, url, retryAfterMs = null) {
  const err = new Error(`Access denied: HTTP ${status} when fetching ${url}`);
  err.httpStatus = status;
  err.retryAfterMs = retryAfterMs;
  return err;
}

function isAccessDenied($) {
  const title = ($("title").text() || "").toLowerCase();
  const bodyText = ($("body").text() || "").replace(/\s+/g, " ").trim().toLowerCase();

  const titleDenyPatterns = [
    "captcha",
    "are you a robot",
    "access denied",
    "blocked",
    "forbidden",
    "unusual traffic",
    "sorry, you have been blocked",
    "verify you are human",
    "one more step",
    "security check",
    "ddos protection",
    "cloudflare",
  ];

  if (titleDenyPatterns.some((pattern) => title.includes(pattern))) return true;

  const bodyDenyPatterns = [
    "to continue, please type the characters",
    "our systems have detected unusual traffic",
    "verify you are human",
    "are you a robot",
    "sorry, you have been blocked",
    "access denied",
  ];

  if (bodyText.length < 1200 && bodyDenyPatterns.some((pattern) => bodyText.includes(pattern)))
    return true;

  return false;
}

async function fetchHtml(url, template, blockMedia) {
  const browser = await browserManager.getBrowser();
  const context = await browser.newContext();

  try {
    // Pre-load cookies from template
    if (template && template.cookies && template.cookies.length > 0) {
      await context.addCookies(template.cookies);
    }

    const page = await context.newPage();

    try {
      // Route blocked resource types
      if (blockMedia) {
        const blockedTypes =
          template && template.block_resources
            ? template.block_resources
            : ["image", "media", "font"];

        if (blockedTypes.length > 0) {
          await page.route("**/*", (route) => {
            const type = route.request().resourceType();
            if (blockedTypes.includes(type)) {
              route.abort();
            } else {
              route.continue();
            }
          });
        }
      }

      let response;
      try {
        response = await page.goto(url, {
          waitUntil: "networkidle",
          timeout: 15000,
        });
      } catch (_navError) {
        // Allow partial rendering on timeout
      }

      // Check HTTP status for access failures
      if (response) {
        const status = response.status();
        if ([401, 403, 429].includes(status)) {
          throw makeHttpStatusError(
            status,
            url,
            status === 429 ? parseRetryAfterMs(response.headers()["retry-after"]) : null,
          );
        }
      }

      const pageContent = await page.content();

      // Check for CAPTCHA / access-denied pages
      const $ = cheerio.load(pageContent);
      if (isAccessDenied($)) {
        throw new Error(
          `Access denied: CAPTCHA or block page detected at ${url}. The site is blocking automated access.`,
        );
      }

      return pageContent;
    } finally {
      await page.close();
    }
  } finally {
    await context.close();
  }
}

async function fetchHtmlWithRetry(url, template, blockMedia) {
  let lastError;
  for (let attempt = 0; attempt < FETCH_MAX_ATTEMPTS; attempt++) {
    try {
      return await fetchHtml(url, template, blockMedia);
    } catch (err) {
      lastError = err;
      if (attempt < FETCH_MAX_ATTEMPTS - 1 && err.httpStatus === 429) {
        await sleep(err.retryAfterMs ?? HTTP_429_RETRY_DELAY_MS);
        continue;
      }
      if (
        attempt < FETCH_MAX_ATTEMPTS - 1 &&
        (err.message.includes("net::") ||
          err.message.includes("ERR_") ||
          err.message.includes("Navigation failed"))
      ) {
        await sleep(500);
        continue;
      }
      throw err;
    }
  }
  throw lastError;
}

// === SOURCE MARKDOWN DETECTION ===========================================

function isMarkdownContent(text) {
  if (!text) return false;
  const htmlTagCount = (text.match(/<\w+[^>]*>/g) || []).length;
  if (htmlTagCount > 3) return false;
  const patterns = [
    /^#{1,6}\s+\S/m,
    /\[.+?\]\(.+?\)/,
    /```\w*\n/,
    /^\s*[-*+]\s+\S/m,
    /\*\*[^*]+\*\*/,
    /^>\s+\S/m,
  ];
  for (const pat of patterns) {
    if (pat.test(text)) return true;
  }
  return false;
}

function stripSourceMarkdown(content) {
  return content
    .replace(/^@twoslash-cache:.*$/gm, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function resolveSourceUrl(sourceTemplate, url) {
  if (sourceTemplate === "{url}.md") {
    return `${url.replace(/\/+$/, "")}.md`;
  }
  return sourceTemplate.replace("{url}", url);
}

async function fetchSourceMarkdown(sourceUrl, template, blockMedia) {
  const browser = await browserManager.getBrowser();
  const context = await browser.newContext();

  try {
    if (template && template.cookies && template.cookies.length > 0) {
      await context.addCookies(template.cookies);
    }

    const page = await context.newPage();
    try {
      if (blockMedia) {
        const blockedTypes =
          template && template.block_resources
            ? template.block_resources
            : ["image", "media", "font"];
        if (blockedTypes.length > 0) {
          await page.route("**/*", (route) => {
            const type = route.request().resourceType();
            if (blockedTypes.includes(type)) route.abort();
            else route.continue();
          });
        }
      }

      let response;
      try {
        response = await page.goto(sourceUrl, {
          waitUntil: "domcontentloaded",
          timeout: 10000,
        });
      } catch (_) {
        return null;
      }

      if (response && response.status() >= 400) return null;

      let text;
      try {
        text = await page.evaluate(
          "() => document.body?.innerText || document.body?.textContent || ''",
        );
      } catch (_) {
        text = await page.content();
      }

      if (!text || typeof text !== "string") return null;

      text = stripSourceMarkdown(text.trim());
      return isMarkdownContent(text) ? text : null;
    } finally {
      await page.close();
    }
  } catch (_) {
    return null;
  } finally {
    await context.close();
  }
}

// === HTML CLEANUP ========================================================

const DEFAULT_REMOVE_SELECTORS = [
  "script",
  "style",
  "svg",
  "nav",
  "footer",
  "noscript",
  "iframe",
  ".advertisement",
];

function applyRemove($, template) {
  const removeSelectors =
    template && template.remove && template.remove.length > 0
      ? template.remove
      : DEFAULT_REMOVE_SELECTORS;

  for (const selector of removeSelectors) {
    try {
      $(selector).remove();
    } catch (_invalidSelector) {
      // Invalid remove selectors are tolerated — templates can express
      // broad cleanup rules that don't apply to every page.
    }
  }

  // Strip style attributes and data:image src
  $("[style]").removeAttr("style");
  $("*").each((_i, el) => {
    const src = $(el).attr("src");
    if (src && src.startsWith("data:image")) {
      $(el).removeAttr("src");
    }
  });
}

// === EXTRACTION ENGINE ===================================================

/**
 * Find elements matching selector, scoped to $parent.
 * Search order: descendants → closest ancestor → ancestor subtrees (up to 4 levels).
 */
function findScoped($parent, selector) {
  if (!selector || selector.trim() === "") {
    return $parent;
  }

  // 1. Descendants
  let result = $parent.find(selector);
  if (result.length > 0) return result;

  // 2. Closest ancestor matching selector
  result = $parent.closest(selector);
  if (result.length > 0) return result;

  // 3. Ancestor subtrees (up to 4 levels up)
  let ancestor = $parent.parent();
  for (let i = 0; i < 4 && ancestor.length > 0; i++) {
    result = ancestor.find(selector);
    if (result.length > 0) return result;
    ancestor = ancestor.parent();
  }

  return $parent.find("__nonexistent__");
}

/**
 * Try comma-separated selectors in order; first match wins.
 */
function findFirstMatch($parent, selectorStr) {
  if (!selectorStr || selectorStr.trim() === "") {
    return $parent;
  }

  const selectors = selectorStr
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

  for (const sel of selectors) {
    const matches = findScoped($parent, sel);
    if (matches.length > 0) return matches;
  }

  return $parent.find("__nonexistent__");
}

/**
 * Resolve top-level elements for a section (document-wide with fallback).
 */
function resolveTopElements($, selectorStr) {
  if (!selectorStr || selectorStr.trim() === "") {
    return $("body");
  }

  const selectors = selectorStr
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

  for (const sel of selectors) {
    try {
      const matches = $(sel);
      if (matches.length > 0) return matches;
    } catch (_) {
      // Skip invalid selectors
    }
  }

  return $();
}

// === TRANSFORMS ==========================================================

function applyTransform(value, transform, origin) {
  const transforms = Array.isArray(transform) ? transform : [transform];
  let result = value;

  for (const t of transforms) {
    if (!result) continue;
    switch (t) {
      case "strip":
        result = result.trim();
        break;

      case "decode_google_url":
        if (result.startsWith("/url?q=")) {
          try {
            const urlPart = result.split("/url?q=")[1].split("&")[0];
            result = decodeURIComponent(urlPart);
          } catch (_) {
            // Leave as-is
          }
        }
        break;

      case "decode_ddg_url":
        if (result.includes("/l/?uddg=")) {
          try {
            const queryString = result.split("?")[1] || "";
            const params = new URLSearchParams(queryString);
            const uddg = params.get("uddg");
            if (uddg) result = decodeURIComponent(uddg);
          } catch (_) {
            // Leave as-is
          }
        }
        break;

      case "json_parse":
        try {
          result = JSON.stringify(JSON.parse(result), null, 2);
        } catch (_) {
          // Leave as-is
        }
        break;

      case "resolve_href":
        if (origin && result.startsWith("/") && !result.startsWith("//")) {
          try {
            result = new URL(result, origin).href;
          } catch (_) {
            // Leave as-is
          }
        }
        break;
    }
  }

  return result;
}

// === EXTRACTION ==========================================================

function extractValue($el, section, origin) {
  let value;

  switch (section.format) {
    case "text":
      value = $el.text().replace(/\s+/g, " ").trim();
      break;

    case "markdown": {
      const html = $el.html() || "";
      value = turndown
        .turndown(html)
        .replace(/\n{3,}/g, "\n\n")
        .trim();
      break;
    }

    case "attribute":
      value = $el.attr(section.attribute) || "";
      break;

    case "html":
      value = $el.html() || "";
      break;

    default:
      value = $el.text().replace(/\s+/g, " ").trim();
  }

  if (section.transform && value) {
    value = applyTransform(value, section.transform, origin);
  }

  return value;
}

/**
 * Extract one child section, scoped to $parentEl.
 * Returns { type: "value", text } or null.
 */
function extractChildSection($, $parentEl, section, origin) {
  const elements = findFirstMatch($parentEl, section.selector);

  if (!elements || elements.length === 0) {
    if (section.required) {
      throw new Error(`Required section '${section.name}' not found on page.`);
    }
    return null;
  }

  const el = elements.eq(0);
  const value = extractValue(el, section, origin);
  return { type: "value", text: value };
}

/**
 * Extract a top-level section from the document.
 * Returns a SectionResult or null.
 */
function extractSection($, section, context) {
  const elements = resolveTopElements($, section.selector);

  if (!elements || elements.length === 0) {
    if (section.required) {
      throw new Error(`Required section '${section.name}' not found on page.`);
    }
    return null;
  }

  // Determine limit
  let limit = elements.length;
  if (section.multiple && section.max_items) {
    limit = Math.min(limit, section.max_items);
  }
  // Override max_items with max_results for first multiple+children section
  if (
    context.isWebsearch &&
    context.maxResultsOverride &&
    !context._maxResultsConsumed &&
    section.multiple &&
    section.children &&
    section.children.length > 0
  ) {
    limit = Math.min(limit, context.maxResultsOverride);
    context._maxResultsConsumed = true;
  }

  if (section.multiple) {
    const items = [];

    for (let i = 0; i < limit; i++) {
      const el = elements.eq(i);

      if (section.children && section.children.length > 0) {
        // Multiple parents, each with children
        const childValues = {};
        for (const child of section.children) {
          const cr = extractChildSection($, el, child, context.origin);
          if (cr && cr.type === "value" && cr.text !== null && cr.text !== undefined) {
            childValues[child.name] = cr.text;
          }
        }
        if (Object.keys(childValues).length > 0) {
          items.push(childValues);
        }
      } else {
        // Multiple parents, no children
        const value = extractValue(el, section, context.origin);
        if (value && value.trim()) {
          items.push(value.trim());
        }
      }
    }

    if (section.children && section.children.length > 0) {
      return { section, type: "children-multiple", items };
    } else {
      return { section, type: "list", items };
    }
  } else {
    // Single parent
    const el = elements.eq(0);

    if (section.children && section.children.length > 0) {
      // Single parent with children — parent format ignored
      const childValues = {};
      for (const child of section.children) {
        const cr = extractChildSection($, el, child, context.origin);
        if (cr && cr.type === "value" && cr.text !== null && cr.text !== undefined) {
          childValues[child.name] = cr.text;
        }
      }
      return { section, type: "children", items: childValues };
    } else {
      const value = extractValue(el, section, context.origin);
      return { section, type: "value", text: value };
    }
  }
}

function extractTemplate($, template, context) {
  const results = [];

  for (const section of template.sections) {
    try {
      const result = extractSection($, section, context);
      if (result !== null) {
        results.push(result);
      }
    } catch (err) {
      // Required-section errors must surface to the user.
      if (err.message && err.message.includes("Required section")) {
        throw err;
      }
      // Non-required extraction failures: selector mismatches or format
      // processing issues. Broad templates legitimately don't match every
      // section on every page — skip and continue.
    }
  }

  return results;
}

// === COMPOSITION: WEBFETCH ===============================================

function isCommentStyle(result) {
  if (!result.items || result.items.length === 0) return false;
  const first = result.items[0];
  const keys = Object.keys(first).map((k) => k.toLowerCase());
  return (
    (keys.includes("author") && (keys.includes("comment") || keys.includes("body"))) ||
    (keys.includes("user") && (keys.includes("comment") || keys.includes("body")))
  );
}

function composeSections(extracted, template, startIndex, maxLength) {
  const parts = [];

  for (const result of extracted) {
    if (result.type === "value") {
      const text = result.text;
      if (text && String(text).trim()) {
        parts.push(`## ${result.section.name}\n\n${String(text).trim()}`);
      }
    } else if (result.type === "list") {
      if (result.items && result.items.length > 0) {
        const listText = result.items.map((item) => `- ${item}`).join("\n");
        parts.push(`## ${result.section.name}\n\n${listText}`);
      }
    } else if (result.type === "children") {
      if (result.items && Object.keys(result.items).length > 0) {
        for (const [childName, value] of Object.entries(result.items)) {
          if (value && String(value).trim()) {
            parts.push(`## ${childName}\n\n${String(value).trim()}`);
          }
        }
      }
    } else if (result.type === "children-multiple") {
      if (result.items && result.items.length > 0) {
        if (isCommentStyle(result)) {
          const commentParts = [];
          for (const item of result.items) {
            const author = item["Author"] || item["author"] || item["User"] || item["user"] || "";
            const comment =
              item["Comment"] || item["Body"] || item["comment"] || item["body"] || "";
            if (author) {
              commentParts.push(`**${author}:**\n\n${comment}`);
            } else if (comment) {
              commentParts.push(comment);
            }
          }
          if (commentParts.length > 0) {
            parts.push(`## ${result.section.name}\n\n${commentParts.join("\n\n---\n\n")}`);
          }
        } else {
          const itemParts = [];
          for (const item of result.items) {
            const lines = [];
            for (const [key, value] of Object.entries(item)) {
              if (value && String(value).trim()) {
                lines.push(`    ${key}: ${String(value).trim()}`);
              }
            }
            if (lines.length > 0) itemParts.push(lines.join("\n"));
          }
          if (itemParts.length > 0) {
            parts.push(`## ${result.section.name}\n\n${itemParts.join("\n\n")}`);
          }
        }
      }
    }
  }

  if (parts.length === 0) {
    return "(No content extracted from this page.)";
  }

  const full = parts.join("\n\n---\n\n");
  const totalLength = full.length;
  const paginated = full.substring(startIndex, startIndex + maxLength);

  const templateName = template ? template.name : "auto";
  let metadata = `\n\n---\n[webfetch: template="${templateName}", showing characters ${startIndex} to ${startIndex + paginated.length} of ${totalLength} total.`;
  if (startIndex + maxLength < totalLength) {
    metadata += ` Use start_index=${startIndex + maxLength} to read more.`;
  }
  metadata += `]`;

  return paginated + metadata;
}

// === COMPOSITION: WEBSEARCH ==============================================

function composeSearchResults(extracted) {
  // Find the search results section (first children-multiple)
  const searchSection = extracted.find((r) => r.type === "children-multiple");

  if (!searchSection || !searchSection.items || searchSection.items.length === 0) {
    // Fall back to section-based output
    return composeSections(extracted, null, 0, Infinity);
  }

  const items = searchSection.items;
  const parts = [];

  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    const num = i + 1;

    const title = item["Title"] || item["title"] || Object.values(item)[0] || "";
    const url = item["URL"] || item["url"] || item["Url"] || "";
    const snippet = item["Snippet"] || item["snippet"] || "";

    // Filter out non-http URLs and google internal links
    let cleanUrl = url;
    if (cleanUrl && !cleanUrl.startsWith("http")) {
      cleanUrl = ""; // Skip internal/non-web URLs
    }
    if (
      cleanUrl &&
      (cleanUrl.includes("google.com/search") || cleanUrl.includes("support.google.com"))
    ) {
      cleanUrl = ""; // Skip google internal links
    }

    if (!title) continue;

    const lines = [`[${num}] ${title}`];
    if (cleanUrl) lines.push(`    URL: ${cleanUrl}`);
    if (snippet) lines.push(`    Snippet: ${snippet}`);

    parts.push(lines.join("\n"));
  }

  if (parts.length === 0) {
    return "(No content extracted from this page.)";
  }

  return `## ${searchSection.section.name}\n\n${parts.join("\n\n")}`;
}

// === GENERIC FALLBACK ====================================================

function genericFallback($, startIndex, maxLength) {
  applyRemove($, null);

  const bodyHtml = $("body").html() || "";
  let markdown = turndown
    .turndown(bodyHtml)
    .replace(/\n{3,}/g, "\n\n")
    .trim();

  if (!markdown || markdown.trim().length === 0) {
    return "(No content extracted from this page.)";
  }

  const totalLength = markdown.length;
  const paginated = markdown.substring(startIndex, startIndex + maxLength);

  let metadata = `\n\n---\n[webfetch: template="auto (fallback)", showing characters ${startIndex} to ${startIndex + paginated.length} of ${totalLength} total.`;
  if (startIndex + maxLength < totalLength) {
    metadata += ` Use start_index=${startIndex + maxLength} to read more.`;
  }
  metadata += `]`;

  return paginated + metadata;
}

// === SEARCH TEMPLATE RESOLUTION ==========================================

function resolveSearchTemplate(engine, query, region, safeSearch) {
  const templateName = resolveEngineToTemplateName(engine);

  let template;
  if (templateName.startsWith("{")) {
    try {
      template = JSON.parse(templateName);
    } catch (e) {
      throw new Error(`Invalid inline JSON template: ${e.message}`);
    }
  } else {
    template = getTemplateByName(templateName);
  }

  if (!template.url_template) {
    throw new Error(`Template '${template.name}' is not a search template (no url_template).`);
  }

  const params = mapSearchParams(engine, query, region, safeSearch);
  let url = resolveUrlTemplate(template, params);

  // Google safe_search: append safe=active to URL
  if ((engine === "google" || templateName === "google-search") && safeSearch === true) {
    url += "&safe=active";
  }

  return { template, url };
}

// === MCP SERVER & TOOLS ==================================================

const server = new McpServer({ name: "searchfetch", version: "3.2.3" });

// --- websearch tool ---

server.registerTool(
  "websearch",
  {
    title: "Web Search",
    description:
      "Search the web using DuckDuckGo or Google. Returns a clean list of titles, URLs, and snippets. Excellent for researching general knowledge, news, and finding URLs.",
    inputSchema: z.object({
      query: z.string().describe("The search query string."),
      engine: z
        .string()
        .default("duckduckgo")
        .describe(
          "Search engine to use. Can be 'duckduckgo' or 'google'. Default is 'duckduckgo'.",
        ),
      region: z
        .string()
        .nullable()
        .default(null)
        .describe(
          "Region and language code to localize search results (e.g., 'us-en', 'uk-en', 'de-de'). For DuckDuckGo it maps directly. For Google, 'us' is country code and 'en' is language. Default is null (uses template default).",
        ),
      safe_search: z
        .boolean()
        .nullable()
        .default(null)
        .describe(
          "Enable safe search filtering. null = use template default. Applies to both DuckDuckGo and Google.",
        ),
      max_results: z
        .number()
        .default(10)
        .describe("Maximum number of search results to return. Default is 10."),
      block_media: z
        .boolean()
        .default(true)
        .describe(
          "Block images, videos, and fonts entirely at the network layer. Default is true.",
        ),
    }),
  },
  async ({ query, engine, region, safe_search, max_results, block_media }) => {
    try {
      // 1. Resolve search template (+ url_params mapping + url building)
      const { template, url } = resolveSearchTemplate(engine, query, region, safe_search);

      // 2. Fetch
      const html = await fetchHtmlWithRetry(url, template, block_media);

      // 3. Extract
      const $ = cheerio.load(html);
      applyRemove($, template);

      const pageOrigin = new URL(url).origin;
      const context = {
        origin: pageOrigin,
        isWebsearch: true,
        maxResultsOverride: max_results,
        _maxResultsConsumed: false,
      };

      const extracted = extractTemplate($, template, context);

      // 4. Compose
      const result = composeSearchResults(extracted);

      return { content: [{ type: "text", text: result }] };
    } catch (err) {
      return {
        content: [{ type: "text", text: `Search Error: ${err.message}` }],
        isError: true,
      };
    }
  },
);

// --- webfetch tool ---

server.registerTool(
  "webfetch",
  {
    title: "Web Fetch",
    description:
      "Fetch and extract the main text content from any webpage. Fully executes JavaScript to load React/SPAs and aggressively strips images/media (including base64) to save context tokens.",
    inputSchema: z.object({
      url: z
        .string()
        .describe("The full URL of the webpage to fetch (must start with http/https)."),
      template: z
        .string()
        .default("auto")
        .describe(
          "Template to use: 'auto' (auto-detect from URL), a built-in name, or inline JSON.",
        ),
      start_index: z.number().default(0).describe("Character offset for pagination. Default: 0."),
      max_length: z
        .number()
        .default(10000)
        .describe("Maximum characters to return per request. Default is 10000."),
      block_media: z
        .boolean()
        .default(true)
        .describe(
          "Block images, videos, and fonts entirely at the network layer. Default is true.",
        ),
    }),
  },
  async ({ url, template: templateParam, start_index, max_length, block_media }) => {
    try {
      // 1. Resolve template
      let template;

      if (templateParam.startsWith("{")) {
        try {
          template = JSON.parse(templateParam);
        } catch (e) {
          throw new Error(`Invalid inline JSON template: ${e.message}`);
        }
      } else if (templateParam === "auto") {
        template = detectTemplateByUrl(url);
      } else {
        template = getTemplateByName(templateParam);
      }

      // 2. Try source markdown if template specifies source_url
      let sourceMd = null;
      if (template && template.source_url) {
        const sourceUrl = resolveSourceUrl(template.source_url, url);
        sourceMd = await fetchSourceMarkdown(sourceUrl, template, block_media);
      }

      if (sourceMd !== null) {
        const totalLength = sourceMd.length;
        const paginated = sourceMd.substring(start_index, start_index + max_length);
        let metadata =
          `\n\n---\n[webfetch: template="${template ? template.name : "auto"}" (source markdown), ` +
          `showing characters ${start_index} to ${start_index + paginated.length} of ${totalLength} total.`;
        if (start_index + max_length < totalLength) {
          metadata += ` Use start_index=${start_index + max_length} to read more.`;
        }
        metadata += "]";
        return { content: [{ type: "text", text: paginated + metadata }] };
      }

      // 3. Fetch
      const html = await fetchHtmlWithRetry(url, template, block_media);

      // 4. Extract and compose
      const $ = cheerio.load(html);

      if (template) {
        applyRemove($, template);

        const pageOrigin = new URL(url).origin;
        const context = {
          origin: pageOrigin,
          isWebsearch: false,
        };

        const extracted = extractTemplate($, template, context);
        const result = composeSections(extracted, template, start_index, max_length);
        return { content: [{ type: "text", text: result }] };
      } else {
        // Generic fallback
        const result = genericFallback($, start_index, max_length);
        return { content: [{ type: "text", text: result }] };
      }
    } catch (err) {
      return {
        content: [{ type: "text", text: `Fetch Error: ${err.message}` }],
        isError: true,
      };
    }
  },
);

// === EXPORTS (for testing without starting server) =========================

export {
  loadBuiltinTemplates,
  getTemplateByName,
  detectTemplateByUrl,
  resolveUrlTemplate,
  resolveEngineToTemplateName,
  mapSearchParams,
  resolveSearchTemplate,
  isMarkdownContent,
  stripSourceMarkdown,
  resolveSourceUrl,
  parseRetryAfterMs,
  isAccessDenied,
  findScoped,
  findFirstMatch,
  resolveTopElements,
  applyTransform,
  extractValue,
  extractChildSection,
  extractSection,
  extractTemplate,
  composeSections,
  composeSearchResults,
  genericFallback,
  isMainModule,
  BUILTIN_TEMPLATES,
  TEMPLATE_MAP,
};

// === MAIN =================================================================

function redirectStartupOutputToStderr() {
  const originalStdoutWrite = process.stdout.write.bind(process.stdout);
  const originalConsoleLog = console.log;
  const originalConsoleInfo = console.info;

  process.stdout.write = (chunk, encoding, callback) => {
    return process.stderr.write(chunk, encoding, callback);
  };
  console.log = (...args) => console.error(...args);
  console.info = (...args) => console.error(...args);

  return () => {
    process.stdout.write = originalStdoutWrite;
    console.log = originalConsoleLog;
    console.info = originalConsoleInfo;
  };
}

function isMainModule(argvPath = process.argv[1], modulePath = __filename) {
  if (!argvPath) return false;
  try {
    return realpathSync(argvPath) === realpathSync(modulePath);
  } catch {
    return false;
  }
}

async function main() {
  process.on("SIGINT", cleanup);
  process.on("SIGTERM", cleanup);

  const restoreStdout = redirectStartupOutputToStderr();
  await ensureBinary();
  restoreStdout();
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

// Guard: only start server when run directly (including via npm bin symlink),
// not when imported. Uses realpath so that `npx` / `node_modules/.bin`
// symlinks resolve to the module file correctly.
const isMain = isMainModule();
if (isMain) {
  main().catch((_err) => {
    process.exit(1);
  });
}
