/**
 * Sunset Ridge — the circuit for simple-racer.
 *
 * ~5 km around the whole map, authored as 3D control nodes (the engine's
 * `TrackSpline` resamples them at a uniform arc length and derives banking from
 * curvature). Each sector is built on a piece of the real terrain, so the
 * heightmap is part of the layout rather than scenery behind it:
 *
 *  1. **Downtown straight** — flat plateau, towers either side, full throttle.
 *  2. **Turn 1** — long right onto the east plateau.
 *  3. **The rim** — the ground falls away into the basin; the track does not.
 *  4. **The flyover** — a viaduct across the basin, ~20 m over the forest. The
 *     carve leaves the valley alone, so trees grow under the deck and the
 *     engine puts pylons down to the ground.
 *  5. **West loop** — back on the plateau, long fast sweepers.
 *  6. **The climb** — into the south-east mountains, 14 m → 30 m.
 *  7. **Summit + descent** — hairpin at the top, then a plunge that dives
 *     **under** the flyover on the way home.
 *  8. **Return straight** — parallel to the start, back to the line.
 *
 * Everything downstream is derived from this list: `<RaceTrack>` geometry, the
 * `<Road>` bed carve, the prop dressing and the checkpoint arrows.
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
 * The track bed sits this far below the driving surface. The road flatten
 * carves the terrain corridor at `y - TRACK_ELEVATION`, and the wall base
 * (track-geometry `buildWalls`) drops the same amount so the suspended track
 * reads as a solid embankment instead of a floating ribbon with a hollow
 * underneath. Applied to every node height — the authored `y` values are the
 * driving surface, this constant is the bed offset.
 */
export const TRACK_ELEVATION = 1;

/**
 * How much wider than the racing surface the carved bed is (m).
 *
 * The `<RaceTrack shoulder>` strip and the barrier footing live outside the
 * white lines and still need flat ground under them: carve only the racing
 * width and the walls end up planted on a slope. This is the bed alone — the
 * gravel run-off beyond it is `flatten-shoulder` on `<Road>`, and the bank that
 * catches a car at the end of the run-off is `flatten-berm`.
 */
export const BED_MARGIN = 8;

/**
 * Deck-above-ground distance (m) that turns a stretch into a viaduct.
 *
 * One number, two consumers, and they must agree: `<Road
 * flatten-viaduct-clearance>` stops grading the terrain above it, and
 * `<RaceTrack viaduct-clearance>` starts building deck and pylons. Set them
 * apart and you get either a graded scar under a bridge or a span with nothing
 * holding it up.
 */
export const VIADUCT_CLEARANCE = 6;

/**
 * Control nodes.
 *
 * Layout rule that is easy to get wrong: two arms of the circuit must stay far
 * enough apart that their **road corridors** do not overlap — bed plus run-off
 * and berm, roughly 60 m here — even where their centerlines are nowhere near
 * each other. `TrackSpline.selfOverlaps()` checks this on every build and warns
 * with the exact arc positions.
 *
 * The one deliberate exception is the flyover: the descent from the mountain
 * crosses under the basin viaduct with ~13 m of air between the two decks.
 * That is safe because `TrackSpline.project` measures in 3D and every car
 * carries its own arc hint, so a car under the bridge is never snapped onto the
 * deck above it.
 */
