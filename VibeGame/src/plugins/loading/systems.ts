import {
  registerReadyGate,
  setLoadingEnforcement,
  type System,
} from '../../core';
import { getActiveGltfLoadCount } from '../../extras/gltf-bridge';
import { mountLoadingScreen, updateLoadingScreen } from './context';

/**
 * Drives the loading screen: shows a full-screen overlay, updates a progress
 * bar from the registered ready gates, and fades out once the world is fully
 * loaded. While it is up, physics is held (see `isPhysicsHeld`), so nothing
 * simulates before terrain colliders and assets are in place.
 *
 * For the earliest possible paint, call `mountLoadingScreen()` yourself at the
 * very start of bootstrap (before building the runtime). This system also
 * mounts it on first run as a fallback.
 */
export const LoadingScreenSystem: System = {
  group: 'draw',
  setup(state) {
    if (state.headless || typeof document === 'undefined') return;
    // Engage the physics hold and add the generic GLTF-assets gate. Domain
    // gates (terrain, spawn) are registered by their own plugins.
    setLoadingEnforcement(state, true);
    registerReadyGate(state, 'assets', () => getActiveGltfLoadCount() === 0);
    mountLoadingScreen();
  },
  update(state) {
    if (state.headless || typeof document === 'undefined') return;
    updateLoadingScreen(state);
  },
};
