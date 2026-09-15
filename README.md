# SearchFetch (MCP Server)

A fault-tolerant, stealth-enabled Model Context Protocol (MCP) server for web searching and content fetching. Built for AI Agents (Cursor, Claude Code, OpenCode), it uses a browser to render pages and converts their content into Markdown.

## Features

- **Browser rendering:** CloakBrowser runs Chromium with humanized interactions. Sites can still require authentication or present challenges.
- **Fault tolerance:** Reconnects disconnected browsers, retries network failures and HTTP 429 once, and blocks template-selected resource types. HTTP errors are reported before waiting for rendering.
- **Token-Optimized Output:** Strips base64 images, SVGs, scripts, and inline styles from the DOM _before_ Markdown conversion.
- **Dual runtime:** Run through Python (`uvx`, Python 3.10+) or Node.js (`npx`, Node 24+). First use downloads dependencies and a browser binary.
- **Template-Driven Extraction:** Structured extraction via shared JSON templates (GitHub, npm, PyPI, crates.io, docs pages, Docker Hub, and more). Supports custom inline templates.

---

## Usage & Installation

You do not need to install this repository manually. Configure your agent to use the zero-install commands `npx` or `uvx`.

### Claude Desktop Configuration

**Option A: Python (`uvx` - Recommended)**

```json
{
  "mcpServers": {
    "searchfetch": {
      "command": "uvx",
      "args": ["searchfetch"]
    }
  }
}
```

**Option B: Node.js (`npx`)**

```json
{
  "mcpServers": {
    "searchfetch": {
      "command": "npx",
      "args": ["-y", "searchfetch"]
    }
  }
}
```

### Cursor / IDE Configuration

Add via the **MCP panel** in Cursor settings:

- **Type:** `command`
- **Command:** `uvx searchfetch` (or `npx -y searchfetch`)

---

## Available Tools

### 1. `websearch`

Search the web through the template pipeline. DuckDuckGo and Google are built-in; custom search templates can be selected by name.

| Parameter     | Type         | Default        | Description                                                                                    |
| ------------- | ------------ | -------------- | ---------------------------------------------------------------------------------------------- |
| `query`       | string       | _required_     | The search query string.                                                                       |
| `engine`      | string       | `"duckduckgo"` | `"duckduckgo"`, `"google"`, or a custom search template name.                                  |
| `max_results` | number       | `10`           | Positive integer limit on extracted results.                                                   |
| `region`      | string/null  | `null`         | Region/language code (e.g. `"us-en"`, `"de-de"`). DDG maps directly; Google maps to `gl`/`hl`. |
| `safe_search` | boolean/null | `null`         | Enable safe search. `null` uses the template default.                                          |
| `block_media` | boolean      | `true`         | Block images, media, and fonts at the network layer.                                           |

### 2. `webfetch`

Fetch a page with the stealth browser and extract structured Markdown using a template. Falls back to generic Markdown extraction for unknown pages.

| Parameter     | Type    | Default    | Description                                           |
| ------------- | ------- | ---------- | ----------------------------------------------------- |
| `url`         | string  | _required_ | Full URL (must start with `http`/`https`).            |
| `template`    | string  | `"auto"`   | `"auto"`, a built-in name, or inline JSON template.   |
| `start_index` | number  | `0`        | Nonnegative integer offset in Unicode code points.    |
| `max_length`  | number  | `10000`    | Positive integer limit in Unicode code points.        |
| `block_media` | boolean | `true`     | Block images, videos, and fonts at the network layer. |

Template extraction supports `text`, `markdown`, `attribute`, and `html` formats; child fields within a section; repeated sections; URL-decoding transforms; per-template cookies; and per-template resource blocking.

Built-in templates live in `templates/*.json` and are shared by the Node.js and Python implementations.

**Available page templates** (auto-detected by URL or selectable by name):
`wikipedia`, `reddit`, `mdn-web-docs`, `gitlab`, `youtube`, `devto`, `go-pkg`, `javadoc`,
`github-repo`, `github-issue`, `npm-package`, `pypi-package`, `crates-package`,
`docker-hub`, `docs-rs`, `docs-page`

**`raw`** — special template that applies minimal filtering and returns full body content as markdown. Use when you need the complete page without template-specific extraction.

---

## Local Development

```bash
# Install dependencies
npm ci
uv sync --locked --extra dev

# Run tests
npm test                # runs all tests (JS + Python)
npm run test:js         # Node.js unit tests (built-in test runner)
npm run test:py         # Python unit tests (pytest)

# Lint
npm run lint            # runs all linters
npm run lint:js         # ESLint
npm run lint:py         # Ruff

# Format
npm run format          # auto-format all source files
npm run format:check    # check formatting without changes

# MCP inspector (for manual testing)
npm run inspector-js    # test with MCP Inspector (Node.js)
npm run inspector-py    # test with MCP Inspector (Python)
```

---

## Architecture

Both MCP servers expose `websearch` and `webfetch` over standard input/output:

1. Validate tool inputs and resolve a built-in or inline JSON template.
2. Reuse one browser, creating an isolated browser context for each fetch attempt.
3. Apply template cookies and resource blocking, navigate, check HTTP status, and allow up to five seconds for network activity to settle.
4. Remove configured elements, extract section fields, apply transforms, and compose Markdown. Page templates may first try a raw Markdown source URL.
5. Paginate page output using Unicode code points. Search requests can fall back from Google to DuckDuckGo HTML and then Lite; output names any fallback engine.

`index.js` and `server.py` contain the runtime-specific browser and MCP integration. `lib/` and `selectors_utils.py` hold focused formatting and selector helpers. Both runtimes read the same `templates/*.json`; Python wheels bundle these as `searchfetch_templates` resources. Shared fixtures in `tests/fixtures/` exercise extraction behavior in both runtimes.

Selectors separated by top-level commas are ordered fallbacks. Commas inside CSS functions or attributes are preserved; an empty fallback selects the current element. Child extraction searches descendants and enclosing elements, without borrowing fields from neighboring results. Missing required fields and malformed selectors report errors.

## Verification and limits

`npm test`, `npm run lint`, and `npm run format:check` check both runtimes. `npm run e2e` runs real browser requests against local HTTP fixtures, exercises every built-in template, and checks the installed npm executable and Python wheel. Public search-engine availability and changing third-party page layouts require separate live checks. A page that continues rendering beyond the bounded wait may return partial content.

`package-lock.json` and `uv.lock` record dependency resolution.
