#!/usr/bin/env node

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
process.on("SIGINT", cleanup);
process.on("SIGTERM", cleanup);

function getGoogleRegionParams(region) {
  if (!region || region === "wt-wt") return "hl=en&gl=us";
  const parts = region.split("-");
  if (parts.length === 2) return `gl=${parts[0]}&hl=${parts[1]}`;
  return `gl=${region}&hl=en`;
}

async function executeSearch(query, maxResults, region, safeSearch, engine) {
  const browser = await browserManager.getBrowser();
  const context = await browser.newContext();

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
    await page.route("**/*", (route) => {
      const type = route.request().resourceType();
      if (["image", "media", "font", "stylesheet"].includes(type)) {
        route.abort();
      } else {
        route.continue();
      }
    });

    const results = [];
    const searchUrl =
      engine === "google"
        ? `https://www.google.com/search?udm=web&udm=14&q=${encodeURIComponent(query)}&${getGoogleRegionParams(region)}`
        : `https://html.duckduckgo.com/html/?q=${encodeURIComponent(query)}&kl=${encodeURIComponent(region)}&kp=${encodeURIComponent(safeSearch)}`;

    try {
      await page.goto(searchUrl, { waitUntil: "networkidle", timeout: 15000 });
    } catch (e) {
      // Allow partial rendering on timeout
    }

    const pageContent = await page.content();
    const $ = cheerio.load(pageContent);

    if (engine === "google") {
      $("h3").each((i, el) => {
        if (results.length >= maxResults) return;

        const h3 = $(el);
        let linkEl = h3.closest("a");
        if (!linkEl.length) linkEl = h3.find("a");
        if (!linkEl.length) return;

        let link = linkEl.attr("href") || "";

        if (!link || (link.startsWith("/") && !link.startsWith("/url?q=")))
          return;
        if (
          link.includes("google.com/search") ||
          link.includes("support.google.com")
        )
          return;

        if (link.startsWith("/url?q=")) {
          try {
            link = decodeURIComponent(link.split("/url?q=")[1].split("&")[0]);
          } catch (e) {}
        }

        const title = h3.text().trim();
        if (!title) return;

        let snippet = "";
        let parent = h3.parent();
        while (parent.length && parent.prop("tagName") !== "BODY") {
          const snippetEl = parent.find(
            "div.VwiC3b, div[style*='-webkit-line-clamp'], div.yXK7lf, div.Uroaid",
          );
          if (snippetEl.length) {
            snippet = snippetEl.first().text().replace(/\s+/g, " ").trim();
            break;
          }
          parent = parent.parent();
        }

        if (link.startsWith("http")) {
          if (!results.some((r) => r.link === link)) {
            results.push({
              position: results.length + 1,
              title,
              link,
              snippet,
            });
          }
        }
      });
    } else {
      $(".result").each((i, el) => {
        if (results.length >= maxResults) return;
        const titleEl = $(el).find(".result__title a");
        let link = titleEl.attr("href") || "";

        if (link.includes("/l/?uddg=")) {
          try {
            const urlParams = new URLSearchParams(link.split("?")[1]);
            link = decodeURIComponent(urlParams.get("uddg") || link);
          } catch (e) {}
        }

        const title = titleEl.text().trim();
        const snippet = $(el)
          .find(".result__snippet")
          .text()
          .replace(/\s+/g, " ")
          .trim();

        if (title && link.startsWith("http")) {
          results.push({ position: results.length + 1, title, link, snippet });
        }
      });
    }

    if (results.length === 0) {
      return `No results found on ${engine}. The engine may have shown a captcha, or the query returned nothing.`;
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
      // Allow partial rendering on timeout
    }

    const pageContent = await page.content();
    let finalContent = "";

    if (format === "raw_html") {
      finalContent = pageContent;
    } else {
      const $ = cheerio.load(pageContent);

      $(
        "script, style, nav, header, footer, noscript, iframe, svg, aside, .advertisement, img, picture, video, audio, canvas, map, area, dialog",
      ).remove();
      $("*")
        .removeAttr("style")
        .each((i, el) => {
          const src = $(el).attr("src");
          if (src && src.startsWith("data:image")) $(el).removeAttr("src");
        });

      if (format === "clean_html") {
        finalContent = $.html();
      } else {
        const turndownService = new TurndownService({
          headingStyle: "atx",
          codeBlockStyle: "fenced",
        });
        finalContent = turndownService
          .turndown($.html())
          .replace(/\n{3,}/g, "\n\n")
          .trim();
      }
    }

    const totalLength = finalContent.length;
    let paginatedText = finalContent.substring(
      startIndex,
      startIndex + maxLength,
    );

    let metadata = `\n\n---\n[Document Info: Showing characters ${startIndex} to ${startIndex + paginatedText.length} of ${totalLength} total.`;
    if (startIndex + maxLength < totalLength) {
      metadata += ` Use start_index=${startIndex + maxLength} to read more.`;
    }
    metadata += `]`;

    return paginatedText + metadata;
  } finally {
    await page.close();
    await context.close();
  }
}

