// gamedev-hooks — opencode project plugin
//
// Mirrors the Claude Code hook pair for this repo:
//   - tool.execute.before  -> guard: block edits to lockfiles / secret files
//   - tool.execute.after   -> format: route edited file to ruff / rustfmt / prettier
//
// opencode transpiles .ts plugins, so ESM import/export works regardless of
// the surrounding package.json "type". Formatters are invoked via
// node:child_process (no Bun shell in PluginInput).

import type { Plugin } from '@opencode-ai/plugin';
import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { basename } from 'node:path';

const FILE_TOOLS = new Set(['edit', 'write', 'patch']);

const BLOCKED = new Set([
  'uv.lock',
  'bun.lockb',
  'bun.lock',
  'package-lock.json',
  'Cargo.lock',
  'poetry.lock',
]);

function isBlocked(base: string): boolean {
  return BLOCKED.has(base) || base === '.env' || base.startsWith('.env.');
}

function run(cmd: string, args: string[]): void {
  try {
    execFileSync(cmd, args, { stdio: 'ignore' });
  } catch {
    // best-effort: never fail the edit because a formatter hiccupped
  }
}

export const GamedevHooks: Plugin = async ({ directory }) => {
  return {
    'tool.execute.before': async (input, output) => {
      if (!FILE_TOOLS.has(input.tool)) return;
      const fp: string | undefined = output.args?.filePath;
      if (!fp) return;
      const base = basename(fp);
      if (isBlocked(base)) {
        throw new Error(
          `BLOCKED: '${base}' is a lockfile or secret file. Edit the source ` +
            `manifest (pyproject.toml / package.json / Cargo.toml) and ` +
            `regenerate, or edit env files manually outside opencode.`
        );
      }
    },

    'tool.execute.after': async (input) => {
      if (!FILE_TOOLS.has(input.tool)) return;
      const fp: string | undefined = input.args?.filePath;
      if (!fp || !existsSync(fp)) return;

      if (fp.endsWith('.py')) {
        run('ruff', ['format', fp]);
        run('ruff', ['check', '--fix', fp]);
      } else if (fp.endsWith('.rs')) {
        run('rustfmt', [fp]);
      } else if (
        fp.includes('/VibeGame/') &&
        /\.(ts|tsx|js|mjs|json|css|md)$/.test(fp)
      ) {
        const prettier = `${directory}/VibeGame/node_modules/.bin/prettier`;
        if (existsSync(prettier)) run(prettier, ['--write', fp]);
      }
    },
  };
};
