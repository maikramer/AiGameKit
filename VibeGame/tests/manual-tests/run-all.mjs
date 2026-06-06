// Runs every check-*.mjs in this folder sequentially and reports a summary.
// Assumes the simple-rpg Vite dev server is already running (see README).
import { readdirSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const checks = readdirSync(here)
  .filter((f) => f.startsWith('check-') && f.endsWith('.mjs'))
  .sort();

let failed = 0;
for (const file of checks) {
  console.log(`\n\x1b[1m▶ ${file}\x1b[0m`);
  const r = spawnSync(process.execPath, [join(here, file)], {
    stdio: 'inherit',
  });
  if (r.status !== 0) failed++;
}

console.log(
  `\n${failed === 0 ? '\x1b[32m' : '\x1b[31m'}${checks.length - failed}/${checks.length} checks passed\x1b[0m`
);
process.exit(failed === 0 ? 0 : 1);
