/**
 * Template loading, validation, lookup and URL resolution.
 *
 * Templates are shared JSON documents in `templates/`; the Python runtime implements the same
 * rules in `searchfetch/templates.py`.
 */
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

export const TEMPLATES_DIR = fileURLToPath(new URL("../templates/", import.meta.url));

export const FORMATS = new Set(["text", "markdown", "attribute", "html"]);
export const TRANSFORMS = new Set([
  "strip",
  "decode_google_url",
  "decode_ddg_url",
  "json_parse",
  "resolve_href",
]);

/** Short engine names accepted by `websearch` in addition to template names. */
export const ENGINE_ALIASES = { duckduckgo: "duckduckgo-search", google: "google-search" };

const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

function validateSection(section, where) {
  if (!isObject(section) || typeof section.name !== "string" || !section.name) {
    throw new Error(`${where}: every section needs a non-empty "name".`);
  }
  const label = `${where} section '${section.name}'`;
  if (section.selector != null && typeof section.selector !== "string") {
    throw new Error(`${label}: "selector" must be a string.`);
  }
  const format = section.format ?? "text";
  if (!FORMATS.has(format)) {
    throw new Error(`${label}: unknown format '${format}'.`);
  }
  if (format === "attribute" && (typeof section.attribute !== "string" || !section.attribute)) {
    throw new Error(`${label}: format "attribute" requires an "attribute" name.`);
  }
  if (
    section.max_items != null &&
    !(Number.isInteger(section.max_items) && section.max_items > 0)
  ) {
    throw new Error(`${label}: "max_items" must be a positive integer.`);
  }
  const transforms = section.transform == null ? [] : [section.transform].flat();
  for (const transform of transforms) {
    if (!TRANSFORMS.has(transform)) throw new Error(`${label}: unknown transform '${transform}'.`);
  }
  if (section.children != null) {
    if (!Array.isArray(section.children)) throw new Error(`${label}: "children" must be a list.`);
    for (const child of section.children) {
      validateSection(child, label);
      if (child.children) throw new Error(`${label}: children cannot be nested.`);
    }
  }
}

/** Validate a template object, compile its URL patterns, and return it. Throws on errors. */
export function validateTemplate(template, where = "template") {
  if (!isObject(template)) throw new Error(`${where}: template must be a JSON object.`);
  if (typeof template.name !== "string" || !template.name) {
    throw new Error(`${where}: template needs a non-empty "name".`);
  }
  if (!Array.isArray(template.sections)) {
    throw new Error(`${where}: template needs a "sections" list.`);
  }
  for (const section of template.sections) validateSection(section, where);
  const patterns = template.url_patterns ?? [];
  if (!Array.isArray(patterns) || patterns.some((pattern) => typeof pattern !== "string")) {
    throw new Error(`${where}: "url_patterns" must be a list of strings.`);
  }
  for (const pattern of patterns) {
    try {
      new RegExp(pattern);
    } catch (err) {
      throw new Error(`${where}: invalid URL pattern ${JSON.stringify(pattern)}: ${err.message}`, {
        cause: err,
      });
    }
  }
  return template;
}

/** Parse and validate a template passed inline as JSON text. */
export function parseInlineTemplate(text) {
  let template;
  try {
    template = JSON.parse(text);
  } catch (err) {
    throw new Error(`Invalid inline JSON template: ${err.message}`, { cause: err });
  }
  if (isObject(template)) template.name ??= "custom";
  return validateTemplate(template, "Inline template");
}

/** Load every `*.json` template from `directory`, sorted by `order` for URL matching. */
export function loadTemplates(directory = TEMPLATES_DIR) {
  const files = readdirSync(directory)
    .filter((file) => file.endsWith(".json"))
    .sort();
  if (files.length === 0) throw new Error(`No template JSON files found in '${directory}'.`);

  const templates = new Map();
  const loaded = files.map((file) => {
    const path = join(directory, file);
    let template;
    try {
      template = JSON.parse(readFileSync(path, "utf8"));
    } catch (err) {
      throw new Error(`Invalid JSON in template file '${path}': ${err.message}`, { cause: err });
    }
    return validateTemplate(template, `Template file '${path}'`);
  });
  loaded.sort((a, b) => (a.order ?? Infinity) - (b.order ?? Infinity));
  for (const template of loaded) {
    if (templates.has(template.name)) throw new Error(`Duplicate template '${template.name}'.`);
    templates.set(template.name, template);
  }
  return templates;
}

