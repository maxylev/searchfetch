#!/usr/bin/env bash
# Run the same checks as CI (.github/workflows/ci.yml) locally.
set -euo pipefail
cd "$(dirname "$0")/.."

npm ci
uv sync --locked --extra dev
npm run format:check
npm run lint
npm test
npm pack --dry-run
uv build
npm run e2e