const server = new McpServer({ name: "searchfetch", version: "2.0.0" });

server.registerTool(
  "websearch",
  {
    title: "Web Search",
    description:
      "Search the web using DuckDuckGo or Google. Returns a clean list of titles, URLs, and snippets. Excellent for researching general knowledge, news, and finding URLs.",
    inputSchema: z.object({
      query: z.string().describe("The search query string."),
      engine: z
        .enum(["duckduckgo", "google"])
        .default("duckduckgo")
        .describe(
          "Search engine to use. Can be 'duckduckgo' or 'google'. Default is 'duckduckgo'.",
        ),
      max_results: z
        .number()
        .default(10)
        .describe("Maximum number of results to return. Default is 10."),
      region: z
        .string()
        .default("wt-wt")
        .describe(
          "Region and language code to localize search results (e.g., 'us-en', 'uk-en', 'de-de'). For DuckDuckGo it maps directly. For Google, 'us' is country code and 'en' is language. Default is 'wt-wt' (global/US English).",
        ),
      safe_search: z
        .string()
        .default("-1")
        .describe(
          "Safe search filtering mode. '-1' for Moderate, '1' for Strict, '-2' for Off. Default is '-1'. Note: Only applies to DuckDuckGo.",
        ),
    }),
  },
  async ({ query, max_results, region, safe_search, engine }) => {
    try {
      const result = await executeSearch(
        query,
        max_results,
        region,
        safe_search,
        engine,
      );
      return { content: [{ type: "text", text: result }] };
    } catch (err) {
      return {
        content: [{ type: "text", text: `Search Error: ${err.message}` }],
        isError: true,
      };
    }
  },
);

server.registerTool(
  "webfetch",
  {
    title: "Web Fetch",
    description:
      "Fetch and extract the main text content from any webpage. Fully executes JavaScript to load React/SPAs and aggressively strips images/media (including base64) to save context tokens.",
    inputSchema: z.object({
      url: z
        .url()
        .describe(
          "The full URL of the webpage to fetch (must start with http/https).",
        ),
      format: z
        .enum(["markdown", "clean_html", "raw_html"])
        .default("markdown")
        .describe(
          "Output format. Set to 'markdown', 'clean_html', or 'raw_html'. Default is 'markdown' (highly recommended to save context tokens).",
        ),
      start_index: z
        .number()
        .default(0)
        .describe(
          "Character offset to start reading from for pagination. Use this if a document is too large to fit in the context window. Default is 0.",
        ),
      max_length: z
        .number()
        .default(10000)
        .describe(
          "Maximum characters to return per request. Default is 10000.",
        ),
      block_media: z
        .boolean()
        .default(true)
        .describe(
          "Block images, videos, and fonts entirely at the network layer to drastically speed up page loads and dodge tracking pixels. Default is true.",
        ),
    }),
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
    } catch (err) {
      return {
        content: [{ type: "text", text: `Fetch Error: ${err.message}` }],
        isError: true,
      };
    }
  },
);

async function main() {
  await ensureBinary();
  process.stdout.write = originalStdoutWrite;
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((err) => {
  process.exit(1);
});
