import path from 'node:path';
import type { Plugin, ViteDevServer } from 'vite';

const CODE_EXT = /\.(tsx?|jsx?|mjs|cjs)$/i;

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
      // Example project root (e.g. simple-rpg/src/main.ts)
      if (rel.startsWith('src/')) return true;
    }
  }
  return false;
}

export function vibegameForceFullReload(): Plugin {
  return {
    name: 'vibegame-force-full-reload',
    apply: 'serve',
    enforce: 'post',
    handleHotUpdate({ file, server }: { file: string; server: ViteDevServer }) {
      if (!shouldForceFullReload(file, server.config.root)) return;
      server.ws.send({ type: 'full-reload', path: '*' });
      return [];
    },
  };
}
