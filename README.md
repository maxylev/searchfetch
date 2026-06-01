# SearchFetch (MCP Server)

A maximum fault-tolerant, stealth-enabled Model Context Protocol (MCP) server for web searching and content fetching. Built specifically for AI Agents (Cursor, Claude Code, OpenCode), it completely bypasses bot detection (Cloudflare Turnstile, Datadome), dynamically handles SPAs/React, and converts bloat into token-optimized Markdown.

## Features
* **Maximum Fault Tolerance:** Implements auto-healing browser sessions, grace-period timeouts for clunky SPAs, and network-level aborting of tracking scripts and media.
* **Stealth Engine:** Powered by CloakBrowser C++ patches + `humanize` logic. Antibot systems score it as a normal browser because it mathematically moves and renders exactly like one.
* **Nuclear Token Scrubber:** Strips Base64 images, SVGs, scripts, and inline styles out of the DOM *before* Markdown conversion, guaranteeing your LLM context window won't blow out.
* **Dual Execution Paths:** Natively supports zero-install execution via both Python (`uvx`) and Node.js (`npx`).

---

## Usage & Installation

You do not need to install this repository manually. Configure your agent to use the zero-install commands `npx` or `uvx` depending on your environment.

### Claude Desktop Configuration
Add the following to your config:

**Option A: Using Python (`uvx` - Recommended)**
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

**Option B: Using Node.js (`npx`)**
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
Add it via the **MCP panel** in Cursor settings:
* **Type:** `command`
* **Command:** `uvx searchfetch` (or `npx -y searchfetch`)

---

## Available Tools

### 1. `websearch`
Searches the web through the v3 template pipeline. DuckDuckGo and Google are built-in templates, and custom search templates can be selected by name.

**Parameters:**
* **`query`** *(string, required)*: The search query string.
* **`engine`** *(string, optional)*: Search engine/template to use. Can be `"duckduckgo"`, `"google"`, or a custom search template name. Default is `"duckduckgo"`.
* **`max_results`** *(number, optional)*: Maximum number of results to return. Default is `10`.
* **`region`** *(string/null, optional)*: Region and language code to localize search results. 
  * Examples: `"us-en"`, `"uk-en"`, `"de-de"`. 
  * For DuckDuckGo, it maps directly. 
  * For Google, it maps to the `gl` (country) and `hl` (language) query parameters automatically.
  * `null` uses the template default.
* **`safe_search`** *(boolean/null, optional)*: Enable safe search. Maps to DuckDuckGo/Google parameters automatically; `null` uses the template default.
* **`block_media`** *(boolean, optional)*: Block images, media, and fonts at the network layer. Default is `true`.

### 2. `webfetch`
Fetches a page with CloakBrowser and extracts structured Markdown using a named, inline, or auto-detected template. Built-ins include GitHub repositories/issues, npm, PyPI, crates.io, and ReadTheDocs-style docs pages. Unknown pages fall back to generic Markdown extraction.

**Parameters:**
* **`url`** *(string, required)*: The full URL of the webpage to fetch (must start with http/https).
* **`template`** *(string, optional)*: `"auto"`, a built-in template name, or inline JSON template. Default is `"auto"`.
* **`start_index`** *(number, optional)*: Character offset to start reading from for pagination. Use this if a document is too large to fit in the context window. Default is `0`.
* **`max_length`** *(number, optional)*: Maximum characters to return per request. Default is `10000`.
* **`block_media`** *(boolean, optional)*: Block images, videos, and fonts entirely at the network layer to drastically speed up page loads and dodge tracking pixels. Default is `true`.

Template extraction supports `text`, `markdown`, `attribute`, and `html` sections; nested children; repeated sections; URL decoding transforms; per-template cookies; and per-template resource blocking.

Built-in templates live in `templates/*.json` and are shared by the Node.js and Python implementations. Each JSON file defines exactly one template — no duplication between languages.

---

## Architecture & Contributions
This repository utilizes a flat dual-manifest file structure (`package.json` and `pyproject.toml` in the root). When committing changes, ensure parity between `index.js` and `server.py` logic.

### Local Development
```bash
# Node.js Testing
npm i
npm run inspector-js

# Python Testing
pip install -e .
npm run inspector-py
```
