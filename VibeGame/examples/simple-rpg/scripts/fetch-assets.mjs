#!/usr/bin/env node
/**
 * Fetch the prebuilt asset bundle (unified `shared-assets-vN` release — one
 * bundle consumed by every example; this script pins it for simple-rpg).
 *
 * Generated binaries are kept out of git and published as a pinned GitHub
 * Release instead (see assets.lock.json). The shared pool
 * (examples/shared-assets/public/assets) is the single home for every
 * generated mesh/image/texture/sky — each example reads them through the
 * `vibegame({ sharedAssets })` vite plugin. This example's public/ keeps only
 * game-specific media (audio, icons, particles, terrain data).
 *
 * This script downloads that bundle, verifies its sha256, extracts it to a
 * staging dir (never over public/ directly) and installs everything in
 * fill-if-missing mode — pool trees and game media alike. The Release NEVER
 * overwrites pool or local content: both are canonical and may be newer than
 * the pinned bundle. No symlinks, no copies of pooled assets inside the
 * example.
 *
 * Download policy (safe for dev — never overwrites local work):
 *   1. --force (or FETCH_ASSETS_FORCE=1): always download (install is still
 *      fill-if-missing — existing files are kept everywhere).
 *   2. Sentinel matches lock.version: skip (already have the right version).
 *   3. Pool or asset folders have local files (dev in progress): skip with a hint.
 *   4. Everything empty (fresh checkout): auto-download.
 *
 * Zero dependencies: uses Node's global fetch + the system `tar`.
 */
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import {
  cpSync,
  existsSync,
  mkdtempSync,
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
const assetsDir = join(extractTo, 'assets');
const sentinel = join(assetsDir, '.assets-version');
const force =
  process.argv.includes('--force') || process.env.FETCH_ASSETS_FORCE === '1';

/** Pool canónico dos binários partilhados (examples/shared-assets). */
const poolDir = resolve(root, '../shared-assets/public/assets');

/**
 * Tarball trees that belong to the shared pool — every example reads them via
 * `vibegame({ sharedAssets })`; the example's public/ never holds copies.
 * `audio` lives in the pool too (combat/sfx packs are shared across games).
 */
const POOL_TREES = new Set(['audio', 'meshes', 'images', 'textures', 'sky']);

/** Pastas de media específica do jogo — instaladas em public/assets. */
const LOCAL_DIRS = ['icons', 'particles', 'terrain'];

function log(msg) {
  process.stdout.write(`[fetch-assets] ${msg}\n`);
}

function hasRealFiles(dir) {
  return (
    existsSync(dir) &&
    readdirSync(dir).some((f) => f !== '.gitkeep' && !f.startsWith('.'))
  );
}

function assetsPresent() {
  if (hasRealFiles(poolDir)) return true;
  for (const d of LOCAL_DIRS) {
    if (hasRealFiles(join(assetsDir, d))) return true;
  }
  return false;
}

/**
 * Preenche o pool a partir do staging ao nível de ficheiro — só copia o que o
 * pool ainda não tem. Um pool parcial mantém o que já tem e ganha o resto.
 */
function fillTree(staged, pool, rel) {
  let copied = 0;
  let kept = 0;
  for (const e of readdirSync(staged, { withFileTypes: true })) {
    const s = join(staged, e.name);
    const p = join(pool, e.name);
    const r = `${rel}/${e.name}`;
    if (e.isDirectory()) {
      const stats = fillTree(s, p, r);
      copied += stats.copied;
      kept += stats.kept;
    } else if (existsSync(p)) {
      kept += 1;
    } else {
      mkdirSync(dirname(p), { recursive: true });
      cpSync(s, p);
      copied += 1;
    }
  }
  return { copied, kept };
}

/**
 * Instala staging/assets em modo fill-if-missing: trees do pool
 * (audio/meshes/images/textures/sky) e media específica do jogo → só copia o
 * que ainda não existe. Ficheiros locais/pool existentes são canónicos e nunca
 * são sobrescritos — nem com --force.
 */
function installStaged(staged) {
  for (const entry of readdirSync(staged, { withFileTypes: true })) {
    const rel = entry.name;
    if (POOL_TREES.has(rel)) {
      const { copied, kept } = fillTree(
        join(staged, rel),
        join(poolDir, rel),
        rel
      );
      log(`${rel}/ → pool: ${copied} novos, ${kept} mantidos (canónicos)`);
      continue;
    }
    const src = join(staged, rel);
    const dst = join(assetsDir, rel);
    if (entry.isFile()) {
      if (existsSync(dst)) {
        log(`${rel} mantido (local é canónico)`);
      } else {
        mkdirSync(dirname(dst), { recursive: true });
        cpSync(src, dst);
        log(`${rel} ← release`);
      }
      continue;
    }
    const { copied, kept } = fillTree(src, dst, rel);
    log(`${rel}/ → local: ${copied} novos, ${kept} mantidos (canónicos)`);
  }
}

// Caso 1: --force → sempre baixar.
if (!force) {
  // Caso 2: sentinel com versão correta → skip.
  if (
    existsSync(sentinel) &&
    readFileSync(sentinel, 'utf8').trim() === lock.version
  ) {
    log(`assets ${lock.version} already present — skipping.`);
    process.exit(0);
  }
  // Caso 3: há ficheiros locais (dev em curso) → skip com hint.
  if (assetsPresent()) {
    log('local assets detected — skipping download (dev mode).');
    log(
      '  use --force or FETCH_ASSETS_FORCE=1 to re-download from the release'
    );
    log('  (install is fill-if-missing — local work is never overwritten).');
    process.exit(0);
  }
}

async function main() {
  const tmp = join(tmpdir(), `${lock.version}.tar.gz`);
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

  // Extrai para staging — nunca por cima de public/ nem do pool.
  writeFileSync(tmp, buf);
  const staging = mkdtempSync(join(tmpdir(), 'simple-rpg-assets-'));
  execFileSync('tar', ['-xzf', tmp, '-C', staging], { stdio: 'inherit' });
  rmSync(tmp, { force: true });

  installStaged(join(staging, 'assets'));
  rmSync(staging, { recursive: true, force: true });

  mkdirSync(dirname(sentinel), { recursive: true });
  writeFileSync(sentinel, `${lock.version}\n`);
  log(`installed (pool ← shared trees; public/assets ← game media) ✓`);
}

main().catch((err) => {
  log(`ERROR: ${err.message}`);
  log(
    'You can also regenerate assets with the GameAssets pipeline (needs a GPU).'
  );
  process.exit(1);
});
