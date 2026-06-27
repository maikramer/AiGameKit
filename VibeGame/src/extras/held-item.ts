import type { State } from '../core';
import { setPlayerHeldItem } from '../plugins/player';

export interface HeldItemGrip {
  pos: [number, number, number];
  rot: [number, number, number];
  scale?: number;
}

export interface HeldItemGripRegistry {
  [weaponId: string]: HeldItemGrip;
}

function isHeldItemGrip(value: unknown): value is HeldItemGrip {
  if (typeof value !== 'object' || value === null) return false;
  const g = value as Record<string, unknown>;
  const isTriplet = (v: unknown): boolean =>
    Array.isArray(v) && v.length === 3 && v.every((n) => typeof n === 'number');
  return (
    isTriplet(g.pos) &&
    isTriplet(g.rot) &&
    (g.scale === undefined || typeof g.scale === 'number')
  );
}

/**
 * Fetch and validate a held-item grip registry from `url`.
 *
 * @throws {Error} if the fetch fails or the JSON does not match the expected shape.
 */
export async function loadHeldItemGrips(
  url: string
): Promise<HeldItemGripRegistry> {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(
      `loadHeldItemGrips: fetch failed for ${url} (HTTP ${res.status})`
    );
  }
  const data: unknown = await res.json();
  if (typeof data !== 'object' || data === null || Array.isArray(data)) {
    throw new Error(`loadHeldItemGrips: expected a JSON object at ${url}`);
  }
  const registry: HeldItemGripRegistry = {};
  for (const [key, val] of Object.entries(data as Record<string, unknown>)) {
    if (!isHeldItemGrip(val)) {
      throw new Error(`loadHeldItemGrips: invalid grip for "${key}" at ${url}`);
    }
    registry[key] = { pos: val.pos, rot: val.rot, scale: val.scale };
  }
  return registry;
}

/**
 * Look up `weaponId` in `registry` and, if found, attach `modelUrl` to the
 * entity's held-item slot with the matching grip. Returns `false` when the
 * weapon id is unknown (the caller should then clear/keep the hand as needed).
 *
 * The grip is applied through the engine's singleton held-item channel
 * (`setPlayerHeldItem`); `modelUrl` is required because the engine couples the
 * held model URL with its grip and re-applies them every frame.
 */
export function attachHeldItem(
  state: State,
  entityEid: number,
  weaponId: string,
  registry: HeldItemGripRegistry,
  modelUrl: string | null
): boolean {
  const grip = registry[weaponId];
  if (!grip || !state.exists(entityEid)) return false;
  const [x, y, z] = grip.pos;
  const [rx, ry, rz] = grip.rot;
  setPlayerHeldItem(modelUrl, { x, y, z, rx, ry, rz, scale: grip.scale ?? 1 });
  return true;
}
