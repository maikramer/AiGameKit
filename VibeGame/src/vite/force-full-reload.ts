import path from 'node:path';
import type { HotUpdateOptions, Plugin, ViteDevServer } from 'vite';

const CODE_EXT = /\.(tsx?|jsx?|mjs|cjs)$/i;

/** Coalesce bursty saves / multi-file edits into one reload. */
const FULL_RELOAD_DEBOUNCE_MS = 200;

/**
 * Soft HMR of engine / example TS leaves orphan WebGL contexts, KTX2 workers,
 * and Rapier/recast WASM — Firefox often locks up until the process is killed.
 * Force a full page reload for code changes; asset HMR stays on the other plugin.
 */
export function shouldForceFullReload(file: string, root?: string): boolean {
  const norm = file.replace(/\\/g, '/');
  if (!CODE_EXT.test(norm)) return false;
  if (norm.includes('/VibeGame/src/')) return true;
  if (norm.includes('/examples/') && norm.includes('/src/')) return true;
  if (root) {
    const rel = path.relative(root, file).replace(/\\/g, '/');
    if (!rel.startsWith('..') && CODE_EXT.test(rel)) {
      if (rel.startsWith('src/')) return true;
    }
  }
  return false;
}

/**
 * Vite 8 renamed `handleHotUpdate` → `hotUpdate`; the legacy hook is skipped for
 * the client environment (only consulted for the mixed graph, with a deprecation
 * warning). Without the new hook, every saved engine/example TS file triggers
 * Vite's default per-file full reload — no coalescing — so a multi-file edit
 * reloads the page once per file and aborts the game mid-boot. Both hook names
 * are implemented so the plugin works on Vite 6/7 and 8+.
 */
export function vibegameForceFullReload(): Plugin {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let pendingServer: ViteDevServer | undefined;

  const flush = () => {
    timer = undefined;
    const server = pendingServer;
    pendingServer = undefined;
    if (!server) return;
    server.ws.send({ type: 'full-reload', path: '*' });
  };

  const scheduleReload = (server: ViteDevServer) => {
    pendingServer = server;
    if (timer !== undefined) clearTimeout(timer);
    timer = setTimeout(flush, FULL_RELOAD_DEBOUNCE_MS);
  };

  return {
    name: 'vibegame-force-full-reload',
    apply: 'serve',
    enforce: 'post',
    // Vite 8+: new hook name; options carry the dev server.
    hotUpdate(options: HotUpdateOptions) {
      if (!shouldForceFullReload(options.file, options.server.config.root)) {
        return;
      }
      scheduleReload(options.server);
      // Suppress soft HMR for this file; reload is scheduled above.
      return [];
    },
    // Vite 6/7: legacy hook name.
    handleHotUpdate({ file, server }: { file: string; server: ViteDevServer }) {
      if (!shouldForceFullReload(file, server.config.root)) return;
      scheduleReload(server);
      // Suppress soft HMR for this file; reload is scheduled above.
      return [];
    },
  };
}
