#!/usr/bin/env node
/**
 * Run bpy vegetation generator (Animator3D venv or ANIMATOR3D_PYTHON).
 *
 *   npm run generate-vegetation
 */
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const repoRoot = resolve(root, '../../..');
const script = join(here, 'generate_vegetation_glb.py');

const candidates = [
  process.env.ANIMATOR3D_PYTHON,
  join(repoRoot, 'Animator3D/.venv/bin/python'),
  join(repoRoot, 'Animator3D/.venv/Scripts/python.exe'),
].filter(Boolean);

const python = candidates.find((p) => existsSync(p));
if (!python) {
  process.stderr.write(
    '[generate-vegetation] bpy python not found.\n' +
      '  Set ANIMATOR3D_PYTHON or install Animator3D/.venv (pip bpy).\n'
  );
  process.exit(1);
}

process.stdout.write(`[generate-vegetation] ${python} ${script}\n`);
const r = spawnSync(python, [script], { stdio: 'inherit', cwd: root });
process.exit(r.status ?? 1);
