import type { Plugin, ViteDevServer } from 'vite';
import { watch } from 'node:fs';
import path from 'node:path';

interface AssetHotReloadOptions {
  watchDirs?: string[];
  extensions?: string[];
  enabled?: boolean;
}

const DEFAULT_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.webp', '.glb', '.gltf'];

/**
 * Vite plugin that watches asset directories and sends HMR events
 * so the runtime can reload textures/materials without a full refresh.
 */
export function vibegameAssetHotReload(
  options?: AssetHotReloadOptions
): Plugin {
  const enabled = options?.enabled ?? true;
  const watchDirs = options?.watchDirs ?? ['public/assets'];
  const extensions = options?.extensions ?? DEFAULT_EXTENSIONS;

  return {
    name: 'vibegame-asset-hot-reload',
    enforce: 'pre',

    configureServer(server: ViteDevServer) {
      if (!enabled) return;

      for (const dir of watchDirs) {
        const resolved = path.resolve(server.config.root, dir);

        try {
          watch(resolved, { recursive: true }, (eventType, filename) => {
            if (!filename) return;
            const ext = path.extname(filename).toLowerCase();
            if (!extensions.includes(ext)) return;

            const assetPath = path.join(dir, filename).replace(/\\/g, '/');
            server.ws.send({
              type: 'custom',
              event: 'vibegame:asset-update',
              data: { path: assetPath, ext },
            });

            console.log(`[VibeGame] Asset ${eventType}: ${assetPath}`);
          });
        } catch {
          // Directory may not exist yet
        }
      }
    },

    handleHotUpdate({ file, server }) {
      if (!enabled) return;

      const ext = path.extname(file).toLowerCase();
      if (!extensions.includes(ext)) return;

      const root = server.config.root;
      const relative = path.relative(root, file).replace(/\\/g, '/');

      server.ws.send({
        type: 'custom',
        event: 'vibegame:asset-update',
        data: { path: relative, ext },
      });

      return []; // Don't trigger Vite's default HMR
    },
  };
}
