#!/usr/bin/env node
// Cloudspin is a Python app. This launcher finds a way to run it and hands
// over the terminal: an installed `cloudspin`, then uv, then pipx.
"use strict";

const { spawnSync } = require("node:child_process");

const args = process.argv.slice(2);
const self = require("node:fs").realpathSync(__filename);

function onPath(command) {
  const probe = spawnSync(process.platform === "win32" ? "where" : "which", ["-a", command], { encoding: "utf8" });
  if (probe.status !== 0) return [];
  return probe.stdout.split(/\r?\n/).filter(Boolean);
}

function run(command, commandArgs) {
  const result = spawnSync(command, commandArgs, { stdio: "inherit" });
  if (result.error) return null;
  return result.status === null ? 1 : result.status;
}

// An already installed Python cloudspin, skipping this launcher itself.
const installed = onPath("cloudspin").find((candidate) => {
  try {
    return require("node:fs").realpathSync(candidate) !== self;
  } catch {
    return false;
  }
});

const attempts = [];
if (installed) attempts.push([installed, args]);
if (onPath("uvx").length) attempts.push(["uvx", ["cloudspin", ...args]]);
if (onPath("pipx").length) attempts.push(["pipx", ["run", "cloudspin", ...args]]);

for (const [command, commandArgs] of attempts) {
  const status = run(command, commandArgs);
  if (status !== null) process.exit(status);
}

console.error(
  [
    "cloudspin needs Python to run, and no way to start it was found.",
    "Install one of these, then try again:",
    "  uv    https://docs.astral.sh/uv/   (then: uvx cloudspin)",
    "  pipx  https://pipx.pypa.io/        (then: pipx install cloudspin)",
  ].join("\n")
);
process.exit(1);
