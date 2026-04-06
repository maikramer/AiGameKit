#!/usr/bin/env node
/**
 * CLI `vibegame` — ponto de entrada estável para o instalador unificado do monorepo.
 * Usa apenas Node (stdlib); `bun install` / `bun run build` são responsabilidade do instalador.
 */
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, '..');
const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
const createScript = join(root, 'create-vibegame', 'index.js');

function help() {
  console.log(`vibegame ${pkg.version} — VibeGame (GameDev monorepo)`);
  console.log('');
  console.log('  vibegame create <name>   Create a new project (scaffold)');
  console.log('  vibegame playwright …    Run Playwright CLI (alias: pw)');
  console.log('  vibegame --version       Show version');
  console.log('  vibegame help            This message');
  console.log('');
  console.log('Playwright examples (from monorepo root with devDependencies installed):');
  console.log('  vibegame pw test');
  console.log('  vibegame pw test tests/playwright/simple-rpg-smoke.spec.ts');
  console.log('  vibegame pw test --ui');
  console.log('  vibegame pw install chromium');
  console.log('');
  console.log('See playwright.config.ts for PLAYWRIGHT_CDP_WS, PLAYWRIGHT_CDP_URL, PLAYWRIGHT_BASE_URL.');
  console.log('');
  console.log(`Library root: ${root}`);
}

/** @returns {string | null} */
function getLocalPlaywrightBin() {
  const binDir = join(root, 'node_modules', '.bin');
  if (process.platform === 'win32') {
    const cmd = join(binDir, 'playwright.cmd');
    return existsSync(cmd) ? cmd : null;
  }
  const sh = join(binDir, 'playwright');
  return existsSync(sh) ? sh : null;
}

function hasPlaywrightConfig() {
  return existsSync(join(root, 'playwright.config.ts'));
}

/**
 * @param {string[]} pwArgs arguments passed to the playwright CLI
 * @returns {never}
 */
function runPlaywright(pwArgs) {
  const localBin = getLocalPlaywrightBin();
  const hasConfig = hasPlaywrightConfig();

  if (!localBin && !hasConfig) {
    console.error(
      'vibegame playwright: no playwright.config.ts in the package root and no local Playwright CLI.',
    );
    console.error(`  Root: ${root}`);
    console.error('  Install dev dependencies in the VibeGame monorepo folder (e.g. bun install), then:');
    console.error('    bun run playwright:install');
    process.exit(1);
  }

  const opts = {
    cwd: root,
    stdio: 'inherit',
    env: process.env,
  };

  if (localBin) {
    const useShell = process.platform === 'win32' && localBin.endsWith('.cmd');
    const r = spawnSync(localBin, pwArgs, useShell ? { ...opts, shell: true } : opts);
    process.exit(r.status ?? 1);
  }

  const tryBunx = spawnSync('bunx', ['playwright', ...pwArgs], { ...opts, shell: true });
  if (!tryBunx.error) {
    process.exit(tryBunx.status ?? 1);
  }

  const tryNpx = spawnSync('npx', ['--yes', 'playwright', ...pwArgs], { ...opts, shell: true });
  if (!tryNpx.error) {
    process.exit(tryNpx.status ?? 1);
  }

  console.error('vibegame playwright: could not run Playwright (no local CLI, bunx, or npx).');
  console.error('  In the VibeGame repo: bun install && bun run playwright:install');
  process.exit(1);
}

const argv = process.argv.slice(2);
const first = argv[0];

if (first === 'create' || first === '-c') {
  const rest = argv.slice(1);
  const r = spawnSync(process.execPath, [createScript, ...rest], {
    stdio: 'inherit',
    cwd: process.cwd(),
  });
  process.exit(r.status ?? 1);
}

if (first === 'playwright' || first === 'pw') {
  const pwArgs = argv.slice(1);
  runPlaywright(pwArgs);
}

if (first === '--version' || first === '-v') {
  console.log(pkg.version);
  process.exit(0);
}

if (
  first === 'help' ||
  first === '--help' ||
  first === '-h' ||
  first === undefined
) {
  help();
  process.exit(first === undefined ? 0 : 0);
}

console.error(`Unknown command: ${first}`);
console.error('Try: vibegame help');
process.exit(1);
