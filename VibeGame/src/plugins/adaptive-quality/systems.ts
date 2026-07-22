import { defineSystem, defineQuery, type State, type System } from '../../core';
import { getRenderingContext } from '../rendering/utils';
import { AdaptiveQuality } from './components';
import {
  QualityTier,
  TIER_PRESETS,
  getAdaptiveQualityTier,
} from './quality-tiers';

const adaptiveQuery = defineQuery([AdaptiveQuality]);

/**
 * EMA smoothing factor for the rolling frame-time average. ~0.92 gives a
 * roughly 1-second window (the EMA "half-life" is ~8 frames at 60 fps), which
 * is responsive enough to catch sustained dips but immune to single hitches.
 */
const EMA_ALPHA = 0.08;

/**
 * Number of consecutive "hot" (over budget) or "cold" (under budget) frames
 * required before a tier transition triggers. With cooldown this prevents
 * flapping on oscillating load.
 */
const HOT_FRAMES_TO_DOWNSCALE = 45; // ~0.75s at 60fps — react faster under load
const COLD_FRAMES_TO_UPSCALE = 300; // ~5s — require sustained headroom to upgrade

/** Hysteresis: downscale when frame time exceeds target by this factor, upgrade
 *  when it drops below target by this factor (we want clear headroom). */
const DOWNSCALE_HYSTERESIS = 1.08;
const UPSCALE_HYSTERESIS = 0.72;

/** Cooldown (ms) between any two tier transitions to avoid rapid oscillation. */
const TRANSITION_COOLDOWN_MS = 3500;

/**
 * Measurement system: samples the real (unscaled) frame delta each frame and
 * maintains the EMA. Runs in `late` so it measures the frame that just
 * happened (systems + render have run by the time `late` ticks the NEXT frame,
 * but `state.time.unscaledDeltaTime` holds the delta of the previous step,
 * which is exactly what we want — the last completed frame's duration).
 */
export const AdaptiveQualityMeasureSystem: System = defineSystem({
  name: 'AdaptiveQualityMeasureSystem',
  group: 'late',
  update(state: State) {
    const entities = adaptiveQuery(state.world);
    if (entities.length === 0) return;
    const eid = entities[0];
    if (!AdaptiveQuality.enabled[eid]) return;

    // state.time.unscaledDeltaTime is the real wall-clock delta of the last
    // frame (seconds), set by the scheduler BEFORE systems run.
    const sampleMs = state.time.unscaledDeltaTime * 1000;
    // Discard absurd samples (tab switch, breakpoint) — they'd poison the EMA.
    if (sampleMs <= 0 || sampleMs > 1000) return;

    const prev = AdaptiveQuality.emaFrameMs[eid];
    const ema =
      prev <= 0 ? sampleMs : prev * (1 - EMA_ALPHA) + sampleMs * EMA_ALPHA;
    AdaptiveQuality.emaFrameMs[eid] = ema;
  },
});

/**
 * Apply system: decides whether to change the tier based on the EMA, applies
 * the tier's pixel-ratio lever to the renderer immediately. Effect-level
 * levers (SSAO halfRes, god-ray samples, water mirror) are read by the
 * respective systems via `getAdaptiveQualityTier(state)`, so they pick up the
 * new tier automatically on their next update — no direct coupling here.
 *
 * Runs in `draw`, `after` the camera/light sync but `before` the final render
 * (which happens in the runtime loop after `state.step`).
 */
