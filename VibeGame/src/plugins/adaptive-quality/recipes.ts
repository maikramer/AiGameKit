import type { Recipe } from '../../core';

/**
 * `<AdaptiveQuality>` — opts the scene into runtime quality auto-scaling.
 *
 * Measures rolling frame time and nudges a quality tier up/down with hysteresis
 * + cooldown, ONLY when the frame rate drops below `target-fps`. At each tier a
 * preset of rendering levers is applied (pixel ratio, SSAO half-res, point-
 * shadow cadence, water mirror, god-ray samples, DoF bokeh). The scene keeps
 * full visual fidelity while the GPU has headroom and degrades gracefully
 * under sustained load.
 *
 * Usage:
 *   <AdaptiveQuality target-fps="55" min-pixel-ratio="1.0"></AdaptiveQuality>
 *
 * Place anywhere under `<Scene>`. There should be at most one per scene.
 */
export const adaptiveQualityRecipe: Recipe = {
  name: 'AdaptiveQuality',
  components: ['transform', 'adaptive-quality'],
};
