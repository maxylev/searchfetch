/**
 * Template-driven extraction from a parsed HTML document (cheerio).
 *
 * Mirrors `searchfetch/extract.py`; shared fixtures in `tests/fixtures/` keep them in sync.
 */
import { htmlToMarkdown } from "./markdown.js";

/** Removed before extraction when a template does not declare its own `remove` list. */
export const DEFAULT_REMOVE = [
  "script",
  "style",
  "svg",
  "nav",
  "footer",
  "noscript",
  "iframe",
  ".advertisement",
];

/** Removed before converting a page that matched no template. */
export const FALLBACK_REMOVE = [
  ...DEFAULT_REMOVE,
  "header",
  "aside",
  "img",
  "picture",
  "video",
  "audio",
  "canvas",
  "map",
  "area",
  "dialog",
];

/**
 * Split a selector list into ordered fallbacks on top-level commas, preserving commas inside
 * CSS functions, attribute brackets, quoted strings, and escapes.
 */
export function splitSelectors(value = "") {
  const selectors = [];
  let start = 0;
  let depth = 0;
  let quote = null;
  for (let i = 0; i < value.length; i++) {
    const char = value[i];
    if (char === "\\") {
      i++;
    } else if (quote) {
      if (char === quote) quote = null;
    } else if (char === '"' || char === "'") {
      quote = char;
    } else if (char === "(" || char === "[") {
      depth++;
    } else if (char === ")" || char === "]") {
      depth--;
    } else if (char === "," && depth === 0) {
      selectors.push(value.slice(start, i).trim());
      start = i + 1;
    }
  }
  selectors.push(value.slice(start).trim());
  return selectors;
}

/** Remove noise elements, inline styles, and embedded images from the document in place. */
export function cleanDocument($, removeSelectors = DEFAULT_REMOVE) {
  for (const selector of removeSelectors) {
    try {
      $(selector).remove();
    } catch {
      // Selector support differs between runtimes; an unsupported cleanup rule is skipped.
    }
  }
  $("[style]").removeAttr("style");
  $("[src^='data:']").removeAttr("src");
}

/** First fallback that matches anywhere in the document; an empty fallback selects `<body>`. */
function selectInDocument($, selectorList) {
  for (const selector of splitSelectors(selectorList)) {
    const matches = selector ? $(selector) : $("body");
    if (matches.length) return matches;
  }
  return $([]);
}

/**
 * First fallback that matches inside `$scope` or on `$scope`/its ancestors. Never searches
 * sibling subtrees, so a record cannot borrow a field from its neighbor. An empty fallback
 * selects `$scope` itself.
 */
export function selectInScope($scope, selectorList) {
  for (const selector of splitSelectors(selectorList)) {
    if (!selector) return $scope;
    const descendants = $scope.find(selector);
    if (descendants.length) return descendants;
    const ancestor = $scope.closest(selector);
    if (ancestor.length) return ancestor;
  }
  return $scope.slice(0, 0);
}

export function applyTransforms(value, transform, pageUrl) {
  let result = value;
  for (const name of [transform ?? []].flat()) {
    if (!result) break;
    result = TRANSFORM_FUNCTIONS[name](result, pageUrl);
  }
  return result;
}

const TRANSFORM_FUNCTIONS = {
  strip: (value) => value.trim(),
  decode_google_url(value) {
    if (!value.startsWith("/url?")) return value;
    return new URLSearchParams(value.slice("/url?".length)).get("q") ?? value;
  },
  decode_ddg_url(value) {
    if (!value.includes("/l/?")) return value;
    return new URLSearchParams(value.slice(value.indexOf("?") + 1)).get("uddg") ?? value;
  },
  json_parse(value) {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  },
  resolve_href(value, pageUrl) {
    try {
      return pageUrl ? new URL(value, pageUrl).href : value;
    } catch {
      return value;
    }
  },
};

const collapseWhitespace = (text) => text.replace(/\s+/g, " ").trim();

/** Extract one field value from the first element of `$element`. */
export function extractValue($element, section, pageUrl) {
  let value;
  switch (section.format ?? "text") {
    case "markdown":
      value = htmlToMarkdown($element.html() ?? "");
      break;
    case "attribute":
      value = $element.attr(section.attribute) ?? "";
      break;
    case "html":
      value = ($element.html() ?? "").trim();
      break;
    default:
      value = collapseWhitespace($element.text());
  }
  return applyTransforms(value, section.transform, pageUrl);
}

/** Extract child fields from one record element; empty fields are omitted. */
function extractFields($scope, section, pageUrl) {
  const fields = {};
  for (const child of section.children) {
    const matches = selectInScope($scope, child.selector);
    if (!matches.length) {
      if (child.required) {
        throw new Error(`Required field '${child.name}' not found in section '${section.name}'.`);
      }
      continue;
    }
    const value = extractValue(matches.first(), child, pageUrl);
    if (value) fields[child.name] = value;
  }
  return fields;
}

/**
 * Extract one top-level section. Result shapes:
 * - `{ name, type: "value", value }` — single element, no children
 * - `{ name, type: "list", items: string[] }` — `multiple`, no children
 * - `{ name, type: "group", fields: {} }` — single element with children
 * - `{ name, type: "records", items: {}[] }` — `multiple` with children
 */
export function extractSection($, section, { pageUrl = "", limit } = {}) {
  const { name } = section;
  const hasChildren = Boolean(section.children?.length);
  const matches = selectInDocument($, section.selector ?? "");
  if (!matches.length && section.required) {
    throw new Error(`Required section '${name}' not found on page.`);
  }

  if (!section.multiple) {
    const $first = matches.first();
    if (hasChildren) {
      return {
        name,
        type: "group",
        fields: matches.length ? extractFields($first, section, pageUrl) : {},
      };
    }
    return {
      name,
      type: "value",
      value: matches.length ? extractValue($first, section, pageUrl) : "",
    };
  }

  const elements = matches.toArray().slice(0, limit ?? section.max_items ?? Infinity);
  if (hasChildren) {
    const items = elements
      .map((element) => extractFields($(element), section, pageUrl))
      .filter((fields) => Object.keys(fields).length > 0);
    return { name, type: "records", items };
  }
  const items = elements
    .map((element) => extractValue($(element), section, pageUrl).trim())
    .filter(Boolean);
  return { name, type: "list", items };
}

/**
 * Clean the document and extract every template section. `maxResults` overrides `max_items`
 * of the first records section (the search results of a search template).
 */
export function extractTemplate($, template, { pageUrl = "", maxResults } = {}) {
  cleanDocument($, template.remove ?? DEFAULT_REMOVE);
  let limitPending = maxResults != null;
  return template.sections.map((section) => {
    const isRecords = Boolean(section.multiple && section.children?.length);
    const limit = limitPending && isRecords ? maxResults : undefined;
    if (limit !== undefined) limitPending = false;
    return extractSection($, section, { pageUrl, limit });
  });
}
