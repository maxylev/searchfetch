# SearchFetch (MCP Server)

A fault-tolerant, stealth-enabled Model Context Protocol (MCP) server for web searching and content fetching. Built for AI Agents (Cursor, Claude Code, OpenCode), it uses a stealth browser engine to fetch pages, dynamically handles SPAs/React, and converts bloat into token-optimized Markdown.

## Features

- **Stealth Engine:** Powered by CloakBrowser C++ patches + `humanize` logic. The browser renders and moves like a real user, reducing bot-detection scores.
- **Fault Tolerance:** Auto-healing browser sessions, grace-period timeouts for SPAs, and network-level blocking of tracking scripts and media.
- **Token-Optimized Output:** Strips base64 images, SVGs, scripts, and inline styles from the DOM _before_ Markdown conversion.
- **Dual Runtime:** Natively supports zero-install execution via both Python (`uvx`) and Node.js (`npx`).
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
| `max_results` | number       | `10`           | Maximum number of results to return.                                                           |
| `region`      | string/null  | `null`         | Region/language code (e.g. `"us-en"`, `"de-de"`). DDG maps directly; Google maps to `gl`/`hl`. |
| `safe_search` | boolean/null | `null`         | Enable safe search. `null` uses the template default.                                          |
| `block_media` | boolean      | `true`         | Block images, media, and fonts at the network layer.                                           |

### 2. `webfetch`

Fetch a page with the stealth browser and extract structured Markdown using a template. Falls back to generic Markdown extraction for unknown pages.

| Parameter     | Type    | Default    | Description                                           |
| ------------- | ------- | ---------- | ----------------------------------------------------- |
| `url`         | string  | _required_ | Full URL (must start with `http`/`https`).            |
| `template`    | string  | `"auto"`   | `"auto"`, a built-in name, or inline JSON template.   |
| `start_index` | number  | `0`        | Character offset for pagination.                      |
| `max_length`  | number  | `10000`    | Maximum characters per request.                       |
| `block_media` | boolean | `true`     | Block images, videos, and fonts at the network layer. |

Template extraction supports `text`, `markdown`, `attribute`, and `html` formats; nested children; repeated sections; URL-decoding transforms; per-template cookies; and per-template resource blocking.

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
npm install
pip install -e ".[dev]"

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

This repository uses a flat dual-manifest structure (`package.json` and `pyproject.toml` in the root). Both runtimes (`index.js` for Node, `server.py` for Python) share the same `templates/*.json` files and maintain feature parity.