export const TRACK_NODES: TrackNodeDef[] = [
  // ── 1. Downtown straight (flat plateau at y≈14, towers either side) ────
  //
  // Node 0 is the start/finish line and it is the seam of the closed spline:
  // its tangent comes from the node *before* it (the hairpin exit) and the one
  // after. Both must already point down the straight, or the lap joins itself
  // at an angle — which is how the first draft ran the return leg into the
  // grid head-on and drew the two corridors through each other.
  { x: -470, y: 14, z: -470, width: 22, section: 'city' },
  { x: -280, y: 14, z: -460, width: 22, section: 'city' },
  { x: -100, y: 14, z: -445, width: 20, section: 'city' },
  { x: 60, y: 14, z: -428, width: 19, section: 'city' },

  // ── 2. Turn 1: long right onto the east plateau ───────────────────────
  { x: 230, y: 14, z: -390, width: 18, section: 'turn1' },
  { x: 360, y: 14, z: -320, width: 17, section: 'turn1' },
  { x: 440, y: 15, z: -200, width: 17, section: 'turn1' },

  // ── 3. The rim: the last of the solid ground before the basin ─────────
  { x: 470, y: 17, z: -110, width: 17, section: 'rim' },

  // ── 4. The flyover: deck held level while the ground drops to ~1 m ────
  { x: 480, y: 20, z: -20, width: 16, section: 'flyover' },
  { x: 430, y: 21, z: 70, width: 16, section: 'flyover' },
  { x: 330, y: 21, z: 140, width: 16, section: 'flyover' },
  { x: 200, y: 21, z: 165, width: 16, section: 'flyover' },
  { x: 60, y: 21, z: 140, width: 16, section: 'flyover' },
  { x: -60, y: 20, z: 90, width: 17, section: 'flyover' },
  { x: -190, y: 18, z: 40, width: 17, section: 'landing' },

  // ── 5. West loop: back on the plateau, fast and open ──────────────────
  { x: -290, y: 15, z: 60, width: 18, section: 'west' },
  { x: -430, y: 14, z: 120, width: 18, section: 'west' },
  { x: -520, y: 14, z: 260, width: 18, section: 'west' },
  { x: -460, y: 14, z: 400, width: 18, section: 'west' },
  { x: -300, y: 14, z: 490, width: 18, section: 'west' },
  { x: -120, y: 15, z: 520, width: 18, section: 'west' },

  // ── 6. The climb: into the mountains, 14 m → 30 m ─────────────────────
  { x: 40, y: 24, z: 540, width: 17, section: 'climb' },
  { x: 180, y: 27, z: 570, width: 16, section: 'climb' },
  { x: 320, y: 29, z: 560, width: 16, section: 'climb' },

  // ── 7. Summit hairpin, then the plunge under the flyover ──────────────
  { x: 450, y: 30, z: 520, width: 16, section: 'crest' },
  { x: 540, y: 28, z: 430, width: 17, section: 'hairpin' },
  { x: 520, y: 22, z: 300, width: 17, section: 'descent' },
  { x: 470, y: 12, z: 190, width: 17, section: 'descent' },
  { x: 400, y: 8, z: 110, width: 17, section: 'underpass' },
  { x: 330, y: 6, z: 30, width: 17, section: 'underpass' },
  { x: 300, y: 10, z: -60, width: 18, section: 'climbout' },

  // ── 8. Return straight, parallel to the start line ────────────────────
  { x: 250, y: 14, z: -160, width: 18, section: 'return' },
  { x: 120, y: 14, z: -230, width: 19, section: 'return' },
  { x: -60, y: 14, z: -280, width: 20, section: 'return' },
  { x: -250, y: 14, z: -300, width: 21, section: 'return' },
  { x: -420, y: 14, z: -300, width: 22, section: 'return' },

  // ── 9. Stadium hairpin: the return leg turns 180° onto the grid ───────
  //
  // The two straights are 160 m apart, so this is a real corner with a ~70 m
  // radius rather than the track folding back on its own corridor. It exits
  // already pointing down the start straight, which is what keeps the seam at
  // node 0 smooth.
  { x: -560, y: 14, z: -330, width: 20, section: 'stadium' },
  { x: -640, y: 14, z: -390, width: 18, section: 'stadium' },
  { x: -600, y: 14, z: -450, width: 20, section: 'stadium' },
];

/** Flat `x y z x y z …` list for the `<RaceTrack centerline>` attribute. */
export function centerlineAttribute(): string {
  return TRACK_NODES.flatMap((n) => [n.x, n.y + TRACK_ELEVATION, n.z]).join(
    ' '
  );
}

/** Parallel per-node width list for `<RaceTrack widths>`. */
export function widthsAttribute(): string {
  return TRACK_NODES.map((n) => n.width).join(' ');
}

/** Parallel per-node section list for `<RaceTrack sections>`. */
export function sectionsAttribute(): string {
  return TRACK_NODES.map((n) => n.section).join(' ');
}
