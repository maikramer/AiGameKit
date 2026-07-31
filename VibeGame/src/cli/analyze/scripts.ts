import { existsSync, readdirSync } from 'node:fs';
import * as path from 'node:path';
import type { ParsedElement, XMLValue } from '../../core';
import type { AnalyzeIssue } from './types';

function attrStr(v: XMLValue | undefined): string | null {
  if (typeof v === 'string' && v.trim() !== '') return v.trim();
  return null;
}

/**
 * Auto-detect entity-script directory next to the game entry / public root.
 */
export function resolveScriptsDir(
  entry: string,
  publicDir: string,
  explicit: string | null | undefined
): string | null {
  if (explicit) {
    const resolved = path.resolve(explicit);
    return existsSync(resolved) ? resolved : null;
  }
  const candidates = [
    path.join(path.dirname(entry), 'src', 'scripts'),
    path.join(path.dirname(publicDir), 'src', 'scripts'),
    path.join(path.dirname(entry), 'scripts'),
  ];
  for (const c of candidates) {
    if (existsSync(c)) return c;
  }
  return null;
}

function listScriptFiles(dir: string): string[] {
  const out: string[] = [];
  const walk = (d: string) => {
    let entries;
    try {
      entries = readdirSync(d, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      const full = path.join(d, e.name);
      if (e.isDirectory()) walk(full);
      else if (/\.(ts|tsx|js|mjs)$/i.test(e.name)) out.push(full);
    }
  };
  walk(dir);
  return out;
}

/** Match like resolveEntityScriptGlobKey: basename or …/file. */
function scriptExists(
  scriptsDir: string,
  files: string[],
  ref: string
): boolean {
  const f = ref.trim();
  if (!f) return false;
  const abs = path.isAbsolute(f) ? f : path.join(scriptsDir, f);
  if (existsSync(abs)) return true;
  return files.some((full) => {
    const base = path.basename(full);
    const rel = path.relative(scriptsDir, full).replace(/\\/g, '/');
    return (
      base === f || rel === f || rel.endsWith(`/${f}`) || full.endsWith(`/${f}`)
    );
  });
}

/**
 * Check `script=` and `<MonoBehaviour file|script>` refs against scriptsDir.
 */
export function checkScripts(
  root: ParsedElement,
  scriptsDir: string | null
): AnalyzeIssue[] {
  const issues: AnalyzeIssue[] = [];
  const refs: { ref: string; tag: string }[] = [];

  const walk = (el: ParsedElement) => {
    const tag = el.tagName;
    const script = attrStr(el.attributes.script);
    if (script) refs.push({ ref: script, tag });
    if (tag.toLowerCase() === 'monobehaviour') {
      const file = attrStr(el.attributes.file) ?? attrStr(el.attributes.script);
      if (file) refs.push({ ref: file, tag });
    }
    for (const c of el.children) walk(c);
  };
  walk(root);

  if (refs.length === 0) return issues;

  if (!scriptsDir) {
    const seen = new Set<string>();
    for (const { ref, tag } of refs) {
      if (seen.has(ref)) continue;
      seen.add(ref);
      issues.push({
        severity: 'warn',
        code: 'script',
        message: `[analyze] WARN script "${ref}" on <${tag}> — scripts-dir not found (pass --scripts-dir)`,
      });
    }
    return issues;
  }

  const files = listScriptFiles(scriptsDir);
  const seen = new Set<string>();
  for (const { ref, tag } of refs) {
    if (seen.has(ref)) continue;
    seen.add(ref);
    if (scriptExists(scriptsDir, files, ref)) continue;
    issues.push({
      severity: 'error',
      code: 'script',
      message: `[analyze] ERROR missing script "${ref}" (<${tag}>) under ${scriptsDir}`,
    });
  }
  return issues;
}
