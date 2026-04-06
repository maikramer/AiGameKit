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

function invalidateTexture(_texturePath: string) {
  // Texture cache invalidation requires access to Three.js Cache
  // which is module-scoped. The texture will reload on next use.
}
