#!/usr/bin/env node
/**
 * Fetch the prebuilt asset bundle for the simple-rpg example.
 *
 * GLB meshes, textures, terrain and audio are large binary blobs, so they are
 * kept out of git and published as a pinned GitHub Release instead (see
 * assets.lock.json). This script downloads that bundle, verifies its sha256,
 * extracts it to a staging dir and installs it into ./public — it never
 * extracts over public/ directly.
 *
 * Shared Crystal Vale packs (forest / village / infra / terrain, rock_mossy,
 * sky) are routed to the examples/shared-assets pool in fill-if-missing mode:
 * the Release never overwrites pool content (the pool is canonical and may be
 * newer). public/assets keeps symlinks into the pool, so a fresh clone ends up
 * with the pool populated and the shared packs symlinked.
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
  lstatSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  renameSync,
  rmSync,
  statSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
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

/** Pool canónico dos packs partilhados (examples/shared-assets). */
const poolDir = resolve(root, '../shared-assets/public/assets');

/** Packs cujos binários vivem no pool — nunca instalados direto em public/. */
const SHARED_PACKS = ['forest', 'village', 'infra', 'terrain'];

/** Ficheiros partilhados fora dos packs (caminhos relativos a assets/). */
const SHARED_FILES = new Set([
  'sky/sky.png',
  'images/props/rock_mossy.png',
  'meshes/props/rock_mossy_collision.glb',
  'meshes/props/rock_mossy_lod0.glb',
  'meshes/props/rock_mossy_lod1.glb',
  'meshes/props/rock_mossy_lod2.glb',
  'meshes/props/rock_mossy_precompute.json',
]);

/** Pastas de assets binários — se alguma tiver ficheiros além de .gitkeep,
 * assumimos que há trabalho de dev local e não devemos substituir. */
const ASSET_DIRS = [
  'textures',
  'icons',
  'audio',
  'sky',
  'terrain',
  'particles',
];

function log(msg) {
  process.stdout.write(`[fetch-assets] ${msg}\n`);
}

function assetsPresent() {
  for (const d of ASSET_DIRS) {
    const dir = join(assetsDir, d);
    if (!existsSync(dir)) continue;
    const real = readdirSync(dir).filter(
      (f) => f !== '.gitkeep' && !f.startsWith('.')
    );
    if (real.length > 0) return true;
  }
  return false;
}

const isSharedPackDir = (rel) => {
  const [head, pack] = rel.split('/');
  return (
    (head === 'meshes' || head === 'images') && SHARED_PACKS.includes(pack)
  );
};
const isSharedFile = (rel) => SHARED_FILES.has(rel);
const isRockMossy = (name) => name.startsWith('rock_mossy');
const hasRealFiles = (dir) =>
  existsSync(dir) &&
  readdirSync(dir).some((f) => f !== '.gitkeep' && !f.startsWith('.'));

/** rename com fallback de cópia (staging em tmpdir pode estar noutro device). */
function moveSync(src, dst) {
  mkdirSync(dirname(dst), { recursive: true });
  try {
    renameSync(src, dst);
  } catch {
    cpSync(src, dst, { recursive: true });
    rmSync(src, { recursive: true, force: true });
  }
}

/** Copia staged → destino sobrescrevendo homónimos, sem apagar extras locais. */
function mergeSync(src, dst) {
  mkdirSync(dirname(dst), { recursive: true });
  cpSync(src, dst, { recursive: true, force: true });
}

/** Preenche o pool a partir do staging — só se ainda não existir conteúdo. */
function fillPool(stagedPath, poolPath, label) {
  if (existsSync(poolPath)) {
    const st = lstatSync(poolPath);
    if (st.isFile() || hasRealFiles(poolPath)) {
      log(`pool já tem ${label} — mantido (canónico)`);
      return;
    }
    rmSync(poolPath, { recursive: true, force: true }); // diretório vazio
  }
  moveSync(stagedPath, poolPath);
  log(`pool ← ${label}`);
}

/** Shallow: mesmos ficheiros com mesmos tamanhos → assume igual. */
function localMatchesPool(localPath, poolPath) {
  const a = lstatSync(localPath);
  if (a.isSymbolicLink()) return true;
  const b = statSync(poolPath);
  if (a.isDirectory() !== b.isDirectory()) return false;
  if (!a.isDirectory()) return a.size === b.size;
  for (const name of readdirSync(localPath)) {
    const other = join(poolPath, name);
    if (!existsSync(other)) return false;
    if (!localMatchesPool(join(localPath, name), other)) return false;
  }
  return true;
}

/** Garante que assets/<rel> é um symlink → pool. Trabalho local divergente
 * do pool não é substituído (política "never overwrites local work"). */
