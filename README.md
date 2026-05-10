# SearchFetch (MCP Server)

A maximum fault-tolerant, stealth-enabled Model Context Protocol (MCP) server for web searching and content fetching. Built specifically for AI Agents (Cursor, Claude Desktop, OpenHands), it completely bypasses bot detection (Cloudflare Turnstile, Datadome), dynamically handles SPAs/React, and converts bloat into token-optimized Markdown.

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
Searches the web using DuckDuckGo or Google. Returns a clean list of titles, URLs, and snippets. Excellent for researching general knowledge, news, and finding URLs.

**Parameters:**
* **`query`** *(string, required)*: The search query string.
* **`engine`** *(string, optional)*: Search engine to use. Can be `"duckduckgo"` or `"google"`. Default is `"duckduckgo"`.
* **`max_results`** *(number, optional)*: Maximum number of results to return. Default is `10`.
* **`region`** *(string, optional)*: Region and language code to localize search results. 
  * Examples: `"us-en"`, `"uk-en"`, `"de-de"`. 
  * For DuckDuckGo, it maps directly. 
  * For Google, it maps to the `gl` (country) and `hl` (language) query parameters automatically.
  * Default is `"wt-wt"` (global/US English).
* **`safe_search`** *(string, optional)*: Safe search filtering mode. 
  * `"-1"` for Moderate.
  * `"1"` for Strict. 
  * `"-2"` for Off. 
  * Default is `"-1"`. 
  * *Note: Only applies to DuckDuckGo.*

### 2. `webfetch`
Fetch and extract the main text content from any webpage. Fully executes JavaScript to load React/SPAs and aggressively strips images/media (including base64) to save context tokens.

**Parameters:**
* **`url`** *(string, required)*: The full URL of the webpage to fetch (must start with http/https).
* **`format`** *(string, optional)*: Output format. Set to `"markdown"`, `"clean_html"`, or `"raw_html"`. Default is `"markdown"` (highly recommended to save context tokens).
* **`start_index`** *(number, optional)*: Character offset to start reading from for pagination. Use this if a document is too large to fit in the context window. Default is `0`.
* **`max_length`** *(number, optional)*: Maximum characters to return per request. Default is `10000`.
* **`block_media`** *(boolean, optional)*: Block images, videos, and fonts entirely at the network layer to drastically speed up page loads and dodge tracking pixels. Default is `true`.

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
