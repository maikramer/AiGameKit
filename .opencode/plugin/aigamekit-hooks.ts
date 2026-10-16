// aigamekit-hooks — opencode project plugin
//
// Mirrors the guard/format pair for this repo (the zcode/Claude-style
// counterpart lives in plugins/aigamekit-gen — zcode never loads this file):
//   - tool execute.before -> guard: block edits to lockfiles / secret files
//   - tool execute.after  -> format: route edited file to ruff / rustfmt / prettier
//
// Compatibility — one file, three consumers:
//   - OpenCode V2 (server runtime): reads the DEFAULT export and requires
//     { id, setup | effect } (schema-validated; without it the plugin fails
//     with "Plugin must export a default definition with an id and an effect
//     or setup function"). Hooks register inside setup() via ctx.tool.hook().
//   - OpenCode V1 object form (>= 1.18.29): default export also carries
//     server() returning the classic hook map.
//   - Older V1 loaders that bind named function exports: named GamedevHooks
//     export kept unchanged.
//
// opencode transpiles .ts plugins, so ESM import/export works regardless of
// the surrounding package.json "type". The @opencode-ai/plugin import is
// type-only (erased at transpile) — no runtime dependency beyond node
// builtins. Formatters are invoked via node:child_process (no Bun shell in
// PluginInput).

import type { Plugin } from "@opencode-ai/plugin";
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { basename } from "node:path";

const FILE_TOOLS = new Set(["edit", "write", "patch"]);

const BLOCKED = new Set([
  "uv.lock",
  "bun.lockb",
  "bun.lock",
  "package-lock.json",
  "Cargo.lock",
  "poetry.lock",
]);

function isBlocked(base: string): boolean {
  return BLOCKED.has(base) || base === ".env" || base.startsWith(".env.");
}

function run(cmd: string, args: string[]): void {
  try {
    execFileSync(cmd, args, { stdio: "ignore" });
  } catch {
    // best-effort: never fail the edit because a formatter hiccupped
  }
}

interface ToolArgs {
  filePath?: string;
}

function blockIfLocked(tool: string, args: ToolArgs | undefined): void {
  if (!FILE_TOOLS.has(tool)) return;
  const fp = args?.filePath;
  if (!fp) return;
  const base = basename(fp);
  if (isBlocked(base)) {
    throw new Error(
      `BLOCKED: '${base}' is a lockfile or secret file. Edit the source ` +
        `manifest (pyproject.toml / package.json / Cargo.toml) and ` +
        `regenerate, or edit env files manually outside opencode.`,
    );
  }
}

function formatEditedFile(tool: string, directory: string, args: ToolArgs | undefined): void {
  if (!FILE_TOOLS.has(tool)) return;
  const fp = args?.filePath;
  if (!fp || !existsSync(fp)) return;

  if (fp.endsWith(".py")) {
    run("ruff", ["format", fp]);
    run("ruff", ["check", "--fix", fp]);
  } else if (fp.endsWith(".rs")) {
    run("rustfmt", [fp]);
  } else if (fp.includes("/VibeGame/") && /\.(ts|tsx|js|mjs|json|css|md)$/.test(fp)) {
    const prettier = `${directory}/VibeGame/node_modules/.bin/prettier`;
    if (existsSync(prettier)) run(prettier, ["--write", fp]);
  }
}

// ---------------------------------------------------------------------------
// V1 named export (legacy loaders): unchanged contract.
// ---------------------------------------------------------------------------
export const GamedevHooks: Plugin = async ({ directory }) => {
  return {
    "tool.execute.before": async (input, output) => {
      blockIfLocked(input.tool, output.args as ToolArgs);
    },

    "tool.execute.after": async (input) => {
      formatEditedFile(input.tool, directory, (input as { args?: ToolArgs }).args);
    },
  };
};

// V1 hook-map builder shared by the default export's server() form.
function v1HookMap(directory: string) {
  return {
    "tool.execute.before": async (input: { tool: string }, output: { args?: ToolArgs }) => {
      blockIfLocked(input.tool, output.args);
    },
    "tool.execute.after": async (input: { tool: string; args?: ToolArgs }) => {
      formatEditedFile(input.tool, directory, input.args);
    },
  };
}

// ---------------------------------------------------------------------------
// Default export — the one the current OpenCode V2 loader validates:
// { id, setup }. server() keeps the V1 object-form contract on the same
// entrypoint (official V1+V2 dual-support shape).
// ---------------------------------------------------------------------------

interface V2ToolHookEvent {
  tool: string;
  input?: ToolArgs;
}

interface V2PluginContext {
  location?: { directory?: string };
  tool: {
    hook(
      name: "execute.before" | "execute.after",
      callback: (event: V2ToolHookEvent) => void | Promise<void>,
    ): Promise<unknown>;
  };
}

export default {
  id: "aigamekit-hooks",

  async setup(ctx: V2PluginContext) {
    const directory = ctx.location?.directory ?? process.cwd();
    await ctx.tool.hook("execute.before", (event) => {
      blockIfLocked(event.tool, event.input);
    });
    await ctx.tool.hook("execute.after", (event) => {
      formatEditedFile(event.tool, directory, event.input);
    });
  },

  // V1 dual-support object form: V1 loaders call server() and use the
  // returned hook map; V2 ignores it.
  async server(opts: { directory?: string } = {}) {
    return v1HookMap(opts.directory ?? process.cwd());
  },
};
