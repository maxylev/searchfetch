/**
 * Render extracted sections as Markdown and paginate tool output.
 *
 * Mirrors `searchfetch/compose.py`; `tests/fixtures/composition.json` keeps them in sync.
 */
export const NO_CONTENT = "(No content extracted from this page.)";

const SECTION_SEPARATOR = "\n\n---\n\n";

/** Case-insensitive lookup of the first non-empty field among `keys`. */
export function field(record, ...keys) {
  const wanted = keys.map((key) => key.toLowerCase());
  for (const key of wanted) {
    for (const [name, value] of Object.entries(record)) {
      if (name.toLowerCase() === key && String(value ?? "").trim()) return String(value).trim();
    }
  }
  return "";
}

/** Records whose first item has an author/user and a comment/body render as a thread. */
function isThread(items) {
  return Boolean(
    items.length && field(items[0], "author", "user") && field(items[0], "comment", "body"),
  );
}

function renderRecords({ name, items }) {
  if (isThread(items)) {
    const comments = items
      .map((item) => {
        const author = field(item, "author", "user");
        const comment = field(item, "comment", "body");
        return author ? `**${author}:**\n\n${comment}` : comment;
      })
      .filter(Boolean);
    return comments.length ? `## ${name}\n\n${comments.join(SECTION_SEPARATOR)}` : "";
  }
  const records = items
    .map((item) =>
      Object.entries(item)
        .map(([key, value]) => [key, String(value ?? "").trim()])
        .filter(([, value]) => value)
        .map(([key, value]) => `    ${key}: ${value}`)
        .join("\n"),
    )
    .filter(Boolean);
  return records.length ? `## ${name}\n\n${records.join("\n\n")}` : "";
}

function renderSection(section) {
  switch (section.type) {
    case "value": {
      const value = String(section.value ?? "").trim();
      return value ? `## ${section.name}\n\n${value}` : "";
    }
    case "list": {
      const items = section.items.map((item) => String(item).trim()).filter(Boolean);
      return items.length ? `## ${section.name}\n\n${items.map((i) => `- ${i}`).join("\n")}` : "";
    }
    case "group":
      return Object.entries(section.fields)
        .map(([name, value]) => [name, String(value ?? "").trim()])
        .filter(([, value]) => value)
        .map(([name, value]) => `## ${name}\n\n${value}`)
        .join(SECTION_SEPARATOR);
    case "records":
      return renderRecords(section);
    default:
      throw new Error(`Unknown section type '${section.type}'.`);
  }
}

/** Render `webfetch` sections; empty sections are skipped. */
export function composeSections(sections) {
  const parts = sections.map(renderSection).filter(Boolean);
  return parts.length ? parts.join(SECTION_SEPARATOR) : NO_CONTENT;
}

function cleanResultUrl(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    return "";
  }
  const isGoogleInternal =
    url.hostname === "support.google.com" ||
    (["google.com", "www.google.com"].includes(url.hostname) && url.pathname === "/search");
  return ["http:", "https:"].includes(url.protocol) && !isGoogleInternal ? value : "";
}

/**
 * Render a search-results records section as a numbered list. Untitled results are skipped;
 * non-web and search-engine-internal URLs are dropped.
 */
export function composeSearchResults({ name, items }) {
  const results = [];
  for (const item of items) {
    const title = field(item, "title");
    if (!title) continue;
    const lines = [`[${results.length + 1}] ${title}`];
    const url = cleanResultUrl(field(item, "url"));
    const snippet = field(item, "snippet");
    if (url) lines.push(`    URL: ${url}`);
    if (snippet) lines.push(`    Snippet: ${snippet}`);
    results.push(lines.join("\n"));
  }
  return results.length ? `## ${name}\n\n${results.join("\n\n")}` : NO_CONTENT;
}

/**
 * Return one page of `text`, counted in Unicode code points so offsets agree with Python and
 * never split a surrogate pair, followed by a footer describing the window.
 */
export function paginate(text, templateName, startIndex, maxLength) {
  const characters = Array.from(text);
  const start = Math.min(startIndex, characters.length);
  const end = Math.min(start + maxLength, characters.length);
  const more = end < characters.length ? ` Use start_index=${end} to read more.` : "";
  return (
    `${characters.slice(start, end).join("")}\n\n---\n` +
    `[webfetch: template="${templateName}", showing characters ${start} to ${end} ` +
    `of ${characters.length} total.${more}]`
  );
}
