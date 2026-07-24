#!/usr/bin/env bun
/**
 * vibegame analyze — offline “compile” of world XML/HTML.
 *
 * Usage:
 *   vibegame analyze [entry] [--public-dir <dir>] [--json] [--fail-on warn|error]
 */
import { existsSync } from 'node:fs';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';
import { formatJsonReport, formatTextReport, shouldFail } from './report';
import { analyzeWorld } from './run';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const engineRoot = path.resolve(__dirname, '../../..');

function printHelp(): void {
  console.log(`vibegame analyze — offline world compile

Usage:
  vibegame analyze [entry] [--public-dir <dir>] [--json] [--fail-on warn|error]

Arguments:
  entry              index.html or world XML (default: ./index.html, or
                     examples/simple-rpg/index.html when cwd is the engine)

Options:
  --public-dir DIR   Site public/ root for Includes and /assets (default: next to entry or ./public)
  --json             Emit JSON report
  --fail-on LEVEL    error (default) | warn — exit 1 when that severity appears
  -h, --help         This message
`);
}

function resolveDefaultEntry(cwd: string): string {
  const local = path.join(cwd, 'index.html');
  if (existsSync(local)) return local;
  const rpg = path.join(engineRoot, 'examples', 'simple-rpg', 'index.html');
  if (existsSync(rpg) && path.resolve(cwd) === engineRoot) return rpg;
  return local;
}

function resolvePublicDir(entry: string, explicit: string | null): string {
  if (explicit) return path.resolve(explicit);
  const beside = path.join(path.dirname(entry), 'public');
  if (existsSync(beside)) return beside;
  const cwdPub = path.join(process.cwd(), 'public');
  if (existsSync(cwdPub)) return cwdPub;
  return beside;
}

export async function main(
  argv: string[] = process.argv.slice(2)
): Promise<number> {
  let entry: string | null = null;
  let publicDir: string | null = null;
  let json = false;
  let failOn: 'error' | 'warn' = 'error';

  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]!;
    if (a === '-h' || a === '--help') {
      printHelp();
      return 0;
    }
    if (a === '--json') {
      json = true;
      continue;
    }
    if (a === '--fail-on' && argv[i + 1]) {
      const v = argv[++i]!.toLowerCase();
      if (v !== 'error' && v !== 'warn') {
        console.error(`[analyze] invalid --fail-on ${v} (use error|warn)`);
        return 2;
      }
      failOn = v;
      continue;
    }
    if ((a === '--public-dir' || a === '-p') && argv[i + 1]) {
      publicDir = argv[++i]!;
      continue;
    }
    if (a.startsWith('-')) {
      console.error(`[analyze] unknown flag ${a}`);
      return 2;
    }
    if (!entry) entry = a;
  }

  const resolvedEntry = path.resolve(
    entry ?? resolveDefaultEntry(process.cwd())
  );
  const resolvedPublic = resolvePublicDir(resolvedEntry, publicDir);

  const result = await analyzeWorld({
    entry: resolvedEntry,
    publicDir: resolvedPublic,
    failOn,
  });

  if (json) console.log(formatJsonReport(result));
  else console.log(formatTextReport(result));

  return shouldFail(result, failOn) ? 1 : 0;
}

if (import.meta.main) {
  main().then((code) => process.exit(code));
}
