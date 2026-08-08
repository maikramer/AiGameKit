/**
 * Sunset Ridge — the circuit for simple-racer.
 *
 * Authored as 3D control nodes (the engine's `TrackSpline` resamples them at a
 * uniform arc length and derives banking from curvature). The layout is built
 * around one idea per sector so a lap has rhythm rather than being a uniform
 * squiggle:
 *
 *  1. **Start straight** — wide, flat, full throttle; the only place to slipstream.
 *  2. **Turn 1** — long right-hander that rewards an early lift and a late apex.
 *  3. **The climb** — uphill sweep, grip loads up, then a crest that gets the car
 *     properly airborne at speed.
 *  4. **Descent** — downhill and fast; the car is light, so it slides.
 *  5. **The esses** — left/right/left, narrower, all about placement.
 *  6. **Hairpin** — the slowest corner on the lap and the best overtaking spot.
 *  7. **Infield + final sweep** — a long left that spits the car back onto the
 *     straight; carry speed here and the whole lap is worth it.
 */

export interface TrackNodeDef {
  x: number;
  y: number;
  z: number;
  /** Road width in metres at this node. */
  width: number;
  /** Theme tag, used by the prop dressing. */
  section: string;
}

/**
 * Control nodes.
 *
 * Layout rule that is easy to get wrong: two arms of the circuit must stay far
 * enough apart that their **road corridors** do not overlap — road width plus
 * both shoulders, roughly 25 m here — even where their centerlines are nowhere
 * near each other. `TrackSpline.selfOverlaps()` checks this on every build and
 * warns with the exact arc positions; the first draft of this layout ran the
 * hairpin 14 m from the main straight and drew two roads through each other.
 */
export const TRACK_NODES: TrackNodeDef[] = [
  // ── Start / finish straight (flat, wide) ──────────────────────────────
  { x: 0, y: 0, z: 0, width: 18, section: 'main' },
  { x: 120, y: 0, z: 0, width: 18, section: 'main' },
  { x: 250, y: 0, z: 0, width: 17, section: 'main' },
  { x: 350, y: 0, z: 8, width: 16, section: 'main' },

  // ── Turn 1: long right-hander ─────────────────────────────────────────
  { x: 440, y: 1, z: 40, width: 16, section: 'turn1' },
  { x: 490, y: 3, z: 100, width: 15, section: 'turn1' },

  // ── The climb, into the crest ─────────────────────────────────────────
  { x: 500, y: 6, z: 165, width: 15, section: 'climb' },
  { x: 475, y: 10, z: 222, width: 15, section: 'climb' },
  { x: 415, y: 14, z: 258, width: 15, section: 'climb' },
  { x: 340, y: 16, z: 272, width: 16, section: 'crest' },

  // ── Downhill ──────────────────────────────────────────────────────────
  { x: 265, y: 13, z: 262, width: 16, section: 'descent' },
  { x: 205, y: 9, z: 235, width: 15, section: 'descent' },
  { x: 170, y: 6, z: 192, width: 14, section: 'descent' },

  // ── The esses ─────────────────────────────────────────────────────────
  { x: 168, y: 4, z: 150, width: 14, section: 'esses' },
  { x: 200, y: 3, z: 118, width: 13, section: 'esses' },
  { x: 228, y: 2, z: 86, width: 13, section: 'esses' },

  // ── Hairpin (kept ≥ 40 m clear of the main straight) ──────────────────
  { x: 212, y: 1, z: 52, width: 14, section: 'hairpin' },
  { x: 155, y: 0, z: 44, width: 14, section: 'hairpin' },
  { x: 98, y: 0, z: 62, width: 15, section: 'hairpin' },

  // ── Infield + final sweep back onto the straight ──────────────────────
  { x: 52, y: 0, z: 96, width: 15, section: 'infield' },
  { x: -10, y: 0, z: 104, width: 16, section: 'infield' },
  { x: -70, y: 0, z: 88, width: 16, section: 'infield' },
  { x: -115, y: 0, z: 46, width: 16, section: 'final' },
  { x: -125, y: 0, z: -6, width: 17, section: 'final' },
  { x: -90, y: 0, z: -40, width: 18, section: 'final' },
  { x: -35, y: 0, z: -30, width: 18, section: 'final' },
];

/** Flat `x y z x y z …` list for the `<RaceTrack centerline>` attribute. */
export function centerlineAttribute(): string {
  return TRACK_NODES.flatMap((n) => [n.x, n.y, n.z]).join(' ');
}

/** Parallel per-node width list for `<RaceTrack widths>`. */
export function widthsAttribute(): string {
  return TRACK_NODES.map((n) => n.width).join(' ');
}

/** Parallel per-node section list for `<RaceTrack sections>`. */
export function sectionsAttribute(): string {
  return TRACK_NODES.map((n) => n.section).join(' ');
}
