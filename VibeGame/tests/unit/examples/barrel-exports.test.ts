import { describe, expect, it } from 'bun:test';
import { lstatSync, readFileSync, readdirSync, statSync } from 'node:fs';
import * as path from 'node:path';

const ENGINE_ROOT = path.resolve(import.meta.dir, '../../..');
const EXAMPLES_ROOT = path.join(ENGINE_ROOT, 'examples');

function tsFilesUnder(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry === 'dist') continue;
    const full = path.join(dir, entry);
    // lstat: don't follow symlinks (examples/node_modules/vibegame → engine
    // would recurse into itself forever).
    if (lstatSync(full).isDirectory()) {
      out.push(...tsFilesUnder(full));
    } else if (entry.endsWith('.ts') && !entry.endsWith('.d.ts')) {
      out.push(full);
    }
  }
  return out;
}

/**
 * Export names of the `vibegame` barrel, read statically from src/index.ts
 * (importing the barrel at runtime pulls the whole plugin graph — heavy and
 * brittle in unit tests). Resolves `export { a, b as c } from ...`, `export
 * type { ... }`, `export const/function/class/interface/type X`, and follows
 * `export * from ...` chains into the core barrel.
 */
function collectExportNames(file: string, seen: Set<string>): Set<string> {
  if (seen.has(file)) return new Set();
  seen.add(file);
  const src = readFileSync(file, 'utf8');
  const names = new Set<string>();

  const blockRe =
    /export\s+type\s*\{([^}]*)\}|export\s*\{([^}]*)\}\s*from\s*'([^']+)';?/g;
  let m: RegExpExecArray | null;
  while ((m = blockRe.exec(src)) !== null) {
    const namesPart = (m[1] ?? m[2] ?? '').replace(/\/\/.*$/gm, '');
    const from = m[3];
    for (const raw of namesPart.split(',')) {
      const parts = raw.trim().split(/\s+as\s+/);
      if (!parts[0]) continue;
      // `export { x as y }` — the exported name is the LAST part.
      names.add(parts[parts.length - 1]!.trim());
    }
    if (from) {
      const target = resolveModulePath(file, from);
      if (target) {
        for (const n of collectExportNames(target, seen)) names.add(n);
      }
    }
  }

  const starRe = /export\s*\*\s*from\s*'([^']+)';?/g;
  while ((m = starRe.exec(src)) !== null) {
    const target = resolveModulePath(file, m[1]!);
    if (target) {
      for (const n of collectExportNames(target, seen)) names.add(n);
    }
  }

  const declRe =
    /export\s+(?:declare\s+)?(?:const|function|class|interface|type|enum)\s+([A-Za-z_$][\w$]*)/g;
  while ((m = declRe.exec(src)) !== null) {
    names.add(m[1]!);
  }
  return names;
}

/** Resolve a relative `from './x'` spec against a file, trying index.ts. */
function resolveModulePath(fromFile: string, spec: string): string | null {
  if (!spec.startsWith('.')) return null;
  const base = path.resolve(path.dirname(fromFile), spec);
  for (const candidate of [base + '.ts', base + '/index.ts', base + '.d.ts']) {
    try {
      if (statSync(candidate).isFile()) return candidate;
    } catch {
      // keep trying
    }
  }
  return null;
}

function barrelExportNames(): Set<string> {
  return collectExportNames(path.join(ENGINE_ROOT, 'src/index.ts'), new Set());
}

/** Names imported from `vibegame` (barrel or subpath) in a file. */
function vibegameImports(
  file: string
): Array<{ subpath: string; name: string }> {
  const src = readFileSync(file, 'utf8');
  const out: Array<{ subpath: string; name: string }> = [];
  const re =
    /import\s+type\s*\{([^}]*)\}\s*from\s*'vibegame([^']*)'|import\s*\{([^}]*)\}\s*from\s*'vibegame([^']*)'/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(src)) !== null) {
    const names = (m[1] ?? m[3] ?? '')
      .replace(/\/\/.*$/gm, '')
      .split(',')
      .map((s) => s.trim());
    const subpath = m[2] ?? m[4] ?? '';
    for (const raw of names) {
      if (!raw) continue;
      // `import { a as b }` — the requested export is `a`.
      const requested = raw.split(/\s+as\s+/)[0]!.trim();
      out.push({ subpath, name: requested });
    }
  }
  return out;
}

describe('examples import only real vibegame exports', () => {
  const files = tsFilesUnder(EXAMPLES_ROOT);
  const exports = barrelExportNames();

  it('discovers example sources to check', () => {
    expect(files.length).toBeGreaterThan(5);
  });

  it('barrel static parse finds a plausible export count', () => {
    // Sanity: the barrel exports hundreds of symbols; a parse regression
    // (regex broken) would collapse this to ~0 and silently pass everything.
    expect(exports.size).toBeGreaterThan(100);
  });

  for (const file of files) {
    const imports = vibegameImports(file);
    if (imports.length === 0) continue;
    const rel = path.relative(EXAMPLES_ROOT, file);

    it(`${rel} — every 'vibegame' import resolves to a real export`, () => {
      const missing = imports
        .filter(({ subpath }) => subpath === '')
        .filter(({ name }) => !exports.has(name))
        .map(({ name }) => name);
      expect(missing).toEqual([]);
    });
  }
});
