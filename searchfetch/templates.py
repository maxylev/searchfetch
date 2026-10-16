"""Template loading, validation, lookup and URL resolution.

Templates are shared JSON documents in ``templates/``; the Node.js runtime implements the same
rules in ``src/templates.js``.
"""

import json
import re
import urllib.parse
from pathlib import Path
from typing import Any

Template = dict[str, Any]

FORMATS = frozenset({"text", "markdown", "attribute", "html"})
TRANSFORMS = frozenset(
    {"strip", "decode_google_url", "decode_ddg_url", "json_parse", "resolve_href"}
)

#: Short engine names accepted by ``websearch`` in addition to template names.
ENGINE_ALIASES = {"duckduckgo": "duckduckgo-search", "google": "google-search"}

_PACKAGE_DIR = Path(__file__).resolve().parent
#: Bundled inside the wheel; falls back to the repository directory in a source checkout.
TEMPLATES_DIR = next(
    (
        path
        for path in (_PACKAGE_DIR / "templates", _PACKAGE_DIR.parent / "templates")
        if path.is_dir()
    ),
    _PACKAGE_DIR / "templates",
)


def _validate_section(section: Any, where: str) -> None:
    if (
        not isinstance(section, dict)
        or not isinstance(section.get("name"), str)
        or not section["name"]
    ):
        raise ValueError(f'{where}: every section needs a non-empty "name".')
    label = f"{where} section '{section['name']}'"
    if section.get("selector") is not None and not isinstance(section["selector"], str):
        raise ValueError(f'{label}: "selector" must be a string.')
    fmt = section.get("format", "text")
    if fmt not in FORMATS:
        raise ValueError(f"{label}: unknown format '{fmt}'.")
    attribute = section.get("attribute")
    if fmt == "attribute" and (not isinstance(attribute, str) or not attribute):
        raise ValueError(f'{label}: format "attribute" requires an "attribute" name.')
    max_items = section.get("max_items")
    if max_items is not None and (type(max_items) is not int or max_items <= 0):
        raise ValueError(f'{label}: "max_items" must be a positive integer.')
    transform = section.get("transform")
    for name in (
        [] if transform is None else [transform] if isinstance(transform, str) else transform
    ):
        if name not in TRANSFORMS:
            raise ValueError(f"{label}: unknown transform '{name}'.")
    children = section.get("children")
    if children is not None:
        if not isinstance(children, list):
            raise ValueError(f'{label}: "children" must be a list.')
        for child in children:
            _validate_section(child, label)
            if child.get("children"):
                raise ValueError(f"{label}: children cannot be nested.")


def validate_template(template: Any, where: str = "template") -> Template:
    """Validate a template object and its URL patterns, and return it. Raises ``ValueError``."""
    if not isinstance(template, dict):
        raise ValueError(f"{where}: template must be a JSON object.")
    if not isinstance(template.get("name"), str) or not template["name"]:
        raise ValueError(f'{where}: template needs a non-empty "name".')
    if not isinstance(template.get("sections"), list):
        raise ValueError(f'{where}: template needs a "sections" list.')
    for section in template["sections"]:
        _validate_section(section, where)
    patterns = template.get("url_patterns", [])
    if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
        raise ValueError(f'{where}: "url_patterns" must be a list of strings.')
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"{where}: invalid URL pattern {json.dumps(pattern)}: {exc}") from exc
    return template


def parse_inline_template(text: str) -> Template:
    """Parse and validate a template passed inline as JSON text."""
    try:
        template = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid inline JSON template: {exc}") from exc
    if isinstance(template, dict):
        template.setdefault("name", "custom")
    return validate_template(template, "Inline template")


def load_templates(directory: Path = TEMPLATES_DIR) -> dict[str, Template]:
    """Load every ``*.json`` template from *directory*, ordered by ``order`` for URL matching."""
    files = sorted(directory.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"No template JSON files found in '{directory}'.")

    loaded = []
    for path in files:
        try:
            template = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in template file '{path}': {exc}") from exc
        loaded.append(validate_template(template, f"Template file '{path}'"))
    loaded.sort(key=lambda template: template.get("order", float("inf")))

    templates: dict[str, Template] = {}
    for template in loaded:
        if template["name"] in templates:
            raise ValueError(f"Duplicate template '{template['name']}'.")
        templates[template["name"]] = template
    return templates


