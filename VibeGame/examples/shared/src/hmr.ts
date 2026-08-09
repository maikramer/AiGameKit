/**
 * Vite HMR guard shared by the examples: soft HMR of the game graph leaks
 * WebGL/KTX2/Rapier (Firefox), so we decline (forcing full page reloads) and
 * release GPU resources on dispose. The unload path stays lightweight so
 * `location.reload()` is never blocked mid-boot.
 */
export function setupHmrGuard(cleanup: () => void): void {
  if (!import.meta.hot) return;
  // decline() exists in runtime Vite; older client typings omit it.
  (import.meta.hot as unknown as { decline(): void }).decline();
  import.meta.hot.dispose(() => {
    try {
      cleanup();
    } catch (e) {
      console.error('[VibeGame] HMR dispose failed:', e);
    }
  });
}
