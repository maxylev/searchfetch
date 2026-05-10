#!/usr/bin/env node

// ==========================================
// 🛡️ STDOUT JAIL FOR MCP PROTOCOL SAFETY
// ==========================================
// We intercept all writes and redirect them to stderr until MCP is ready.
// This prevents installation logs from crashing the JSON-RPC stream.
const originalStdoutWrite = process.stdout.write.bind(process.stdout);
process.stdout.write = (chunk, encoding, callback) => {
  return process.stderr.write(chunk, encoding, callback);
};
console.log = (...args) => console.error(...args);
console.info = (...args) => console.error(...args);

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { launch, ensureBinary } from "cloakbrowser";
import * as cheerio from "cheerio";
import TurndownService from "turndown";

const logger = {
  info: (msg) => console.error(`[INFO] ${msg}`),
  warn: (msg) => console.error(`[WARN] ${msg}`),
  error: (msg, err) => console.error(`[ERROR] ${msg}`, err || ""),
};

// ==========================================
// BROWSER LIFECYCLE MANAGEMENT
// ==========================================
class BrowserManager {
  constructor() {
    this.browser = null;
  }

  async getBrowser() {
    if (!this.browser) {
      logger.info("Launching stealth CloakBrowser instance...");
      this.browser = await launch({
        headless: true,
        humanize: true, // Native C++ bot-bypass patches + human behavior
        args: ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
      });
    }
    return this.browser;
  }

  async close() {
    if (this.browser) {
      await this.browser.close();
      this.browser = null;
      logger.info("Browser instance securely closed.");
    }
  }
}

const browserManager = new BrowserManager();

const cleanup = async () => {
  logger.info("Received termination signal. Shutting down browser...");
  await browserManager.close();
  process.exit(0);
};
process.on("SIGINT", cleanup);
process.on("SIGTERM", cleanup);

// ==========================================
// CORE LOGIC: SEARCH & FETCH
// ==========================================

