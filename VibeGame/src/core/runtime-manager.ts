import { logger } from './utils/logger';
import type { GameRuntime } from '../runtime';

const activeRuntimes: GameRuntime[] = [];

let pageTeardownInstalled = false;
let pageHideHandler: ((event: PageTransitionEvent) => void) | null = null;

/** Test-only: drop pagehide hook so suites can re-install cleanly. */
export function resetRuntimePageTeardownForTests(): void {
  if (pageHideHandler && typeof window !== 'undefined') {
    window.removeEventListener('pagehide', pageHideHandler);
  }
  pageHideHandler = null;
  pageTeardownInstalled = false;
}

/**
 * Fast GPU release for document unload / full-reload.
 *
 * Must NOT call full `destroy()` here: Vite runs `vite:beforeFullReload` /
 * `hot.dispose` synchronously before `location.reload()`. Heavy Rapier /
 * navmesh teardown mid-boot can hang and **block the reload**, leaving a
 * dead page (dispose logged, no new boot).
 */
export function releaseRuntimeGpuResources(): void {
  for (const runtime of activeRuntimes) {
    try {
      runtime.releaseGpuContext();
    } catch (error) {
      logger.warn('[VibeGame] releaseGpuContext failed:', error);
    }
  }
}

/**
 * Drop WebGL contexts before the document goes away so Firefox does not keep
 * orphan GPU contexts until the browser process is killed.
 */
export function ensureRuntimePageTeardown(): void {
  if (pageTeardownInstalled || typeof window === 'undefined') return;
  pageTeardownInstalled = true;

  pageHideHandler = (event: PageTransitionEvent) => {
    if (event.persisted) return;
    releaseRuntimeGpuResources();
  };
  window.addEventListener('pagehide', pageHideHandler);
}

export function registerRuntime(runtime: GameRuntime): void {
  ensureRuntimePageTeardown();
  activeRuntimes.push(runtime);
}

export function unregisterRuntime(runtime: GameRuntime): void {
  const index = activeRuntimes.indexOf(runtime);
  if (index !== -1) {
    activeRuntimes.splice(index, 1);
  }
}

export function disposeAllRuntimes(): void {
  if (activeRuntimes.length > 0) {
    logger.warn(
      `[VibeGame] Disposing ${activeRuntimes.length} active runtime(s)`
    );
    // Copy — destroy() unregisters itself.
    for (const runtime of [...activeRuntimes]) {
      try {
        runtime.destroy();
      } catch (error) {
        logger.error('[VibeGame] Failed to dispose runtime:', error);
      }
    }
    activeRuntimes.length = 0;
  }
}