BUILTIN_TEMPLATES = load_templates()


def get_template(name: str, templates: dict[str, Template] = BUILTIN_TEMPLATES) -> Template:
    try:
        return templates[name]
    except KeyError:
        available = ", ".join(templates)
        raise ValueError(f"Unknown template '{name}'. Available: {available}") from None


def detect_template(
    url: str, templates: dict[str, Template] = BUILTIN_TEMPLATES
) -> Template | None:
    """Return the first template (by ``order``) whose URL pattern matches, or ``None``."""
    for template in templates.values():
        if any(re.search(pattern, url) for pattern in template.get("url_patterns", [])):
            return template
    return None


def resolve_page_template(url: str, value: str) -> Template | None:
    """Resolve ``webfetch``'s ``template`` argument: "auto", a template name, or inline JSON."""
    if value.lstrip().startswith("{"):
        return parse_inline_template(value)
    if value == "auto":
        return detect_template(url)
    return get_template(value)


def validate_url(value: str) -> str:
    parts = urllib.parse.urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("URL must use HTTP or HTTPS and include a hostname.")
    return value


def _encode_uri_component(value: str) -> str:
    """Percent-encode exactly like JavaScript's ``encodeURIComponent``."""
    return urllib.parse.quote(value, safe="-_.!~*'()")


def resolve_url_template(template: Template, params: dict[str, Any]) -> str:
    """Fill ``{placeholder}``s in ``url_template`` from *params* and ``url_params`` defaults."""
    url_template = template.get("url_template")
    if not url_template:
        raise ValueError(f"Template '{template['name']}' has no url_template.")
    definitions = template.get("url_params", {})

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        definition = definitions.get(key, {})
        value = params.get(key)
        if value is None:
            value = definition.get("default")
        if value is None and definition.get("required"):
            raise ValueError(
                f"Required URL parameter '{key}' is missing for template '{template['name']}'."
            )
        text = "" if value is None else str(value)
        return _encode_uri_component(text) if definition.get("encode") == "url" else text

    return re.sub(r"\{(\w+)\}", substitute, url_template)


def map_search_params(
    engine: str | None, query: str, region: str | None, safe_search: bool | None
) -> dict[str, str]:
    """Map the engine-neutral ``region`` and ``safe_search`` options onto template URL params."""
    params = {"query": query}
    if engine == "duckduckgo":
        if region is not None:
            params["kl"] = region
        if safe_search is not None:
            params["kp"] = "1" if safe_search else "-2"
    elif engine == "google" and region is not None:
        country, _, language = region.partition("-")
        params["gl"] = country
        if language:
            params["hl"] = language
    return params


def build_search_url(
    template: Template, query: str, region: str | None, safe_search: bool | None
) -> str:
    """Build the request URL for one search template."""
    if not template.get("url_template"):
        raise ValueError(
            f"Template '{template['name']}' is not a search template (no url_template)."
        )
    params = map_search_params(template.get("engine"), query, region, safe_search)
    url = resolve_url_template(template, params)
    if template.get("engine") != "google" or safe_search is None:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}safe={'active' if safe_search else 'off'}"


def resolve_search_candidates(
    engine: str, query: str, region: str | None, safe_search: bool | None
) -> list[tuple[Template, str]]:
    """Resolve ``websearch``'s ``engine`` into ``(template, url)`` pairs to try in order.

    The requested template comes first, followed by its ``fallback`` chain.
    """
    template: Template | None = (
        parse_inline_template(engine)
        if engine.lstrip().startswith("{")
        else get_template(ENGINE_ALIASES.get(engine, engine))
    )
    candidates = []
    seen = set()
    while template is not None and template["name"] not in seen:
        seen.add(template["name"])
        candidates.append((template, build_search_url(template, query, region, safe_search)))
        template = get_template(template["fallback"]) if template.get("fallback") else None
    return candidates
