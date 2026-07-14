#!/usr/bin/env node
/**
 * Compress runtime GLB meshes with meshopt + vertex quantization + (optional)
 * topology simplification. Runs against the GLBs actually referenced by the
 * game (index.html + src/), so junk in public/ is never touched.
 *
 * Three profiles, tuned by asset type:
 *   - rigged  : meshopt + quantize + simplify ratio 0.65 (preserve skinning)
 *   - static  : meshopt + quantize + simplify ratio 0.40 (aggressive on props)
 *   - skip    : collision meshes + already-compressed files (tiny / idempotent)
 *
 * Idempotent: a GLB already carrying EXT_meshopt_compression is skipped, so the
 * script is safe to re-run. On first pass the original is kept alongside as
 * `<name>.orig` for rollback.
 *
 * NOTE on fetch-assets: scripts/fetch-assets.mjs only re-downloads when the
 * sentinel (public/assets/.assets-version) differs from assets.lock.json. As
 * long as that sentinel is untouched, compressed files persist. If you ever do
 * a fresh `npm run setup` (which wipes public/), re-run this script afterwards.
 *
 * Usage:
 *   node scripts/compress-glbs.mjs            # compress all referenced GLBs
 *   node scripts/compress-glbs.mjs --force    # re-compress even if already meshopt
 *   node scripts/compress-glbs.mjs --dry-run  # report what would happen, no writes
 */
import { NodeIO } from '@gltf-transform/core';
import {
  dedup,
  meshopt,
  prune,
  quantize,
  simplify,
  weld,
} from '@gltf-transform/functions';
import { EXTMeshoptCompression } from '@gltf-transform/extensions';
import { MeshoptDecoder, MeshoptEncoder, MeshoptSimplifier } from 'meshoptimizer';
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, statSync, writeFileSync, renameSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const publicDir = join(root, 'public');

const FORCE = process.argv.includes('--force');
const DRY = process.argv.includes('--dry-run');
// Optional positional filter: only process URLs containing this substring.
// e.g. `node compress-glbs.mjs form_cliff` or a comma list `form_cliff,medieval`.
const FILTER = process.argv
  .filter((a) => !a.endsWith('.mjs') && !a.startsWith('-') && !a.startsWith('/'))
  .slice(1)
  .flatMap((a) => a.split(','))
  .map((a) => a.trim())
  .filter(Boolean);

