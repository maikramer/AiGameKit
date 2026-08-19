import { defineQueryLive, type State } from '../../core';
import { Postprocessing } from './components';

const postprocessingQuery = defineQueryLive([Postprocessing]);

/**
 * True when a postprocessing pipeline is declared and enabled but the composer
 * has not been built yet (the world XML is still being parsed, or the camera
 * has not appeared).
 *
 * Callers use this to avoid rendering the scene straight to the canvas in the
 * meantime. Three keys shader programs by the **output** colour space, taken
 * from the bound render target: `srgb` for the canvas, `srgb-linear` for the
 * composer's half-float buffer. Any frame drawn to the canvas before the
 * composer exists therefore compiles a second copy of every visible material,
 * which is discarded the moment the composer takes over — pure boot-time cost,
 * and it is exactly what the shader warmup is supposed to prevent.
 *
 * Leaf module on purpose: `rendering/` reads this without importing the
 * postprocessing systems (which import `rendering/` back).
 */
export function isPostprocessingPending(state: State): boolean {
  for (const eid of postprocessingQuery(state.world)) {
    if (Postprocessing.enabled[eid] === 1) return true;
  }
  return false;
}