async function executeSearch(query, maxResults, region, safeSearch, engine) {
  logger.info(
    `Searching ${engine.toUpperCase()} via Stealth Browser for: "${query}"`,
  );

  const browser = await browserManager.getBrowser();
  const context = await browser.newContext();

  // Inject Google Consent cookie to universally bypass GDPR popups blocking the DOM
  await context.addCookies([
    {
      name: "CONSENT",
      value: "YES+cb.20250101-01-p0.en+FX+999",
      domain: ".google.com",
      path: "/",
    },
  ]);

  const page = await context.newPage();

  try {
    // Optimization: Block heavy/unnecessary resources to make searches lightning fast
    await page.route("**/*", (route) => {
      const type = route.request().resourceType();
      if (["image", "media", "font", "stylesheet"].includes(type)) {
        route.abort();
      } else {
        route.continue();
      }
    });

    const results = [];
    let searchUrl = "";

    if (engine === "google") {
      searchUrl = `https://www.google.com/search?q=${encodeURIComponent(query)}&hl=en&gl=us`;
    } else {
      searchUrl = `https://html.duckduckgo.com/html/?q=${encodeURIComponent(query)}&kl=${encodeURIComponent(region)}&kp=${encodeURIComponent(safeSearch)}`;
    }

    try {
      // Use networkidle to ensure JavaScript fully renders organic results or follows hidden redirects
      await page.goto(searchUrl, { waitUntil: "networkidle", timeout: 15000 });
    } catch (e) {
      if (e.name === "TimeoutError") {
        logger.warn(`Network idle timeout on search. Extracting loaded DOM...`);
      } else {
        throw e;
      }
    }

    const pageContent = await page.content();
    const $ = cheerio.load(pageContent);

    if (engine === "google") {
      // Google's core organic result selector
      $("div.g").each((i, el) => {
        if (results.length >= maxResults) return;

        const titleEl = $(el).find("h3").first();
        const linkEl = $(el).find("a").first();
        if (!titleEl.length || !linkEl.length) return;

        const title = titleEl.text().trim();
        let link = linkEl.attr("href") || "";

        // Handle Google relative redirect links
        if (link.startsWith("/url?q=")) {
          try {
            link = decodeURIComponent(link.split("/url?q=")[1].split("&")[0]);
          } catch (e) {}
        }

        // Isolate snippet text safely
        const cloned = $(el).clone();
        cloned.find("h3, a, script, style, cite").remove();
        const snippet = cloned.text().replace(/\s+/g, " ").trim();

        if (title && link && link.startsWith("http")) {
          results.push({ position: results.length + 1, title, link, snippet });
        }
      });
    } else {
      // DuckDuckGo selector
      $(".result").each((i, el) => {
        if (results.length >= maxResults) return;

        const titleEl = $(el).find(".result__title a");
        const snippetEl = $(el).find(".result__snippet");
        if (!titleEl.length) return;

        const title = titleEl.text().trim();
        let link = titleEl.attr("href") || "";

        if (link.includes("/l/?uddg=")) {
          try {
            const urlParams = new URLSearchParams(link.split("?")[1]);
            link = decodeURIComponent(urlParams.get("uddg") || link);
          } catch (e) {}
        }

        const snippet = snippetEl.text().replace(/\s+/g, " ").trim();
        if (title && link && link.startsWith("http")) {
          results.push({ position: results.length + 1, title, link, snippet });
        }
      });
    }

    if (results.length === 0) {
      const pageText = $("body").text().replace(/\s+/g, " ").substring(0, 300);
      logger.warn(`No results found. DOM Sample: ${pageText}`);
      return `No results found on ${engine}. The search engine might be showing a captcha/consent screen, or the query returned nothing. Try rephrasing or switching engines.`;
    }

    return (
      `Found ${results.length} search results on ${engine}:\n\n` +
      results
        .map(
          (r) =>
            `[${r.position}] ${r.title}\n    URL: ${r.link}\n    Summary: ${r.snippet}`,
        )
        .join("\n\n")
    );
  } finally {
    await page.close();
    await context.close();
  }
}

async function executeFetch(url, format, startIndex, maxLength, blockMedia) {
  logger.info(`Fetching URL: ${url} | Format: ${format}`);

  const browser = await browserManager.getBrowser();
  const context = await browser.newContext();
  const page = await context.newPage();

  try {
    if (blockMedia) {
      await page.route("**/*", (route) => {
        const type = route.request().resourceType();
        if (["image", "media", "font"].includes(type)) {
          route.abort();
        } else {
          route.continue();
        }
      });
    }

    try {
      await page.goto(url, { waitUntil: "networkidle", timeout: 15000 });
    } catch (navError) {
      if (navError.name === "TimeoutError") {
        logger.warn(
          `Network idle timeout on ${url}. Extracting partial DOM...`,
        );
      } else {
        throw navError;
      }
    }

    const pageContent = await page.content();
    let finalContent = "";

    if (format === "raw_html") {
      finalContent = pageContent;
    } else {
      const $ = cheerio.load(pageContent);

      // 🚀 NUCLEAR OPTION FOR BASE64 AND TOKENS 🚀
      // Physically scrub out all tags that harbor base64 strings or waste tokens
      $(
        "script, style, nav, header, footer, noscript, iframe, svg, aside, .advertisement, img, picture, video, audio, canvas, map, area, dialog",
      ).remove();

      // Remove inline styles from EVERY element to prevent background-image base64 leaks
      $("*").removeAttr("style");

      // Remove data URIs anywhere else in the document
      $("*").each((i, el) => {
        const src = $(el).attr("src");
        if (src && src.startsWith("data:image")) $(el).removeAttr("src");
      });

      if (format === "clean_html") {
        finalContent = $.html();
      } else if (format === "markdown") {
        const turndownService = new TurndownService({
          headingStyle: "atx",
          codeBlockStyle: "fenced",
        });
        finalContent = turndownService.turndown($.html());
        finalContent = finalContent.replace(/\n{3,}/g, "\n\n").trim();
      }
    }

    const totalLength = finalContent.length;
    let paginatedText = finalContent.substring(
      startIndex,
      startIndex + maxLength,
    );
    const isTruncated = startIndex + maxLength < totalLength;

    let metadata = `\n\n---\n[Document Info: Showing characters ${startIndex} to ${
      startIndex + paginatedText.length
    } of ${totalLength} total.`;

    if (isTruncated) {
      metadata += ` Use start_index=${startIndex + maxLength} to paginate and read more.`;
    }
    metadata += `]`;

    return paginatedText + metadata;
  } finally {
    await page.close();
    await context.close();
  }
}

