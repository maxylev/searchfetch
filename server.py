import asyncio
import json
import re
import sys
import urllib.parse
from pathlib import Path

from bs4 import BeautifulSoup, Tag
from cloakbrowser import launch_async
from markdownify import markdownify as md
from mcp.server.fastmcp import FastMCP
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

mcp = FastMCP("searchfetch")

# ---------------------------------------------------------------------------
# Browser Manager — single shared browser instance, stealth by default
# ---------------------------------------------------------------------------


class BrowserManager:
    def __init__(self):
        self.browser = None
        self._lock = asyncio.Lock()

    async def get_browser(self):
        async with self._lock:
            if self.browser and self.browser.is_connected:
                return self.browser

            self.browser = await launch_async(
                headless=True,
                humanize=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            return self.browser

    async def close(self):
        if self.browser:
            await self.browser.close()
            self.browser = None


browser_manager = BrowserManager()

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _get_string_attr(tag: Tag, attr: str) -> str:
    val = tag.get(attr)
    if isinstance(val, list):
        return str(val[0]) if val else ""
    return str(val or "")


def _select_first(parent, selector_string: str):
    """Try comma-separated selectors in order; return list of matches from the
    first selector that produces results.  Searches descendants first, then
    walks up the ancestor chain (up to 4 levels) searching each ancestor's
    subtree — this allows child selectors to find elements that are siblings
    of the parent (e.g. a snippet <div> next to an <h3> result heading).

    If *selector_string* is empty or blank, return *[parent]* (the "current
    element" semantic).  An empty selector *within* a comma list
    (e.g. ``"a, "``) also means "current parent" and short-circuits to
    *[parent]*."""
    if not selector_string or not selector_string.strip():
        return [parent]
    # Split on top-level commas — soupsieve selectors don't nest commas.
    selectors = [s.strip() for s in selector_string.split(",")]
    for sel in selectors:
        if not sel:
            # Empty selector inside comma list → "current parent element"
            return [parent]
        try:
            # 1. Descendants
            results = parent.select(sel)
            if results:
                return results
        except Exception:
            pass
        # 2. Ancestor subtrees (walk up 4 levels; siblings are in parent's
        #    subtree at level 1)
        ancestor = getattr(parent, "parent", None)
        for _level in range(4):
            if ancestor is None or not hasattr(ancestor, "select"):
                break
            try:
                results = ancestor.select(sel)
                if results:
                    return results
            except Exception:
                pass
            ancestor = getattr(ancestor, "parent", None)
    return []


def _select_one_first(parent, selector_string: str):
    results = _select_first(parent, selector_string)
    return results[0] if results else None


# ---------------------------------------------------------------------------
# Transforms (post-extraction)
# ---------------------------------------------------------------------------

TRANSFORMS = {}


def _tf_strip(value, _origin):
    return value.strip() if isinstance(value, str) else value


TRANSFORMS["strip"] = _tf_strip


def _tf_decode_google_url(value, _origin):
    """If value starts with /url?q=, strip prefix and URL-decode."""
    if isinstance(value, str) and value.startswith("/url?q="):
        try:
            raw = value.split("/url?q=")[1].split("&")[0]
            return urllib.parse.unquote(raw)
        except Exception:
            return value
    return value


TRANSFORMS["decode_google_url"] = _tf_decode_google_url


def _tf_decode_ddg_url(value, _origin):
    """If value contains /l/?uddg=, extract and URL-decode the uddg parameter."""
    if isinstance(value, str) and "/l/?uddg=" in value:
        try:
            params = dict(
                urllib.parse.parse_qsl(urllib.parse.urlsplit(value).query)
            )
            return urllib.parse.unquote(str(params.get("uddg", value)))
        except Exception:
            return value
    return value


TRANSFORMS["decode_ddg_url"] = _tf_decode_ddg_url


def _tf_json_parse(value, _origin):
    """Parse the extracted text as JSON and pretty-print."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return json.dumps(parsed, indent=2, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            return value
    return value


TRANSFORMS["json_parse"] = _tf_json_parse


def _tf_resolve_href(value, origin):
    """If href is relative (starts with /), prepend page origin."""
    if isinstance(value, str) and value.startswith("/") and not value.startswith("//"):
        return origin + value
    return value


TRANSFORMS["resolve_href"] = _tf_resolve_href


def apply_transform(value, transform, origin=""):
    """Apply a single transform name or chain (list) to *value*."""
    if transform is None:
        return value
    if isinstance(transform, str):
        chain = [transform]
    else:
        chain = transform
    for name in chain:
        fn = TRANSFORMS.get(name)
        if fn:
            value = fn(value, origin)
    return value


# ---------------------------------------------------------------------------
# Built-in templates (loaded from templates/*.json at startup)
# ---------------------------------------------------------------------------

def _load_builtin_templates():
    """Load built-in template JSON files from disk.

    Searches (in order):
      1. templates/ adjacent to server.py (development / editable install)
      2. templates/ in current working directory (uv run, npm run)
      3. templates/ under sys.prefix (data_files wheel install)

    Returns a dict of template name -> template data, preserving insertion
    order sorted by the "order" field in each template.
    """
    search_paths = [
        Path(__file__).resolve().parent / "templates",
        Path.cwd() / "templates",
        Path(sys.prefix) / "templates",
    ]

    templates_dir = None
    for sp in search_paths:
        if sp.is_dir():
            templates_dir = sp
            break

    if templates_dir is None:
        searched = "', '".join(str(p) for p in search_paths[:2])
        raise FileNotFoundError(
            f"Templates directory not found. Searched: '{searched}'"
        )

    json_files = sorted(templates_dir.glob("*.json"))
    if not json_files:
        raise FileNotFoundError(
            f"No template JSON files (*.json) found in '{templates_dir}'"
        )

    raw_templates = []
    for filepath in json_files:
        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON in template file '{filepath}': {exc}"
            ) from exc

        name = data.get("name")
        if not name or not isinstance(name, str):
            raise ValueError(
                f"Template file '{filepath}' is missing a valid 'name' field"
            )
        raw_templates.append(data)

    # Sort by "order" field for deterministic URL-pattern matching
    raw_templates.sort(key=lambda t: t.get("order", 999))

    # Build ordered dict for O(1) lookup
    templates = {}
    for t in raw_templates:
        templates[t["name"]] = t

    return templates


BUILTIN_TEMPLATES = _load_builtin_templates()

# ---------------------------------------------------------------------------
# Template resolution helpers
# ---------------------------------------------------------------------------


def resolve_search_template(engine: str):
    """Map engine name to template name. Also handles inline JSON and custom names."""
    if engine == "duckduckgo":
        return "duckduckgo-search"
    if engine == "google":
        return "google-search"
    if engine.startswith("{"):
        return engine  # inline JSON — caller parses it
    return engine  # custom name


def map_search_params(
    query: str, engine: str, region: str | None, safe_search: bool | None
):
    """Map universal websearch params to engine-specific url_params dict."""
    params = {"query": query}

    if engine == "duckduckgo":
        if region is not None:
            params["kl"] = region
        if safe_search is True:
            params["kp"] = "1"
        elif safe_search is False:
            params["kp"] = "-2"

    elif engine == "google":
        if region is not None:
            parts = region.split("-", 1) if "-" in region else [region, region]
            params["hl"] = parts[0]
            if len(parts) >= 2:
                params["gl"] = parts[1]
            else:
                params["gl"] = parts[0]
        if safe_search is True:
            params["safe"] = "active"

    return params


def resolve_url_template(template: dict, provided_params: dict) -> str:
    """Fill url_template placeholders using provided_params + url_params defaults.

    Returns the resolved URL string or raises ValueError for missing required params.
    """
    url_template = template.get("url_template")
    if not url_template:
        raise ValueError(
            f"Template '{template.get('name', 'unknown')}' has no url_template."
        )

    url_params = template.get("url_params", {})
    resolved = url_template

    # Find all {placeholder} tokens
    placeholders = re.findall(r"\{(\w+)\}", url_template)

    for key in placeholders:
        param_def = url_params.get(key, {})
        value = None

        if key in provided_params and provided_params[key] is not None:
            value = str(provided_params[key])
        elif "default" in param_def:
            value = str(param_def["default"])
        elif param_def.get("required", False):
            raise ValueError(
                f"Required URL parameter '{key}' is missing for template "
                f"'{template.get('name', 'unknown')}'."
            )
        else:
            continue

        if param_def.get("encode") == "url":
            value = urllib.parse.quote_plus(value, safe="")
        resolved = resolved.replace(f"{{{key}}}", str(value))

    return resolved


def resolve_page_template(url: str, template_arg: str):
    """Resolve the template for a webfetch request.

    Returns (template_dict | None, template_name: str).
    """
    # 1. Inline JSON?
    if template_arg.startswith("{"):
        try:
            tmpl = json.loads(template_arg)
            return tmpl, tmpl.get("name", "custom")
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid inline JSON template: {exc}") from exc

    # 2. Named built-in?
    if template_arg in BUILTIN_TEMPLATES:
        return BUILTIN_TEMPLATES[template_arg], template_arg

    # 3. "auto" — match by URL pattern
    if template_arg == "auto":
        for name, tmpl in BUILTIN_TEMPLATES.items():
            for pat in tmpl.get("url_patterns", []):
                try:
                    if re.search(pat, url):
                        return tmpl, name
                except re.error:
                    continue
        return None, "auto (fallback)"

    # Named template not found
    available = sorted(BUILTIN_TEMPLATES.keys())
    raise ValueError(
        f"Unknown template '{template_arg}'. Available: {', '.join(available)}"
    )


# ---------------------------------------------------------------------------
# Access-failure detection
# ---------------------------------------------------------------------------

ACCESS_DENIED_TITLE_PATTERNS = [
    r"\b(?:captcha|robot|verify|access\s*denied|blocked|forbidden|not\s+available)\b",
    r"\b(?:unusual\s+traffic|are\s+you\s+a\s+human)\b",
    r"\b(?:429|403|401)\b",
]

ACCESS_DENIED_BODY_TRIGGERS = [
    "captcha",
    "verify you are human",
    "are you a robot",
    "unusual traffic",
    "access denied",
    "sorry, you have been blocked",
    "to continue, please type the characters",
]

_ACCESS_DENIED_RE = re.compile(
    "|".join(ACCESS_DENIED_TITLE_PATTERNS), re.IGNORECASE
)


def _detect_access_failure(http_status: int | None, soup: BeautifulSoup) -> bool:
    """Conservative check: does the page look like an access-denial / CAPTCHA?"""
    if http_status is not None and http_status in (401, 403, 429):
        return True

    title_tag = soup.find("title")
    title_text = title_tag.get_text(strip=True) if title_tag else ""
    if title_text and _ACCESS_DENIED_RE.search(title_text):
        return True

    body = soup.find("body")
    if body:
        body_text = body.get_text(" ", strip=True)
        if len(body_text) < 800:
            lowered = body_text.lower()
            if any(trigger in lowered for trigger in ACCESS_DENIED_BODY_TRIGGERS):
                return True

    return False


# ---------------------------------------------------------------------------
# Fetch engine — stealth browser with cookie injection & resource blocking
# ---------------------------------------------------------------------------

FETCH_MAX_ATTEMPTS = 2
HTTP_429_RETRY_DELAY_SECONDS = 2.0


class HttpStatusError(RuntimeError):
    def __init__(self, status: int, url: str, retry_after: float | None = None):
        super().__init__(f"Access denied: HTTP {status} when fetching {url}")
        self.status = status
        self.retry_after = retry_after


def _parse_retry_after(value: str | None) -> float:
    if not value:
        return HTTP_429_RETRY_DELAY_SECONDS
    try:
        seconds = float(value)
        if seconds >= 0:
            return min(seconds, 30.0)
    except ValueError:
        pass
    return HTTP_429_RETRY_DELAY_SECONDS


async def _new_fetch_page(browser, template: dict | None, block_media: bool):
    context = await browser.new_context()

    if template:
        for c in template.get("cookies", []):
            await context.add_cookies([c])

    page = await context.new_page()

    if block_media:
        if template:
            block_types = set(template.get("block_resources", ["image", "media", "font"]))
        else:
            block_types = {"image", "media", "font"}

        async def route_handler(route):
            if route.request.resource_type in block_types:
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", route_handler)

    return context, page


async def fetch_html(
    url: str, template: dict | None, block_media: bool, retry: bool = True
):
    """Fetch page HTML through cloakbrowser.

    Args:
        url: Target URL.
        template: Optional template dict (for cookies, block_resources).
        block_media: Whether to block media; if True, blocks template.block_resources
                     (or default ["image","media","font"] when no template).
        retry: If True, retry once on network errors.
    """
    browser = await browser_manager.get_browser()
    context, page = await _new_fetch_page(browser, template, block_media)

    last_exc = None
    attempts = FETCH_MAX_ATTEMPTS if retry else 1
    for attempt in range(attempts):
        try:
            try:
                response = await page.goto(
                    url, wait_until="networkidle", timeout=15000
                )
            except PlaywrightTimeoutError:
                response = None  # partial render is OK

            http_status = response.status if response else None
            if http_status in (401, 403):
                raise HttpStatusError(http_status, url)
            if http_status == 429:
                retry_after = _parse_retry_after(response.headers.get("retry-after"))
                raise HttpStatusError(http_status, url, retry_after)

            content = await page.content()
            soup = BeautifulSoup(content, "html.parser")

            if _detect_access_failure(http_status, soup):
                raise RuntimeError(
                    f"Access to {url} was denied (HTTP {http_status or 'N/A'}). "
                    "The site may require authentication, has blocked this request, "
                    "or presented a CAPTCHA."
                )

            await context.close()
            return content

        except HttpStatusError as exc:
            last_exc = exc
            if attempt < attempts - 1 and exc.status == 429:
                await context.close()
                await asyncio.sleep(exc.retry_after or HTTP_429_RETRY_DELAY_SECONDS)
                context, page = await _new_fetch_page(browser, template, block_media)
                continue
            await context.close()
            raise

        except (RuntimeError, ValueError):
            await context.close()
            raise

        except Exception as exc:
            last_exc = exc
            # Retry only on network-level errors (parity with index.js:
            # retry on net::, ERR_, Navigation failed)
            exc_msg = str(exc).lower()
            is_network = (
                "net::" in exc_msg
                or "err_" in exc_msg
                or "timeout" in exc_msg
                or "connection" in exc_msg
                or "econnrefused" in exc_msg
                or "econnreset" in exc_msg
                or "enotfound" in exc_msg
                or "navigation" in exc_msg
            )
            if attempt < attempts - 1 and is_network:
                await asyncio.sleep(0.5)
                # Need fresh page/context for retry
                await context.close()
                context, page = await _new_fetch_page(browser, template, block_media)
                continue
            await context.close()
            raise RuntimeError(f"Failed to fetch {url} after retry: {last_exc}") from exc

    # Should not reach here, but guard anyway
    await context.close()
    raise RuntimeError(f"Failed to fetch {url} after retry: {last_exc}")


# ---------------------------------------------------------------------------
# Extraction engine
# ---------------------------------------------------------------------------


def _extract_element(element: Tag, section: dict, origin: str):
    """Extract content from a single element according to section format/attribute."""
    fmt = section.get("format", "text")
    attr_name = section.get("attribute")

    if fmt == "attribute":
        value = _get_string_attr(element, attr_name or "href")
    elif fmt == "html":
        value = str(element)
    elif fmt == "markdown":
        raw = element.decode_contents()
        value = md(raw, heading_style="ATX")
        value = re.sub(r"\n{3,}", "\n\n", value).strip()
    else:  # "text" (default)
        value = element.get_text(" ", strip=True)

    transform = section.get("transform")
    if transform:
        value = apply_transform(value, transform, origin)

    return value


def extract_section(parent, section: dict, origin: str = ""):
    """Extract one section from *parent* (soup or Tag element).

    Returns a dict describing the extracted content:
        {"name": …, "type": "value"|"multiple"|"children"|"search_results", …}
    """
    name = section["name"]
    selector = section.get("selector", "")
    is_multiple = section.get("multiple", False)
    max_items = section.get("max_items")
    children_defs = section.get("children", [])
    required = section.get("required", False)

    if children_defs:
        # ----- Children mode: parent is just a scoping container -----
        if is_multiple:
            # Multiple parents, each with children → "search_results" type
            parent_matches = _select_first(parent, selector)
            if not parent_matches:
                if required:
                    raise ValueError(f"Required section '{name}' not found on page.")
                return {"name": name, "type": "search_results", "items": []}

            if max_items is not None:
                parent_matches = parent_matches[:max_items]

            items = []
            for p_el in parent_matches:
                if not isinstance(p_el, Tag):
                    continue
                item = {}
                for child_def in children_defs:
                    child_name = child_def["name"]
                    child_selector = child_def.get("selector", "")
                    child_req = child_def.get("required", False)

                    child_el = _select_one_first(p_el, child_selector)
                    if child_el and isinstance(child_el, Tag):
                        item[child_name] = _extract_element(
                            child_el, child_def, origin
                        )
                    elif child_req:
                        raise ValueError(
                            f"Required child section '{child_name}' not found "
                            f"inside '{name}'."
                        )
                    else:
                        item[child_name] = ""
                items.append(item)

            return {"name": name, "type": "search_results", "items": items}

        else:
            # Single parent with children
            parent_el = _select_one_first(parent, selector)
            if not parent_el:
                if required:
                    raise ValueError(f"Required section '{name}' not found on page.")
                return {"name": name, "type": "children", "children": []}

            child_results = []
            for child_def in children_defs:
                child_name = child_def["name"]
                child_selector = child_def.get("selector", "")
                child_req = child_def.get("required", False)

                child_el = _select_one_first(parent_el, child_selector)
                if child_el and isinstance(child_el, Tag):
                    child_results.append(
                        {
                            "name": child_name,
                            "type": "value",
                            "value": _extract_element(child_el, child_def, origin),
                        }
                    )
                elif child_req:
                    raise ValueError(
                        f"Required child section '{child_name}' not found "
                        f"inside '{name}'."
                    )
                else:
                    child_results.append(
                        {"name": child_name, "type": "value", "value": ""}
                    )

            return {"name": name, "type": "children", "children": child_results}

    else:
        # ----- Simple mode (no children) -----
        if is_multiple:
            matches = _select_first(parent, selector)
            if not matches:
                if required:
                    raise ValueError(f"Required section '{name}' not found on page.")
                return {"name": name, "type": "multiple", "items": []}

            if max_items is not None:
                matches = matches[:max_items]

            values = []
            for el in matches:
                if isinstance(el, Tag):
                    values.append(_extract_element(el, section, origin))
                elif isinstance(el, str):
                    values.append(str(el))

            return {"name": name, "type": "multiple", "items": values}

        else:
            el = _select_one_first(parent, selector)
            if not el:
                if required:
                    raise ValueError(f"Required section '{name}' not found on page.")
                return {"name": name, "type": "value", "value": ""}

            if not isinstance(el, Tag):
                return {"name": name, "type": "value", "value": str(el)}

            return {
                "name": name,
                "type": "value",
                "value": _extract_element(el, section, origin),
            }


def extract_template(
    soup: BeautifulSoup,
    template: dict,
    origin: str = "",
    max_results_override: int | None = None,
):
    """Run the full extraction pipeline against *soup* using *template*.

    Returns list of section result dicts.
    """
    # 1. Global cleanup — remove elements listed in template.remove
    #    Use spec default when template doesn't specify its own remove list.
    remove_selectors = template.get("remove", None)
    if remove_selectors is None:
        remove_selectors = _SPEC_DEFAULT_REMOVE
    for sel in remove_selectors:
        try:
            for tag in soup.select(sel):
                if hasattr(tag, "decompose"):
                    tag.decompose()
        except Exception:
            pass

    # Strip style attributes and data:image src (parity with index.js applyRemove)
    for tag in soup.select("[style]"):
        if isinstance(tag, Tag):
            tag.attrs.pop("style", None)
    for tag in soup.find_all(True):
        if isinstance(tag, Tag):
            src = _get_string_attr(tag, "src")
            if src.startswith("data:image"):
                tag.attrs.pop("src", None)

    sections_data = []
    max_items_applied = False

    for section in template.get("sections", []):
        # max_results override: first section with multiple + children
        if (
            max_results_override is not None
            and not max_items_applied
            and section.get("multiple")
            and section.get("children")
        ):
            section = dict(section)
            section["max_items"] = max_results_override
            max_items_applied = True

        result = extract_section(soup, section, origin)
        sections_data.append(result)

    return sections_data


# ---------------------------------------------------------------------------
# Composition (output formatting)
# ---------------------------------------------------------------------------


def _format_children_section(section_data: dict) -> str:
    """Format a single-parent + children section.

    Each child is emitted as its own ``## ChildName`` section (parity with
    index.js composeSections "children" branch).  The parent section name is
    not used as a heading — it only scopes extraction.

    Special case: if children contain 'Author' + 'Comment' (or 'Body'),
    use the threaded comment format.
    """
    children = section_data.get("children", [])
    name = section_data["name"]

    child_names = {c["name"].lower() for c in children}
    is_threaded = "author" in child_names and (
        "comment" in child_names or "body" in child_names
    )

    if is_threaded:
        # Threaded comment format: ## Comments / **author:** / body / ---
        lines = [f"## {name}", ""]
        for child in children:
            cname = child["name"]
            cval = child.get("value", "").strip()
            if cname.lower() == "author":
                if cval:
                    lines.append(f"**{cval}:**")
            elif cname.lower() in ("comment", "body"):
                if cval:
                    lines.append("")
                    lines.append(cval)
                    lines.append("")
                    lines.append("---")
                    lines.append("")
        # Remove trailing --- separator
        while lines and lines[-1] in ("---", ""):
            lines.pop()
        return "\n".join(lines).rstrip() if len(lines) > 2 else ""

    # Standard children: each child is its own ## ChildName section
    parts = []
    for child in children:
        cname = child["name"]
        cval = child.get("value", "").strip()
        if cval:
            parts.append(f"## {cname}\n\n{cval}")
    return "\n\n---\n\n".join(parts) if parts else ""


def compose_sections(sections_data: list[dict], _template_name: str = "") -> str:
    """Compose extracted sections into a Markdown string (for webfetch)."""
    parts = []

    for sec in sections_data:
        sec_type = sec.get("type")
        name = sec.get("name", "")

        if sec_type == "value":
            val = sec.get("value", "").strip()
            if val:
                parts.append(f"## {name}\n\n{val}")

        elif sec_type == "multiple":
            items = sec.get("items", [])
            if items:
                lines = [f"## {name}", ""]
                for item in items:
                    lines.append(str(item).strip())
                parts.append("\n".join(lines))

        elif sec_type == "children":
            child_str = _format_children_section(sec)
            if child_str:
                parts.append(child_str)

        elif sec_type == "search_results":
            items = sec.get("items", [])
            if not items:
                continue
            lines = [f"## {name}", ""]
            for i, item in enumerate(items, start=1):
                title = str(item.get("Title", "")).strip()
                url = str(item.get("URL", "")).strip()
                snippet = str(item.get("Snippet", "")).strip()
                lines.append(f"[{i}] {title}")
                if url:
                    lines.append(f"    URL: {url}")
                if snippet:
                    lines.append(f"    Snippet: {snippet}")
            parts.append("\n".join(lines))

    if not parts:
        return "(No content extracted from this page.)"

    return "\n\n---\n\n".join(parts)


def compose_search_results(
    sections_data: list[dict], _template_name: str = ""
) -> str:
    """Compose extracted sections into the websearch numbered-output format.
    Filters non-http URLs and Google internal links (parity with index.js)."""
    for sec in sections_data:
        if sec.get("type") == "search_results":
            items = sec.get("items", [])
            if not items:
                return "## Results\n\nNo results found."

            section_name = sec.get("name", "Results")
            lines = [f"## {section_name}", ""]
            for i, item in enumerate(items, start=1):
                title = str(item.get("Title", "")).strip()
                url = str(item.get("URL", "")).strip()
                snippet = str(item.get("Snippet", "")).strip()

                # Filter out non-http URLs and Google internal links
                clean_url = url
                if clean_url and not clean_url.startswith("http"):
                    clean_url = ""
                if clean_url and (
                    "google.com/search" in clean_url
                    or "support.google.com" in clean_url
                ):
                    clean_url = ""

                if not title:
                    continue

                lines.append(f"[{i}] {title}")
                if clean_url:
                    lines.append(f"    URL: {clean_url}")
                if snippet:
                    lines.append(f"    Snippet: {snippet}")
            if len(lines) <= 2:  # only header, no items
                return "## Results\n\nNo results found."
            return "\n".join(lines)

    # Fall back to section-based output when no search_results section exists
    # (parity with index.js: composeSearchResults falls back to composeSections)
    return compose_sections(sections_data, _template_name)


# ---------------------------------------------------------------------------
# Generic fallback (webfetch when no template matches)
# ---------------------------------------------------------------------------


# Per-spec default remove selectors when a template does not specify its own.
_SPEC_DEFAULT_REMOVE = [
    "script", "style", "svg", "nav", "footer", "noscript", "iframe", ".advertisement",
]


DEFAULT_REMOVE_SELECTORS = [
    "script", "style", "nav", "header", "footer", "noscript",
    "iframe", "svg", "aside", "img", "picture", "video", "audio",
    "canvas", "map", "area", "dialog", ".advertisement",
]


def generic_fallback(html_content: str, _url: str = "") -> str:
    """Strip noise and convert entire page to Markdown."""
    soup = BeautifulSoup(html_content, "html.parser")

    for sel in DEFAULT_REMOVE_SELECTORS:
        try:
            for tag in soup.select(sel):
                if hasattr(tag, "decompose"):
                    tag.decompose()
        except Exception:
            pass

    for tag in soup.find_all(True):
        if isinstance(tag, Tag):
            tag.attrs.pop("style", None)
            src = _get_string_attr(tag, "src")
            if src.startswith("data:image"):
                tag.attrs.pop("src", None)

    markdown = md(str(soup), heading_style="ATX")
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()
    return markdown


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def websearch(
    query: str,
    engine: str = "duckduckgo",
    region: str | None = None,
    safe_search: bool | None = None,
    max_results: int = 10,
    block_media: bool = True,
) -> str:
    """
    Search the web using template-driven search engines (Google, DuckDuckGo, or custom).

    Args:
        query: The search query string.
        engine: Search engine: 'duckduckgo' or 'google'. Also accepts custom template names.
        region: Region code. DDG: 'us-en', 'de-de', etc. Google: 'de-de' maps to hl=de, gl=de.
        safe_search: Enable safe search. Maps to engine-specific params.
        max_results: Max results to extract. Default: 10.
        block_media: Block images/media/fonts. Default: True.
    """
    # 1. Resolve engine → template name
    template_name = resolve_search_template(engine)

    # 2. Resolve the template object
    if template_name.startswith("{"):
        try:
            template = json.loads(template_name)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid inline JSON template: {exc}") from exc
    else:
        if template_name not in BUILTIN_TEMPLATES:
            available = sorted(BUILTIN_TEMPLATES.keys())
            raise ValueError(
                f"Unknown search template '{template_name}'. "
                f"Available: {', '.join(available)}"
            )
        template = BUILTIN_TEMPLATES[template_name]

    # Validate url_template exists
    if not template.get("url_template"):
        raise ValueError(
            f"Template '{template.get('name', template_name)}' is not a search "
            "template (no url_template). Use webfetch for page templates."
        )

    # 3. Map universal params to engine-specific url_params
    engine_key = engine if engine in ("duckduckgo", "google") else "custom"
    url_params = map_search_params(query, engine_key, region, safe_search)

    # 4. Resolve URL template
    resolved_url = resolve_url_template(template, url_params)

    # Google safe_search: append &safe=active after URL resolution
    if engine_key == "google" and safe_search is True:
        resolved_url += "&safe=active"

    # 5. Fetch
    html = await fetch_html(resolved_url, template, block_media, retry=True)

    # 6. Parse and extract
    soup = BeautifulSoup(html, "html.parser")
    sections_data = extract_template(
        soup, template, origin="", max_results_override=max_results
    )

    # 7. Compose output
    return compose_search_results(sections_data, template.get("name", template_name))


@mcp.tool()
async def webfetch(
    url: str,
    template: str = "auto",
    start_index: int = 0,
    max_length: int = 10000,
    block_media: bool = True,
) -> str:
    """
    Fetch a webpage and extract structured information using templates.

    Args:
        url: The full URL to fetch.
        template: "auto", a built-in name, or inline JSON. Default: "auto".
        start_index: Character offset for pagination. Default: 0.
        max_length: Max characters to return. Default: 10000.
        block_media: Block images/media/fonts. Default: True.
    """
    # 1. Resolve template
    matched_template, template_name = resolve_page_template(url, template)

    # 2. Determine origin for resolve_href transform
    try:
        parsed = urllib.parse.urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        origin = ""

    # 3. Fetch
    html = await fetch_html(url, matched_template, block_media, retry=True)

    # 4. Parse and extract
    soup = BeautifulSoup(html, "html.parser")

    if matched_template is not None:
        sections_data = extract_template(soup, matched_template, origin=origin)
        output = compose_sections(sections_data, template_name)
    else:
        output = generic_fallback(html, url)

    # 5. Pagination
    total_length = len(output)
    paginated = output[start_index : start_index + max_length]

    showing_end = start_index + len(paginated)
    metadata = (
        f"\n\n---\n"
        f"[webfetch: template=\"{template_name}\", "
        f"showing characters {start_index} to {showing_end} "
        f"of {total_length} total."
    )
    if start_index + max_length < total_length:
        metadata += f" Use start_index={start_index + max_length} to read more."
    metadata += "]"

    return paginated + metadata


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
