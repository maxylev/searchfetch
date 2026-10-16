/** MCP server exposing the `websearch` and `webfetch` tools. Mirrors `searchfetch/server.py`. */
import { readFileSync } from "node:fs";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import * as cheerio from "cheerio";
import { z } from "zod";
import { BrowserManager, fetchHtml, fetchSourceMarkdown } from "./browser.js";
import { NO_CONTENT, composeSearchResults, composeSections, paginate } from "./compose.js";
import { FALLBACK_REMOVE, cleanDocument, extractTemplate } from "./extract.js";
import { htmlToMarkdown, resolveSourceUrl } from "./markdown.js";
import { BUILTIN_TEMPLATES, resolvePageTemplate, resolveSearchCandidates } from "./templates.js";

export const VERSION = JSON.parse(
  readFileSync(new URL("../package.json", import.meta.url), "utf8"),
).version;

const PAGE_TEMPLATES = [...BUILTIN_TEMPLATES.values()]
  .filter((template) => !template.url_template)
  .map((template) => template.name);

const TOOL_ANNOTATIONS = { readOnlyHint: true, openWorldHint: true };

const blockMediaSchema = z
  .boolean()
  .default(true)
  .describe("Block images, media and fonts at the network layer. Default: true.");

const websearchSchema = z.object({
  query: z.string().trim().min(1).describe("The search query."),
  engine: z
    .string()
    .default("duckduckgo")
    .describe(
      "'duckduckgo' (default), 'google', a search template name, or an inline JSON search " +
        "template. Built-in engines fall back to DuckDuckGo when unavailable.",
    ),
  region: z
    .string()
    .nullable()
    .default(null)
    .describe(
      "Region-language code such as 'us-en' or 'de-de'. DuckDuckGo uses it as-is; Google maps " +
        "it to country (gl) and language (hl). Default: engine default.",
    ),
  safe_search: z
    .boolean()
    .nullable()
    .default(null)
    .describe("Enable (true) or disable (false) safe search. Default: engine default."),
  max_results: z.int().positive().default(10).describe("Maximum results to return. Default: 10."),
  block_media: blockMediaSchema,
});

const webfetchSchema = z.object({
  url: z
    .url({ protocol: /^https?$/, hostname: z.regexes.hostname })
    .describe("Absolute http(s) URL of the page to fetch."),
  template: z
    .string()
    .default("auto")
    .describe(
      `'auto' (detect from the URL), a template name (${PAGE_TEMPLATES.join(", ")}), or an ` +
        "inline JSON template. 'raw' converts the whole page with minimal filtering.",
    ),
  start_index: z
    .int()
    .nonnegative()
    .default(0)
    .describe("Offset in characters (Unicode code points) for pagination. Default: 0."),
  max_length: z
    .int()
    .positive()
    .default(10_000)
    .describe("Maximum characters to return. Default: 10000."),
  block_media: blockMediaSchema,
});

const text = (value) => ({ content: [{ type: "text", text: value }] });
const failure = (prefix, err) => ({ ...text(`${prefix}: ${err.message}`), isError: true });

export async function websearch(browser, args) {
  const { query, engine, region, safe_search, max_results, block_media } =
    websearchSchema.parse(args);
  const failures = [];
  for (const { template, url } of resolveSearchCandidates(engine, query, region, safe_search)) {
    try {
      const html = await fetchHtml(browser, url, template, block_media);
      const sections = extractTemplate(cheerio.load(html), template, {
        pageUrl: url,
        maxResults: max_results,
      });
      const results = sections.find((section) => section.type === "records");
      const output = results ? composeSearchResults(results) : NO_CONTENT;
      if (output === NO_CONTENT) throw new Error("no results extracted");
      if (failures.length === 0) return output;
      return `[websearch: ${failures.join("; ")}. Results are from ${template.name}.]\n\n${output}`;
    } catch (err) {
      failures.push(`${template.name} failed: ${err.message}`);
    }
  }
  throw new Error(failures.join("; "));
}

export async function webfetch(browser, args) {
  const {
    url,
    template: templateArg,
    start_index,
    max_length,
    block_media,
  } = webfetchSchema.parse(args);
  const template = resolvePageTemplate(url, templateArg);
  const templateName = template?.name ?? "auto (fallback)";

  if (template?.source_url) {
    const sourceUrl = resolveSourceUrl(template.source_url, url);
    const markdown = await fetchSourceMarkdown(browser, sourceUrl, template, block_media);
    if (markdown !== null) return paginate(markdown, templateName, start_index, max_length);
  }

  const $ = cheerio.load(await fetchHtml(browser, url, template, block_media));
  let output;
  if (template) {
    output = composeSections(extractTemplate($, template, { pageUrl: url }));
  } else {
    cleanDocument($, FALLBACK_REMOVE);
    output = htmlToMarkdown($("body").html() ?? $.html()) || NO_CONTENT;
  }
  return paginate(output, templateName, start_index, max_length);
}

/** Create the MCP server. The caller owns `browser` and must close it on shutdown. */
export function createServer(browser = new BrowserManager()) {
  const server = new McpServer({ name: "searchfetch", version: VERSION });

  server.registerTool(
    "websearch",
    {
      title: "Web Search",
      description:
        "Search the web with DuckDuckGo or Google and return numbered results with titles, " +
        "URLs and snippets.",
      inputSchema: websearchSchema,
      annotations: TOOL_ANNOTATIONS,
    },
    async (args) => {
      try {
        return text(await websearch(browser, args));
      } catch (err) {
        return failure("Search Error", err);
      }
    },
  );

  server.registerTool(
    "webfetch",
    {
      title: "Web Fetch",
      description:
        "Fetch a web page in a JavaScript-capable stealth browser and return its main content " +
        "as Markdown, with images, scripts and styles stripped. Long output is paginated.",
      inputSchema: webfetchSchema,
      annotations: TOOL_ANNOTATIONS,
    },
    async (args) => {
      try {
        return text(await webfetch(browser, args));
      } catch (err) {
        return failure("Fetch Error", err);
      }
    },
  );

  return server;
}
