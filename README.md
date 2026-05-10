# SearchFetch

A fault-tolerant, stealth-enabled Model Context Protocol (MCP) server for web searching and content fetching. Built specifically for AI Agents, it bypasses Google's GDPR consent screens, Cloudflare Turnstile, and converts heavy HTML into clean, token-optimized Markdown.

## Features
* **Multi-Engine Search:** Natively supports Google and DuckDuckGo parsing out of the box. Google is set as the preferred default.
* **Aggressive Base64 / Image Scrubber:** Implements "nuclear" DOM scrubbing prior to parsing. Guaranteed to NEVER pollute your LLM's context window with giant base64 image strings (`data:image/...`).
* **Stealth CloakBrowser:** Avoids FingerprintJS, reCAPTCHA, and Cloudflare using Chromium C++ patches and humanized mouse movements natively. 
* **SPA & React Support:** Waits for network idle to ensure modern Single Page Applications fully execute JavaScript and render before extracting content.
* **Fault Tolerant:** Extracts whatever DOM was successfully loaded even if a massive, clunky page times out mid-render.
* **Pagination Support:** Fetches massive webpages iteratively via `start_index` and `max_length` without blowing out AI context tokens limits.

## Installation

1. Clone or copy the directory.
  ```bash
  git clone https://github.com/maxylev/searchfetch
  ```
2. Install dependencies:
   ```bash
   npm install
   ```
3. Make the main script executable:
   ```bash
   chmod +x index.js
   ```
4. Link it globally to your system:
   ```bash
   npm link
   ```

## Configuration

Configure your AI tool/IDE (Cursor, Claude Desktop, Opencode, etc.) to point to this server.

### Example `config.json` (Opencode, Cursor):
```json
{
  "mcp": {
    "searchfetch": {
      "type": "local",
      "command":["npx", "searchfetch"],
      "enabled": true
    }
  }
}
```

### Example `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "searchfetch": {
      "command": "npx",
      "args": ["searchfetch"]
    }
  }
}
```

## Available Tools

### 1. `websearch`
Searches the web via Google or DuckDuckGo and returns structured snippets.
* **`query`** (string): Your search query.
* **`engine`** (string): `"google"` or `"duckduckgo"` (default `"duckduckgo"`).
* **`max_results`** (number): Number of results to return (default `10`).

### 2. `webfetch`
Visits a URL as a stealthy human, waits for the JS to render, completely scrubs visual assets/inline styles to save tokens, and returns the markdown content.
* **`url`** (string): Full HTTP/HTTPS link.
* **`format`** (string): Set to `"markdown"` (default), `"clean_html"`, or `"raw_html"`.
* **`start_index`** (number): Pagination offset.
* **`max_length`** (number): Maximum character length to return per call (default `10000`).
* **`block_media`** (boolean): Speeds up page loads by ignoring images, videos, and fonts entirely at the network layer (default `true`).

## Debugging
If you want to debug JSON-RPC shapes locally:
```bash
npm run inspector
```
