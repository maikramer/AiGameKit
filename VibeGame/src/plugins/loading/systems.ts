import {
  defineSystem,
  registerReadyGate,
  setLoadingEnforcement,
  type System,
} from '../../core';
import { gltfAssetsReady } from '../gltf-xml/ready-gate';
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
export const LoadingScreenSystem: System = defineSystem({
  name: 'LoadingScreenSystem',
  group: 'draw',
  setup(state) {
    if (state.headless || typeof document === 'undefined') return;
    // Engage the physics hold and add the GLTF assets gate (critical loads
    // only — lod1/lod2 stream in the background). Domain gates (terrain,
    // spawn) are registered by their own plugins.
    setLoadingEnforcement(state, true);
    registerReadyGate(state, 'assets', () => gltfAssetsReady(state));
    mountLoadingScreen();
  },
  update(state) {
    if (state.headless || typeof document === 'undefined') return;
    updateLoadingScreen(state);
  },
});
