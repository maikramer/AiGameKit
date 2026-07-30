import type { State } from '../../core';
import { Transform } from '../transforms';

/**
 * World-space marker registry shared by every "where do I go?" HUD surface —
 * minimap blips, compass pips and the screen-space waypoint arrow.
 *
 * It lives in `hud` rather than in `quests` on purpose: `quests` already
 * imports `hud` (screen-layer, string interning), so the reverse edge would be
 * a cycle. Keeping the registry neutral also lets non-quest content (a shop, a
 * boss arena, a player-placed pin) light up the same HUD without the compass
 * having to know what a quest is.
 */

export type WaypointKind =
  | 'quest-available'
  | 'quest-active'
  | 'quest-turnin'
  | 'objective'
  | 'poi'
  | 'custom';

export interface WaypointStyle {
  /** Marker tint (CSS color). */
  readonly color: string;
  /** Single glyph drawn inside the blip / pip. */
  readonly glyph: string;
  /** Higher wins when auto-selecting what the arrow should point at. */
  readonly priority: number;
}

export const WAYPOINT_STYLES: Record<WaypointKind, WaypointStyle> = {
  'quest-available': { color: '#ffd24a', glyph: '!', priority: 20 },
  'quest-active': { color: '#8fb7ff', glyph: '?', priority: 10 },
  'quest-turnin': { color: '#7fe0a0', glyph: '?', priority: 40 },
  objective: { color: '#ff9a4a', glyph: '◆', priority: 30 },
  poi: { color: '#c9d6ef', glyph: '◇', priority: 5 },
  custom: { color: '#e8eef7', glyph: '•', priority: 1 },
};

export interface Waypoint {
  /** Stable id — re-registering the same id updates in place. */
  readonly id: string;
  x: number;
  y: number;
  z: number;
  kind: WaypointKind;
  /** Short human label (tooltip / arrow caption). */
  label?: string;
  /** Overrides {@link WAYPOINT_STYLES} colour for this marker. */
  color?: string;
  /** Overrides {@link WAYPOINT_STYLES} glyph for this marker. */
  glyph?: string;
  /**
   * Entity the waypoint follows. When set, {@link refreshWaypointPositions}
   * re-reads the entity Transform each frame, so a marker can sit on a moving
   * NPC, and the waypoint is dropped once the entity is gone.
   */
  eid?: number;
  /** Quest index this marker belongs to, or -1. Used for per-quest tracking. */
  questIndex?: number;
}

const stateToWaypoints = new WeakMap<State, Map<string, Waypoint>>();
const stateToTracked = new WeakMap<State, string | null>();

function waypointMap(state: State): Map<string, Waypoint> {
  let m = stateToWaypoints.get(state);
  if (!m) {
    m = new Map();
    stateToWaypoints.set(state, m);
  }
  return m;
}

/** Insert or update a marker. Returns the stored record (mutable in place). */
export function setWaypoint(state: State, waypoint: Waypoint): Waypoint {
  const map = waypointMap(state);
  const existing = map.get(waypoint.id);
  if (existing) {
    existing.x = waypoint.x;
    existing.y = waypoint.y;
    existing.z = waypoint.z;
    existing.kind = waypoint.kind;
    existing.label = waypoint.label;
    existing.color = waypoint.color;
    existing.glyph = waypoint.glyph;
    existing.eid = waypoint.eid;
    existing.questIndex = waypoint.questIndex;
    return existing;
  }
  const record: Waypoint = { ...waypoint };
  map.set(record.id, record);
  return record;
}

export function removeWaypoint(state: State, id: string): void {
  waypointMap(state).delete(id);
  if (stateToTracked.get(state) === id) stateToTracked.set(state, null);
}

/** Drop every marker, or only those whose id starts with `prefix`. */
export function clearWaypoints(state: State, prefix?: string): void {
  const map = waypointMap(state);
  if (prefix === undefined) {
    map.clear();
    stateToTracked.set(state, null);
    return;
  }
  for (const id of [...map.keys()]) {
    if (id.startsWith(prefix)) removeWaypoint(state, id);
  }
}

export function getWaypoints(state: State): ReadonlyMap<string, Waypoint> {
  return waypointMap(state);
}

export function getWaypoint(state: State, id: string): Waypoint | null {
  return waypointMap(state).get(id) ?? null;
}

/**
 * Re-read entity-anchored marker positions and drop markers whose entity is
 * gone. Called once per frame by the HUD before any widget reads the registry,
 * so every surface sees the same positions in the same frame.
 */
export function refreshWaypointPositions(state: State): void {
  const map = stateToWaypoints.get(state);
  if (!map || map.size === 0) return;
  for (const wp of [...map.values()]) {
    if (wp.eid === undefined) continue;
    if (!state.exists(wp.eid)) {
      removeWaypoint(state, wp.id);
      continue;
    }
    wp.x = Transform.posX[wp.eid];
    wp.y = Transform.posY[wp.eid];
    wp.z = Transform.posZ[wp.eid];
  }
}

/**
 * Pin the arrow to one marker. Pass `null` to fall back to automatic
 * selection (see {@link getTrackedWaypoint}).
 */
export function setTrackedWaypointId(state: State, id: string | null): void {
  stateToTracked.set(state, id);
}

export function getTrackedWaypointId(state: State): string | null {
  return stateToTracked.get(state) ?? null;
}

/**
 * The marker the arrow should point at: the pinned one when it still exists,
 * otherwise the highest-priority marker, ties broken by distance to
 * (`fromX`,`fromZ`). Returns `null` when there is nothing to point at.
 */
export function getTrackedWaypoint(
  state: State,
  fromX = 0,
  fromZ = 0
): Waypoint | null {
  const map = stateToWaypoints.get(state);
  if (!map || map.size === 0) return null;

  const pinnedId = stateToTracked.get(state);
  if (pinnedId) {
    const pinned = map.get(pinnedId);
    if (pinned) return pinned;
  }

  let best: Waypoint | null = null;
  let bestPriority = -Infinity;
  let bestDist = Infinity;
  for (const wp of map.values()) {
    const priority = WAYPOINT_STYLES[wp.kind]?.priority ?? 0;
    const dx = wp.x - fromX;
    const dz = wp.z - fromZ;
    const dist = dx * dx + dz * dz;
    if (
      priority > bestPriority ||
      (priority === bestPriority && dist < bestDist)
    ) {
      best = wp;
      bestPriority = priority;
      bestDist = dist;
    }
  }
  return best;
}

export function waypointColor(wp: Waypoint): string {
  return (
    wp.color ?? WAYPOINT_STYLES[wp.kind]?.color ?? WAYPOINT_STYLES.custom.color
  );
}

export function waypointGlyph(wp: Waypoint): string {
  return (
    wp.glyph ?? WAYPOINT_STYLES[wp.kind]?.glyph ?? WAYPOINT_STYLES.custom.glyph
  );
}

/** Horizontal distance in metres from a point to a marker. */
export function waypointDistance(wp: Waypoint, x: number, z: number): number {
  return Math.hypot(wp.x - x, wp.z - z);
}

/** `120 m` / `12 m` — compact distance caption for pips and the arrow. */
export function formatWaypointDistance(metres: number): string {
  if (!Number.isFinite(metres)) return '';
  if (metres >= 1000) return `${(metres / 1000).toFixed(1)} km`;
  return `${Math.round(metres)} m`;
}
