/** HTML-to-Markdown conversion and detection of raw Markdown sources. */
import TurndownService from "turndown";

const turndown = new TurndownService({
  headingStyle: "atx",
  codeBlockStyle: "fenced",
  bulletListMarker: "-",
  emDelimiter: "*",
});

export const collapseBlankLines = (text) => text.replace(/\n{3,}/g, "\n\n").trim();

export function htmlToMarkdown(html) {
  return collapseBlankLines(turndown.turndown(html));
}

const HTML_TAG = /<\w+[^>]*>/g;
const MARKDOWN_SIGNALS = [
  /^#{1,6}\s+\S/m,
  /\[.+?\]\(.+?\)/,
  /```\w*\n/,
  /^\s*[-*+]\s+\S/m,
  /\*\*[^*]+\*\*/,
  /^>\s+\S/m,
];

/** Heuristic: text with Markdown syntax and at most three HTML tags. */
export function isMarkdown(text) {
  if (!text || (text.match(HTML_TAG) ?? []).length > 3) return false;
  return MARKDOWN_SIGNALS.some((pattern) => pattern.test(text));
}

/** Drop build-tool artifacts (e.g. Twoslash caches) and redundant blank lines. */
export function cleanSourceMarkdown(text) {
  return collapseBlankLines(text.replace(/^@twoslash-cache:.*$/gm, ""));
}

/**
 * Resolve a template `source_url`. `{url}.md` appends `.md` to the path (keeping the query and
 * dropping the fragment); any other `{url}` placeholder is replaced verbatim.
 */
export function resolveSourceUrl(sourceTemplate, url) {
  if (sourceTemplate === "{url}.md") {
    const parsed = new URL(url);
    parsed.pathname = `${parsed.pathname.replace(/\/+$/, "")}.md`;
    parsed.hash = "";
    return parsed.href;
  }
  return sourceTemplate.replaceAll("{url}", url);
}
