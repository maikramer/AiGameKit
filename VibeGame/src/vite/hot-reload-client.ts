import { Cache } from 'three';

/**
 * Client-side handler for VibeGame asset hot-reload.
 * Import this in your app entry point during development.
 */
export function initAssetHotReload() {
  if (typeof import.meta === 'undefined' || !import.meta.hot) return;

  import.meta.hot.on(
    'vibegame:asset-update',
    (data: { path: string; ext: string }) => {
      console.log(`[VibeGame] Asset updated: ${data.path}`);

      if (['.png', '.jpg', '.jpeg', '.webp'].includes(data.ext)) {
        // Invalidate Three.js texture cache
        invalidateTexture(data.path);
      } else if (['.glb', '.gltf'].includes(data.ext)) {
        console.log(
          `[VibeGame] Model updated — reload recommended: ${data.path}`
        );
      }
    }
  );
}

function invalidateTexture(texturePath: string) {
  // TextureLoader caches by URL in THREE.Cache. Drop the entry (in both the
  // relative and the Vite-public root-prefixed form) so the next load
  // re-fetches the file. This used to be a no-op stub that only logged.
  Cache.remove(texturePath);
  Cache.remove(`/${texturePath.replace(/^\/+/, '')}`);
}
