import {
  defineComponent,
  F32,
  F64,
  U32,
  U8,
} from '../../core/ecs/component-storage';

/**
 * Adaptive Quality — runtime auto-scaler.
 *
 * Measures rolling frame time (EMA) and nudges a quality `tier` (0=Max …
 * 3=Low) up/down with hysteresis + cooldown, ONLY when the frame rate drops
 * below `targetFps`. At each tier a preset of rendering levers is applied
 * (pixel ratio, effect resolutions, shadow throttle, …). The goal is to keep
 * the full visual fidelity when the GPU has headroom and degrade gracefully
 * under load — never degrade proactively.
 *
 * The tier is read by other systems (rendering, postprocessing, water) via
 * `getAdaptiveQualityTier(state)`. Each lever's enablement per tier lives in
 * `quality-tiers.ts` so the policy is centralized and auditable.
 */
export const AdaptiveQuality = defineComponent({
  /** 0 = disabled (no measurement, tier stays at 0 = Max). */
  enabled: U8,
  /** Target frame rate; the scaler engages when sustained frame time exceeds
   *  `1000 / targetFps * downscaleHysteresis`. */
  targetFps: F32,
  /** Floor for pixel-ratio downscaling (desktop default 1.0). */
  minPixelRatio: F32,
  /** Ceiling for pixel ratio (matches the renderer's existing cap, 1.5). */
  maxPixelRatio: F32,
  /** Current applied tier. 0=Max, 1=High, 2=Medium, 3=Low. */
  currentTier: U8,
  /** EMA of the frame time in milliseconds. */
  emaFrameMs: F32,
  /** Timestamp (ms, performance.now) of the last tier transition. */
  lastTransitionMs: F64,
  /** Frames continuously over/under threshold since last check — used to avoid
   *  reacting to a single slow frame. */
  consecutiveHotFrames: U32,
  consecutiveColdFrames: U32,
  /** Total frame transitions (instrumentation). */
  transitionCount: U32,
});
