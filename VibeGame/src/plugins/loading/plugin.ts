import type { Plugin } from '../../core';
import { LoadingScreenSystem } from './systems';

/**
 * Honest loading gate: shows a full-screen loading overlay and holds physics
 * until the world is fully ready (terrain decoded + collision, spawns done,
 * GLTF assets loaded). Opt-in — add it to a game to enable the behavior.
 */
export const LoadingPlugin: Plugin = {
  systems: [LoadingScreenSystem],
};