/** Collect every /assets/.../*.glb URL referenced by index.html or src/. */
function collectReferencedGlbs() {
  const urls = new Set();
  const grep = (file) => {
    if (!existsSync(file)) return;
    const txt = readFileSync(file, 'utf8');
    for (const m of txt.matchAll(/\/assets\/[^\s"'`)]+\.glb/g)) urls.add(m[0]);
  };
  grep(join(root, 'index.html'));
  // index.html is one big file, but src/ has per-file references too.
  const srcFiles = execFileSync('find', [join(root, 'src'), '-type', 'f'], {
    encoding: 'utf8',
  }).trim().split('\n');
  for (const f of srcFiles) grep(f);
  return [...urls].sort();
}

/** Read the JSON chunk of a GLB to detect existing compression / riggedness. */
function inspectGlb(path) {
  const buf = readFileSync(path);
  if (buf.length < 20 || buf.toString('ascii', 0, 4) !== 'glTF') return null;
  const jsonLen = buf.readUInt32LE(12);
  const json = JSON.parse(buf.toString('utf8', 20, 20 + jsonLen));
  const extsUsed = new Set(json.extensionsUsed ?? []);
  const hasSkin = (json.skins ?? []).length > 0;
  const hasAnim = (json.animations ?? []).length > 0;
  return {
    hasMeshopt: extsUsed.has('EXT_meshopt_compression'),
    rigged: hasSkin || hasAnim,
  };
}

function log(msg) {
  process.stdout.write(`${msg}\n`);
}

async function main() {
  log('initializing meshopt WASM…');
  await MeshoptEncoder.ready;
  await MeshoptSimplifier.ready;
  await MeshoptDecoder.ready;

  // Dependency keys must match what EXTMeshoptCompression / simplify() look
  // up internally — string keys, not constructor names. The decoder is needed
  // to re-read already-compressed GLBs on a forced re-run.
  const io = new NodeIO()
    .registerExtensions([EXTMeshoptCompression])
    .registerDependencies({
      'meshopt.encoder': MeshoptEncoder,
      'meshopt.simplifier': MeshoptSimplifier,
      'meshopt.decoder': MeshoptDecoder,
    });

  const urls = collectReferencedGlbs();
  const filtered = FILTER.length
    ? urls.filter((u) => FILTER.some((f) => u.includes(f)))
    : urls;
  log(`found ${urls.length} referenced GLB URLs${FILTER.length ? `; filtering to ${filtered.length} (--filter: ${FILTER.join(', ')})` : ''}`);

  let totalBefore = 0;
  let totalAfter = 0;
  let compressed = 0;
  let skippedAlready = 0;
  let skippedMissing = 0;
  let skippedCollision = 0;
  const report = [];

  for (const url of urls) {
    const path = join(publicDir, url);
    if (!existsSync(path)) {
      log(`  SKIP (missing): ${url}`);
      skippedMissing++;
      continue;
    }
    const beforeSize = statSync(path).size;
    totalBefore += beforeSize;

    const info = inspectGlb(path);
    if (!info) {
      log(`  SKIP (not a glTF binary): ${url}`);
      continue;
    }
    if (url.includes('_collision')) {
      // Collision meshes are already tiny convex hulls — no gain, and we must
      // never alter their shape (physics depends on exact geometry).
      skippedCollision++;
      totalAfter += beforeSize;
      report.push({ url, before: beforeSize, after: beforeSize, skipped: 'collision' });
      continue;
    }
    if (info.hasMeshopt && !FORCE) {
      log(`  SKIP (already meshopt): ${url}`);
      skippedAlready++;
      totalAfter += beforeSize;
      report.push({ url, before: beforeSize, after: beforeSize, skipped: 'already' });
      continue;
    }

    const profile = info.rigged ? 'rigged' : 'static';
    const ratio = info.rigged ? 0.65 : 0.4;
    const simpErr = info.rigged ? 0.001 : 0.01;
    const label = `${(beforeSize / 1048576).toFixed(2).padStart(7)}MB  [${profile.padEnd(7)}]`;

    if (DRY) {
      log(`  DRY  ${label}  ${url}`);
      continue;
    }

    // Keep a one-time original for rollback (cp -n = no-clobber, idempotent).
    const origPath = `${path}.orig`;
    if (!existsSync(origPath)) {
      execFileSync('cp', ['-n', path, origPath]);
    }

    try {
      const doc = await io.read(path);
      const transforms = [
        dedup(),
        weld({ tolerance: 0.0001 }),
        simplify({ simplifier: MeshoptSimplifier, ratio, error: simpErr, lockBorder: false }),
        prune({ keepAttributes: false }),
        // Quantize everything EXCEPT POSITION: the mesh-collider system loads
        // the same *_lod0.glb as a trimesh and rejects non-float32 POSITION
        // ("POSITION accessor must be float32 VEC3"). Float32 POSITION + meshopt
        // compresses just as well, so we keep POSITION untouched for safety.
        quantize({
          pattern: /^(TEXCOORD_0|JOINTS_0|WEIGHTS_0|NORMAL|TANGENT|COLOR_0)$/,
          quantizeTexcoord: 12,
          quantizeNormal: 8,
        }),
        meshopt({ encoder: MeshoptEncoder, level: 'medium' }),
      ];
      await doc.transform(...transforms);

      // Write to a temp then atomic rename to avoid half-written files.
      const tmpOut = `${path}.tmp.glb`;
      await io.write(tmpOut, doc);
      const afterSize = statSync(tmpOut).size;
      if (afterSize >= beforeSize) {
        // Compression made it bigger (rare on tiny meshes) — keep original.
        existsSync(tmpOut) && execFileSync('rm', ['-f', tmpOut]);
        log(`  KEEP ${label}  ${url}  (no reduction)`);
        totalAfter += beforeSize;
        report.push({ url, before: beforeSize, after: beforeSize, skipped: 'no-gain' });
        continue;
      }
      renameSync(tmpOut, path);
      totalAfter += afterSize;
      compressed++;
      const pct = ((1 - afterSize / beforeSize) * 100).toFixed(0).padStart(3);
      log(`  OK   ${label} -> ${(afterSize / 1048576).toFixed(2).padStart(7)}MB  (-${pct}%)  ${url}`);
      report.push({ url, before: beforeSize, after: afterSize });
    } catch (err) {
      log(`  FAIL ${label}  ${url}  -- ${err.message}`);
      existsSync(`${path}.tmp.glb`) && execFileSync('rm', ['-f', `${path}.tmp.glb`]);
      totalAfter += beforeSize;
      report.push({ url, before: beforeSize, after: beforeSize, skipped: `error:${err.message}` });
    }
  }

  log('');
  log('──────────────────────────────────────────────');
  log(`compressed:        ${compressed}`);
  log(`skipped (already): ${skippedAlready}`);
  log(`skipped (collision): ${skippedCollision}`);
  log(`skipped (missing): ${skippedMissing}`);
  log(`payload before:    ${(totalBefore / 1048576).toFixed(1)} MB`);
  log(`payload after:     ${(totalAfter / 1048576).toFixed(1)} MB`);
  const saved = totalBefore - totalAfter;
  log(`saved:             ${(saved / 1048576).toFixed(1)} MB (${totalBefore ? ((saved / totalBefore) * 100).toFixed(0) : 0}%)`);
  if (DRY) log('(dry-run — no files were modified)');

  // Persist a machine-readable report next to the script for audit.
  if (!DRY) {
    const reportPath = join(root, 'compress-report.json');
    writeFileSync(
      reportPath,
      JSON.stringify(
        { compressed, skippedAlready, skippedCollision, skippedMissing, totalBefore, totalAfter, files: report },
        null,
        2
      )
    );
    log(`report written to ${reportPath}`);
  }
}

main().catch((err) => {
  log(`FATAL: ${err.stack || err.message}`);
  process.exit(1);
});