export const AdaptiveQualityApplySystem: System = defineSystem({
  name: 'AdaptiveQualityApplySystem',
  group: 'draw',
  update(state: State) {
    const entities = adaptiveQuery(state.world);
    if (entities.length === 0) return;
    const eid = entities[0];
    if (!AdaptiveQuality.enabled[eid]) return;

    const targetMs = 1000 / AdaptiveQuality.targetFps[eid];
    const ema = AdaptiveQuality.emaFrameMs[eid];
    // Need a few frames of warmup before the EMA is meaningful.
    if (ema <= 0) return;

    const hot = ema > targetMs * DOWNSCALE_HYSTERESIS;
    const cold = ema < targetMs * UPSCALE_HYSTERESIS;

    const hotFrames = AdaptiveQuality.consecutiveHotFrames[eid];
    const coldFrames = AdaptiveQuality.consecutiveColdFrames[eid];
    AdaptiveQuality.consecutiveHotFrames[eid] = hot ? hotFrames + 1 : 0;
    AdaptiveQuality.consecutiveColdFrames[eid] = cold ? coldFrames + 1 : 0;

    const currentTier = AdaptiveQuality.currentTier[eid];
    const now = performance.now();
    const sinceTransition = now - AdaptiveQuality.lastTransitionMs[eid];

    let newTier = currentTier;
    // DOWNSCALE: sustained hot frames → bump up one tier (towards Low=3).
    if (
      AdaptiveQuality.consecutiveHotFrames[eid] >= HOT_FRAMES_TO_DOWNSCALE &&
      currentTier < QualityTier.Low &&
      sinceTransition >= TRANSITION_COOLDOWN_MS
    ) {
      newTier = currentTier + 1;
    }
    // UPSCALE: sustained cold frames + headroom → bump down one tier (towards Max=0).
    // Upscale requires MORE sustained evidence + a longer cooldown — we never
    // want to ping-pong across a tier boundary under oscillating load.
    else if (
      AdaptiveQuality.consecutiveColdFrames[eid] >= COLD_FRAMES_TO_UPSCALE &&
      currentTier > QualityTier.Max &&
      sinceTransition >= TRANSITION_COOLDOWN_MS * 1.5
    ) {
      newTier = currentTier - 1;
    }

    if (newTier !== currentTier) {
      AdaptiveQuality.currentTier[eid] = newTier;
      AdaptiveQuality.lastTransitionMs[eid] = now;
      AdaptiveQuality.transitionCount[eid] += 1;
      // Reset streaks so the new tier gets a fair observation window.
      AdaptiveQuality.consecutiveHotFrames[eid] = 0;
      AdaptiveQuality.consecutiveColdFrames[eid] = 0;
    }

    // Apply the pixel-ratio lever every frame (cheap; idempotent if unchanged).
    // The renderer's existing cap (1.5 desktop / 1.25 mobile) is the ceiling;
    // each tier scales down from it. The resize handler also consults the tier
    // so it doesn't clobber this.
    applyPixelRatioLever(state, eid, newTier);
  },
});

/** Module-level helper: apply the tier's pixel-ratio scale to the renderer,
 *  clamped to the user-configured [minPixelRatio, maxPixelRatio]. Exported via
 *  the quality-tiers module so the resize handler reuses the same math. */
function applyPixelRatioLever(state: State, eid: number, tier: number): void {
  const renderer = getRenderer(state);
  if (!renderer) return;
  const preset = TIER_PRESETS[tier] ?? TIER_PRESETS[0];
  const cap = AdaptiveQuality.maxPixelRatio[eid] || defaultPixelRatioCap();
  const floor = AdaptiveQuality.minPixelRatio[eid] || 0.5;
  const desired = Math.max(floor, Math.min(cap, cap * preset.pixelRatioScale));
  // Avoid spamming setPixelRatio (it triggers a buffer realloc) when unchanged.
  if (Math.abs(renderer.getPixelRatio() - desired) > 0.001) {
    renderer.setPixelRatio(desired);
    // setSize must be re-applied for the new ratio to take effect on the
    // drawing buffer; reuse the current canvas size.
    const canvas = renderer.domElement;
    const w = canvas.clientWidth || window.innerWidth;
    const h = canvas.clientHeight || window.innerHeight;
    renderer.setSize(w, h, false);
  }
}

function defaultPixelRatioCap(): number {
  return Math.min(
    window.devicePixelRatio,
    /Mobi|Android/i.test(navigator.userAgent) ? 1.25 : 1.5
  );
}

function getRenderer(state: State): import('three').WebGLRenderer | null {
  return getRenderingContext(state).renderer ?? null;
}

export { getAdaptiveQualityTier };
