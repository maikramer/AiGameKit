import type { Plugin, ViteDevServer } from 'vite';

export interface WorldHmrOptions {
  /** File extensions treated as world XML (default: ['.xml']). */
  extensions?: string[];
  enabled?: boolean;
  /** Inject the auto-connecting client into index.html (default true). */
  injectClient?: boolean;
}

const DEFAULT_EXTENSIONS = ['.xml'];
const WS_EVENT = 'vibegame:world';
const VIRTUAL_ID = 'virtual:vibegame-world-hmr';
const RESOLVED_VIRTUAL_ID = '\0' + VIRTUAL_ID;
const VIRTUAL_URL = '/@id/__x00__virtual:vibegame-world-hmr';

const lastSentAt = new Map<string, number>();

function relativeTo(root: string, file: string): string {
  return file.replace(root + '/', '').replace(/\\/g, '/');
}

function isIgnored(relativePath: string): boolean {
  return (
    relativePath.startsWith('node_modules/') ||
    relativePath.startsWith('dist/') ||
    relativePath.includes('/node_modules/')
  );
}

function sendEvent(server: ViteDevServer, file: string): boolean {
  const relative = relativeTo(server.config.root, file);
  if (isIgnored(relative)) return false;
  // The file watcher and the hotUpdate/handleHotUpdate hooks can both report
  // the same save — collapse duplicates so the world swaps once.
  const now = Date.now();
  const last = lastSentAt.get(relative) ?? 0;
  if (now - last < 100) return false;
  lastSentAt.set(relative, now);
  try {
    server.ws.send({
      type: 'custom',
      event: WS_EVENT,
      data: { file: relative },
    });
  } catch {
    // Dev server torn down (file event after close) — ignore.
  }
  console.log(`[VibeGame] World XML changed: ${relative} — hot-swapping`);
  return true;
}

/**
 * World XML hot-reload: saving a `.xml` world file pushes a `vibegame:world`
 * HMR event; the injected client tells the runtime to hot-swap the `<scene>`
 * content (Scene.swap) instead of a full page reload. The player, cameras and
 * runtime state survive the swap.
 */
export function vibegameWorldHmr(options?: WorldHmrOptions): Plugin {
  const enabled = options?.enabled ?? true;
  const extensions = options?.extensions ?? DEFAULT_EXTENSIONS;
  const injectClient = options?.injectClient ?? true;

  const isWorldFile = (file: string): boolean => {
    const ext = '.' + file.split('.').pop()?.toLowerCase();
    return extensions.includes(ext);
  };

  return {
    name: 'vibegame-world-hmr',
    enforce: 'pre',

    resolveId(id) {
      if (id === VIRTUAL_ID) return RESOLVED_VIRTUAL_ID;
      return null;
    },

    load(id) {
      if (id !== RESOLVED_VIRTUAL_ID) return null;
      return `import { initWorldHotReload } from 'aigamekit-vibegame'; initWorldHotReload();`;
    },

    transformIndexHtml() {
      if (!enabled || !injectClient) return [];
      return [
        {
          tag: 'script',
          attrs: { type: 'module', src: VIRTUAL_URL },
          injectTo: 'head',
        },
      ];
    },

    // Standalone world files are fetched at runtime (not in the module graph),
    // so they only surface through the file watcher.
    configureServer(server: ViteDevServer) {
      if (!enabled) return;
      server.watcher.on('change', (file) => {
        if (isWorldFile(file)) sendEvent(server, file);
      });
    },

    // Vite 8+ hook name (handleHotUpdate is skipped for the client environment).
    hotUpdate({ file, server }) {
      if (!enabled || !isWorldFile(file)) return undefined;
      sendEvent(server, file);
      return []; // World swap instead of Vite's default HMR/full reload.
    },

    // Vite 6/7: legacy hook name.
    handleHotUpdate({ file, server }) {
      if (!enabled || !isWorldFile(file)) return undefined;
      sendEvent(server, file);
      return [];
    },
  };
}
