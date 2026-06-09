#!/usr/bin/env node
/**
 * Fetch the prebuilt asset bundle for the simple-rpg example.
 *
 * GLB meshes, textures, terrain and audio are large binary blobs, so they are
 * kept out of git and published as a pinned GitHub Release instead (see
 * assets.lock.json). This script downloads that bundle, verifies its sha256,
 * and extracts it into ./public — idempotently, so it is safe to run on every
 * `dev`/`build` (it no-ops once the pinned version is present).
 *
 * Zero dependencies: uses Node's global fetch + the system `tar`.
 */
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { tmpdir } from "node:os";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");
const lock = JSON.parse(readFileSync(join(root, "assets.lock.json"), "utf8"));

const extractTo = resolve(root, lock.extractTo);
const sentinel = join(extractTo, "assets", ".assets-version");

function log(msg) {
  process.stdout.write(`[fetch-assets] ${msg}\n`);
}

if (existsSync(sentinel) && readFileSync(sentinel, "utf8").trim() === lock.version) {
  log(`assets ${lock.version} already present — skipping.`);
  process.exit(0);
}

const tmp = join(tmpdir(), `${lock.version}.tar.gz`);

async function main() {
  log(`downloading ${lock.version} …`);
  const res = await fetch(lock.url, { redirect: "follow" });
  if (!res.ok) throw new Error(`download failed: HTTP ${res.status} ${lock.url}`);
  const buf = Buffer.from(await res.arrayBuffer());

  const sha = createHash("sha256").update(buf).digest("hex");
  if (sha !== lock.sha256) {
    throw new Error(`checksum mismatch: expected ${lock.sha256}, got ${sha}`);
  }
  log(`checksum ok (${(buf.length / 1048576).toFixed(1)} MB).`);

  writeFileSync(tmp, buf);
  mkdirSync(extractTo, { recursive: true });
  // tar ships with Linux, macOS and Windows 10+.
  execFileSync("tar", ["-xzf", tmp, "-C", extractTo], { stdio: "inherit" });
  rmSync(tmp, { force: true });

  mkdirSync(dirname(sentinel), { recursive: true });
  writeFileSync(sentinel, `${lock.version}\n`);
  log(`extracted to ${extractTo}/assets ✓`);
}

main().catch((err) => {
  log(`ERROR: ${err.message}`);
  log("You can also regenerate assets with the GameAssets pipeline (needs a GPU).");
  process.exit(1);
});