function ensureSymlink(rel) {
  const linkPath = join(assetsDir, rel);
  const poolPath = join(poolDir, rel);
  if (!existsSync(poolPath)) {
    log(`symlink ${rel} skip — pool sem ${rel}`);
    return;
  }
  if (existsSync(linkPath) && lstatSync(linkPath).isSymbolicLink()) return;
  if (existsSync(linkPath)) {
    if (!localMatchesPool(linkPath, poolPath)) {
      log(`AVISO: ${rel} local difere do pool — mantido como está`);
      return;
    }
    rmSync(linkPath, { recursive: true, force: true });
  } else {
    mkdirSync(dirname(linkPath), { recursive: true });
  }
  symlinkSync(relative(dirname(linkPath), poolPath), linkPath);
  log(`symlink ${rel} → pool`);
}

/**
 * Instala staging/assets respeitando o layout partilhado:
 * meshes/images <pack> + rock_mossy + sky.png → pool (fill-if-missing);
 * vegetation versionada fica; tudo o resto é merge direto em public/assets.
 */
function installStaged(staged) {
  for (const entry of readdirSync(staged, { withFileTypes: true })) {
    const rel = entry.name;

    if (entry.isDirectory() && (rel === 'meshes' || rel === 'images')) {
      for (const pack of readdirSync(join(staged, rel), {
        withFileTypes: true,
      })) {
        installPackEntry(
          join(staged, rel, pack.name),
          `${rel}/${pack.name}`,
          pack
        );
      }
      continue;
    }

    if (entry.isDirectory() && rel === 'sky') {
      for (const f of readdirSync(join(staged, rel))) {
        const fileRel = `sky/${f}`;
        if (isSharedFile(fileRel)) {
          fillPool(join(staged, fileRel), join(poolDir, fileRel), fileRel);
        } else {
          mergeSync(join(staged, fileRel), join(assetsDir, fileRel));
          log(`${fileRel} ← release`);
        }
      }
      continue;
    }

    if (entry.isFile() && isSharedFile(rel)) {
      fillPool(join(staged, rel), join(poolDir, rel), rel);
      continue;
    }

    const stagedPath = join(staged, rel);
    if (entry.isFile()) {
      mergeSync(stagedPath, join(assetsDir, rel));
      log(`${rel} ← release`);
    } else {
      mergeSync(stagedPath, join(assetsDir, rel));
      log(`${rel}/ ← release`);
    }
  }

  // Symlinks no fim — o pool já está preenchido.
  for (const pack of SHARED_PACKS) {
    ensureSymlink(`meshes/${pack}`);
    ensureSymlink(`images/${pack}`);
  }
  for (const f of SHARED_FILES) ensureSymlink(f);
}

function installPackEntry(stagedPath, rel, entry) {
  if (entry.isDirectory()) {
    if (isSharedPackDir(rel)) {
      fillPool(stagedPath, join(poolDir, rel), rel);
      return;
    }
    if (rel === 'meshes/vegetation') {
      // bpy carpet (tracked in git) — release tarball still has Kenney stubs;
      // keep the local/versioned copy across install.
      if (hasRealFiles(join(assetsDir, rel))) {
        log('meshes/vegetation mantida (versionada)');
      } else {
        mergeSync(stagedPath, join(assetsDir, rel));
        log('meshes/vegetation/ ← release');
      }
      return;
    }
    if (rel === 'meshes/props' || rel === 'images/props') {
      // Pasta mista: rock_mossy é shared; o resto é identidade do jogo.
      for (const f of readdirSync(stagedPath)) {
        const fileRel = `${rel}/${f}`;
        if (isRockMossy(f)) {
          fillPool(join(stagedPath, f), join(poolDir, fileRel), fileRel);
        } else {
          mergeSync(join(stagedPath, f), join(assetsDir, fileRel));
        }
      }
      log(`${rel}/ ← release (rock_mossy → pool)`);
      return;
    }
    mergeSync(stagedPath, join(assetsDir, rel));
    log(`${rel}/ ← release`);
    return;
  }

  if (isSharedFile(rel)) {
    fillPool(stagedPath, join(poolDir, rel), rel);
    return;
  }
  mergeSync(stagedPath, join(assetsDir, rel));
  log(`${rel} ← release`);
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
      '  use --force or FETCH_ASSETS_FORCE=1 to re-download from the release.'
    );
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

  // Extrai para staging — nunca por cima de public/ (symlinks do pool).
  writeFileSync(tmp, buf);
  const staging = mkdtempSync(join(tmpdir(), 'simple-rpg-assets-'));
  execFileSync('tar', ['-xzf', tmp, '-C', staging], { stdio: 'inherit' });
  rmSync(tmp, { force: true });

  installStaged(join(staging, 'assets'));
  rmSync(staging, { recursive: true, force: true });

  mkdirSync(dirname(sentinel), { recursive: true });
  writeFileSync(sentinel, `${lock.version}\n`);
  log(`installed to ${assetsDir} ✓ (shared packs → pool)`);
}

main().catch((err) => {
  log(`ERROR: ${err.message}`);
  log(
    'You can also regenerate assets with the GameAssets pipeline (needs a GPU).'
  );
  process.exit(1);
});
