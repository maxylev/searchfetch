#!/usr/bin/env node
/** Command-line entry point: serve searchfetch over MCP stdio. */
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { ensureBinary } from "cloakbrowser";
import { BrowserManager } from "./browser.js";
import { createServer } from "./server.js";

/** stdout carries the MCP protocol, so route anything printed during setup to stderr. */
async function withStdoutOnStderr(task) {
  const { write } = process.stdout;
  const { log, info } = console;
  process.stdout.write = process.stderr.write.bind(process.stderr);
  console.log = console.info = console.error;
  try {
    return await task();
  } finally {
    process.stdout.write = write;
    Object.assign(console, { log, info });
  }
}

const browser = new BrowserManager();
let shuttingDown = false;

async function shutdown(code = 0) {
  if (shuttingDown) return;
  shuttingDown = true;
  try {
    await browser.close();
  } finally {
    process.exit(code);
  }
}

process.on("SIGINT", () => shutdown());
process.on("SIGTERM", () => shutdown());

try {
  await withStdoutOnStderr(() => ensureBinary());
  await createServer(browser).connect(new StdioServerTransport());
  process.stdin.once("end", () => shutdown());
} catch (err) {
  console.error(err);
  await shutdown(1);
}
