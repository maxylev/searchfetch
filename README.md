# SearchFetch

An [MCP](https://modelcontextprotocol.io) server that gives AI agents web search and page
fetching through a stealth browser. Pages are rendered with JavaScript, stripped of noise, and
returned as compact Markdown.

- **Browser rendering:** [CloakBrowser](https://github.com/CloakHQ/CloakBrowser) runs Chromium
  with humanized input. Each fetch uses an isolated browser context. Sites may still demand
  logins or challenges.
- **Fault tolerance:** relaunches a disconnected browser and retries timeouts, network failures
  and HTTP 429 (honoring `Retry-After`, capped at 30 s) once. Built-in search engines fall back
  to DuckDuckGo HTML and then DuckDuckGo Lite.
- **Token-efficient output:** scripts, styles, SVGs, inline styles and embedded `data:` images
  are removed before conversion; images, media and fonts are blocked at the network layer.
- **Template-driven extraction:** JSON templates for GitHub, GitLab, npm, PyPI, crates.io,
  docs.rs, Docker Hub, Wikipedia, MDN, Reddit, YouTube, DEV, pkg.go.dev, Javadoc and generic
  documentation sites. Custom templates can be passed inline.
- **Two runtimes:** Python (`uvx`, Python 3.11.5+) or Node.js (`npx`, Node 24+), with the same
  tools, templates and output. The first run downloads the browser binary.

## Setup

No manual install is needed. Point your MCP client at `uvx` or `npx`:

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

For Node.js, use `"command": "npx", "args": ["-y", "searchfetch"]`. In Cursor, add a `command`
server with `uvx searchfetch` or `npx -y searchfetch`.

## Tools

Both tools are read-only. Failures are returned as MCP tool errors.

### `websearch`

| Parameter     | Type         | Default        | Description                                                                       |
| ------------- | ------------ | -------------- | --------------------------------------------------------------------------------- |
| `query`       | string       | _required_     | Search query (must not be blank).                                                 |
| `engine`      | string       | `"duckduckgo"` | `"duckduckgo"`, `"google"`, a search template name, or an inline JSON template.   |
| `region`      | string/null  | `null`         | Region-language code such as `"us-en"`. DuckDuckGo: `kl`; Google: `gl` and `hl`.  |
| `safe_search` | boolean/null | `null`         | `true` or `false` sets the engine's safe search; `null` keeps the engine default. |
| `max_results` | integer      | `10`           | Maximum results (positive).                                                       |
| `block_media` | boolean      | `true`         | Block images, media and fonts at the network layer.                               |

Results are a numbered list of titles, URLs and snippets. When an engine fails or returns no
results, the next template in its `fallback` chain is tried (`google` → `duckduckgo-search` →
`duckduckgo-lite-search`), and a `[websearch: …]` note at the top of the output explains why.

### `webfetch`

| Parameter     | Type    | Default    | Description                                                         |
| ------------- | ------- | ---------- | ------------------------------------------------------------------- |
| `url`         | string  | _required_ | Absolute `http`/`https` URL.                                        |
| `template`    | string  | `"auto"`   | `"auto"` (detect by URL), a template name, or inline JSON template. |
| `start_index` | integer | `0`        | Start offset in Unicode code points (non-negative).                 |
| `max_length`  | integer | `10000`    | Maximum code points to return (positive).                           |
| `block_media` | boolean | `true`     | Block images, media and fonts at the network layer.                 |

Output ends with a footer naming the template and the character window, plus the next
`start_index` when more content remains. Pages that match no template are converted whole after
removing navigation, headers, footers, sidebars, dialogs and media (`template="auto (fallback)"`).
The `raw` template converts the full `<body>` with no removal.

## Templates

Built-in templates live in [`templates/`](templates/) and are shared by both runtimes. Page
templates: `github-repo`, `github-issue`, `npm-package`, `pypi-package`, `crates-package`,
`docs-rs`, `javadoc`, `docs-page`, `docker-hub`, `wikipedia`, `gitlab`, `reddit`,
`mdn-web-docs`, `devto`, `go-pkg`, `youtube`, `raw`. Search templates: `google-search`,
`duckduckgo-search`, `duckduckgo-lite-search`.

Templates are validated when they are loaded or passed inline:

| Field             | Description                                                                                      |
| ----------------- | ------------------------------------------------------------------------------------------------ |
| `name`            | Required unique name (inline templates default to `"custom"`).                                   |
| `sections`        | Required list of sections (see below).                                                           |
| `order`           | Precedence for `auto` detection; lower wins.                                                     |
| `url_patterns`    | Regular expressions (compatible with JavaScript and Python) that select the template for `auto`. |
| `remove`          | CSS selectors removed before extraction. Defaults to scripts, styles, SVG, nav, footer, iframes. |
| `source_url`      | Raw Markdown source tried first, e.g. `"{url}.md"`; HTML responses are ignored.                  |
| `cookies`         | Playwright cookies added to the browser context.                                                 |
| `block_resources` | Resource types blocked when `block_media` is on. Default: `image`, `media`, `font`.              |
| `url_template`    | Search URL with `{placeholders}`; makes the template usable by `websearch`.                      |
| `url_params`      | Per placeholder: `required`, `default`, and `encode: "url"`.                                     |
| `engine`          | `"duckduckgo"` or `"google"`: how `region` and `safe_search` map to URL parameters.              |
| `fallback`        | Name of the search template to try when this one fails.                                          |

Each section has a `name`, a `selector` (empty selects `<body>`), and optionally `format` (`text`, `markdown`,
`attribute` with `attribute`, or `html`), `transform` (one or a list of `strip`,
`decode_google_url`, `decode_ddg_url`, `json_parse`, `resolve_href`), `required`, `multiple`,
`max_items`, and `children` (fields extracted from inside each matched element).

Selectors separated by top-level commas are ordered fallbacks: the first selector that matches
wins, and an empty fallback selects the current element. Children search inside their parent
element and its ancestors, never neighbouring results, so a missing field stays missing. Missing
`required` sections or fields are reported as errors.

A records section whose first item has `Author`/`User` and `Comment`/`Body` fields is rendered as
a comment thread; other records render as indented `Key: value` blocks.

## Development

```bash
npm ci && uv sync --locked --extra dev
git config core.hooksPath .githooks   # optional: format, lint and unit tests before commits

npm test               # Node (node:test) and Python (pytest) unit tests
npm run lint           # ESLint and Ruff
npm run format         # Prettier and Ruff format (format:check to verify)
npm run e2e            # real browser against local fixtures, plus the packed npm and wheel builds
npm run check          # everything CI runs
npm run inspect:node   # MCP Inspector (inspect:py for Python)
```

### Layout

| Path                         | Contents                                                                  |
| ---------------------------- | ------------------------------------------------------------------------- |
| `src/`                       | Node.js runtime (`index.js` is the `searchfetch` executable).             |
| `searchfetch/`               | Python package (`python -m searchfetch` or the `searchfetch` script).     |
| `templates/`                 | Shared templates; bundled into the npm package and the Python wheel.      |
| `tests/fixtures/`            | Shared fixtures that keep both runtimes' extraction and output identical. |
| `tests/node`, `tests/python` | Unit tests.                                                               |
| `tests/e2e/`                 | MCP end-to-end tests over stdio against a local HTTP server.              |

The runtimes mirror each other module for module (`templates`, `extract`, `markdown`, `compose`,
`browser`, `server`). Any change to one should be made to the other, with a shared fixture if
the behavior is observable.

### Releasing

Bump `version` in both `package.json` and `pyproject.toml` (then `npm install` and `uv lock`),
commit, and push a matching tag:

```sh
git tag -a v4.0.1 -m "v4.0.1" && git push origin main v4.0.1
```

The Publish workflow checks that the tag matches both versions, runs lint and unit tests,
publishes to npm and PyPI, and then creates the GitHub Release with generated notes. To retry a
tag, run the workflow manually from the Actions tab with that tag.

### Limits

- HTML-to-Markdown conversion uses turndown (Node) and markdownify (Python). They agree on
  common content, but edge cases such as tables differ.
- After the DOM is ready, each fetch waits up to five seconds for network activity to settle.
  Pages that keep rendering after that may return partial content.
- E2E tests use local fixtures. Live search-engine availability and third-party page layouts
  change over time and need separate live checks.