// ==========================================
// MCP SERVER INIT & TOOL REGISTRATION
// ==========================================

const server = new McpServer({
  name: "searchfetch",
  version: "1.3.0",
});

server.tool(
  "websearch",
  "Search the web using DuckDuckGo or Google. Returns a clean list of titles, URLs, and snippets. Excellent for researching general knowledge, news, and finding URLs.",
  {
    query: z.string().describe("The search query string."),
    engine: z
      .enum(["duckduckgo", "google"])
      .default("duckduckgo")
      .describe("Search engine to use (default: duckduckgo)."),
    max_results: z
      .number()
      .default(10)
      .describe("Maximum number of results to return (default: 10)."),
    region: z
      .string()
      .default("wt-wt")
      .describe("Region code (e.g., 'us-en'). Only applies to DuckDuckGo."),
    safe_search: z
      .string()
      .default("-1")
      .describe(
        "'-1' for Moderate, '1' for Strict, '-2' for Off. Only applies to DuckDuckGo.",
      ),
  },
  async ({ query, engine, max_results, region, safe_search }) => {
    try {
      const result = await executeSearch(
        query,
        max_results,
        region,
        safe_search,
        engine,
      );
      return { content: [{ type: "text", text: result }] };
    } catch (error) {
      logger.error("Search Tool failed:", error);
      return {
        content: [{ type: "text", text: `Search Error: ${error.message}` }],
        isError: true,
      };
    }
  },
);

server.tool(
  "webfetch",
  "Fetch and extract the main text content from any webpage. Fully executes JavaScript to load React/SPAs and aggressively strips images/media (including base64) to save context tokens.",
  {
    url: z
      .string()
      .url()
      .describe(
        "The full URL of the webpage to fetch (must start with http/https).",
      ),
    format: z
      .enum(["markdown", "clean_html", "raw_html"])
      .default("markdown")
      .describe(
        "Output format. Markdown is highly recommended to save context tokens.",
      ),
    start_index: z
      .number()
      .default(0)
      .describe("Character offset to start reading from for pagination."),
    max_length: z
      .number()
      .default(10000)
      .describe("Maximum characters to return per request (default: 10000)."),
    block_media: z
      .boolean()
      .default(true)
      .describe(
        "Block images/videos/fonts to drastically speed up rendering (default: true).",
      ),
  },
  async ({ url, format, start_index, max_length, block_media }) => {
    try {
      const result = await executeFetch(
        url,
        format,
        start_index,
        max_length,
        block_media,
      );
      return { content: [{ type: "text", text: result }] };
    } catch (error) {
      logger.error(`Fetch Tool failed on ${url}:`, error);
      return {
        content: [{ type: "text", text: `Fetch Error: ${error.message}` }],
        isError: true,
      };
    }
  },
);

// ==========================================
// BOOTSTRAP
// ==========================================

async function main() {
  logger.info("Initializing MCP Server...");

  await ensureBinary();

  // Re-enable STDOUT right before protocol hook-in
  process.stdout.write = originalStdoutWrite;

  const transport = new StdioServerTransport();
  await server.connect(transport);

  logger.info("searchfetch successfully connected and listening for requests.");
}

main().catch((err) => {
  logger.error("Fatal error during startup:", err);
  process.exit(1);
});