export const BUILTIN_TEMPLATES = loadTemplates();

export function getTemplate(name, templates = BUILTIN_TEMPLATES) {
  const template = templates.get(name);
  if (!template) {
    throw new Error(`Unknown template '${name}'. Available: ${[...templates.keys()].join(", ")}`);
  }
  return template;
}

/** Return the first template (by `order`) whose URL pattern matches, or null. */
export function detectTemplate(url, templates = BUILTIN_TEMPLATES) {
  for (const template of templates.values()) {
    if (template.url_patterns?.some((pattern) => new RegExp(pattern).test(url))) return template;
  }
  return null;
}

/** Resolve the `template` argument of `webfetch`: "auto", a template name, or inline JSON. */
export function resolvePageTemplate(url, value) {
  if (value.trimStart().startsWith("{")) return parseInlineTemplate(value);
  if (value === "auto") return detectTemplate(url);
  return getTemplate(value);
}

export function validateUrl(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error(`Invalid URL: ${value}`);
  }
  if (!["http:", "https:"].includes(url.protocol) || !url.hostname) {
    throw new Error("URL must use HTTP or HTTPS and include a hostname.");
  }
  return value;
}

/** Fill `{placeholder}`s in `template.url_template` from `params` and `url_params` defaults. */
export function resolveUrlTemplate(template, params) {
  if (!template.url_template) throw new Error(`Template '${template.name}' has no url_template.`);
  return template.url_template.replace(/\{(\w+)\}/g, (_match, key) => {
    const definition = template.url_params?.[key] ?? {};
    const value = (Object.hasOwn(params, key) ? params[key] : null) ?? definition.default;
    if (value == null && definition.required) {
      throw new Error(
        `Required URL parameter '${key}' is missing for template '${template.name}'.`,
      );
    }
    const text = String(value ?? "");
    return definition.encode === "url" ? encodeURIComponent(text) : text;
  });
}

/** Map the engine-neutral `region` and `safe_search` options onto template URL params. */
export function mapSearchParams(engine, query, region, safeSearch) {
  const params = { query };
  if (engine === "duckduckgo") {
    if (region != null) params.kl = region;
    if (safeSearch != null) params.kp = safeSearch ? "1" : "-2";
  } else if (engine === "google" && region != null) {
    const [country, ...language] = region.split("-");
    params.gl = country;
    if (language.length > 0) params.hl = language.join("-");
  }
  return params;
}

/** Build the request URL for one search template. */
export function buildSearchUrl(template, query, region, safeSearch) {
  if (!template.url_template) {
    throw new Error(`Template '${template.name}' is not a search template (no url_template).`);
  }
  const params = mapSearchParams(template.engine, query, region, safeSearch);
  const url = resolveUrlTemplate(template, params);
  if (template.engine !== "google" || safeSearch == null) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}safe=${safeSearch ? "active" : "off"}`;
}

/**
 * Resolve `websearch`'s `engine` argument into the ordered list of templates to try: the
 * requested template followed by its `fallback` chain.
 */
export function resolveSearchCandidates(engine, query, region, safeSearch) {
  const first = engine.trimStart().startsWith("{")
    ? parseInlineTemplate(engine)
    : getTemplate(ENGINE_ALIASES[engine] ?? engine);

  const candidates = [];
  const seen = new Set();
  for (let template = first; template && !seen.has(template.name);) {
    seen.add(template.name);
    candidates.push({ template, url: buildSearchUrl(template, query, region, safeSearch) });
    template = template.fallback ? getTemplate(template.fallback) : null;
  }
  return candidates;
}
