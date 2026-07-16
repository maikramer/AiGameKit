#!/usr/bin/env node
/**
 * Fetch the prebuilt asset bundle for the simple-rpg example.
 *
 * GLB meshes, textures, terrain and audio are large binary blobs, so they are
 * kept out of git and published as a pinned GitHub Release instead (see
 * assets.lock.json). This script downloads that bundle, verifies its sha256,
 * and extracts it into ./public.
 *
 * Download policy (safe for dev — never overwrites local work):
 *   1. --force (or FETCH_ASSETS_FORCE=1): always download.
 *   2. Sentinel matches lock.version: skip (already have the right version).
 *   3. Asset folders have local files (dev in progress): skip with a hint.
 *   4. Asset folders empty (fresh checkout): auto-download.
 *
 * Zero dependencies: uses Node's global fetch + the system `tar`.
 */
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import {
  cpSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const lock = JSON.parse(readFileSync(join(root, 'assets.lock.json'), 'utf8'));

const extractTo = resolve(root, lock.extractTo);
const sentinel = join(extractTo, 'assets', '.assets-version');
const force = process.argv.includes('--force') || process.env.FETCH_ASSETS_FORCE === '1';

/** Pastas de assets binários — se alguma tiver ficheiros além de .gitkeep,
 * assumimos que há trabalho de dev local e não devemos substituir. */
const ASSET_DIRS = ['textures', 'icons', 'audio', 'sky', 'terrain', 'particles'];

function log(msg) {
  process.stdout.write(`[fetch-assets] ${msg}\n`);
}

function assetsPresent() {
  for (const d of ASSET_DIRS) {
    const dir = join(extractTo, 'assets', d);
    if (!existsSync(dir)) continue;
    const real = readdirSync(dir).filter((f) => f !== '.gitkeep' && !f.startsWith('.'));
    if (real.length > 0) return true;
  }
  return false;
}

// Caso 1: --force → sempre baixar.
if (!force) {
  // Caso 2: sentinel com versão correta → skip.
  if (existsSync(sentinel) && readFileSync(sentinel, 'utf8').trim() === lock.version) {
    log(`assets ${lock.version} already present — skipping.`);
    process.exit(0);
  }
  // Caso 3: há ficheiros locais (dev em curso) → skip com hint.
  if (assetsPresent()) {
    log('local assets detected — skipping download (dev mode).');
    log('  use --force or FETCH_ASSETS_FORCE=1 to re-download from the release.');
    process.exit(0);
  }
}

const tmp = join(tmpdir(), `${lock.version}.tar.gz`);

async function main() {
  log(`downloading ${lock.version} …`);
  const res = await fetch(lock.url, { redirect: 'follow' });
  if (!res.ok)
    throw new Error(`download failed: HTTP ${res.status} ${lock.url}`);
  const buf = Buffer.from(await res.arrayBuffer());

  const sha = createHash('sha256').update(buf).digest('hex');
  if (sha !== lock.sha256) {
    throw new Error(`checksum mismatch: expected ${lock.sha256}, got ${sha}`);
  }
  log(`checksum ok (${(buf.length / 1048576).toFixed(1)} MB).`);

  writeFileSync(tmp, buf);
  mkdirSync(extractTo, { recursive: true });

  // bpy carpet (tracked in git) — release tarball still has Kenney stubs;
  // keep local/versioned vegetation across extract.
  const vegDir = join(extractTo, 'assets', 'meshes', 'vegetation');
  const vegBak = join(tmpdir(), 'simple-rpg-vegetation-preserve');
  let preservedVeg = false;
  if (existsSync(vegDir) && readdirSync(vegDir).some((f) => f.endsWith('.glb'))) {
    rmSync(vegBak, { recursive: true, force: true });
    cpSync(vegDir, vegBak, { recursive: true });
    preservedVeg = true;
    log('preserving meshes/vegetation/ across extract.');
  }

  // tar ships with Linux, macOS and Windows 10+.
  execFileSync('tar', ['-xzf', tmp, '-C', extractTo], { stdio: 'inherit' });
  rmSync(tmp, { force: true });

  if (preservedVeg && existsSync(vegBak)) {
    mkdirSync(dirname(vegDir), { recursive: true });
    rmSync(vegDir, { recursive: true, force: true });
    cpSync(vegBak, vegDir, { recursive: true });
    rmSync(vegBak, { recursive: true, force: true });
    log('restored meshes/vegetation/ (bpy carpet).');
  }

  mkdirSync(dirname(sentinel), { recursive: true });
  writeFileSync(sentinel, `${lock.version}\n`);
  log(`extracted to ${extractTo}/assets ✓`);
}

main().catch((err) => {
  log(`ERROR: ${err.message}`);
  log(
    'You can also regenerate assets with the GameAssets pipeline (needs a GPU).'
  );
  process.exit(1);
});
