#!/usr/bin/env bash
set -euo pipefail

npm ci
npm run format:check
npm run lint
npm test
npm pack --dry-run
uv build
npm run e2e
