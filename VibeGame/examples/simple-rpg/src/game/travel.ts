// Fast travel + death respawn for A Nota.
//
// Marked landmarks become paths: the plaza campfire lists them, [F] at a
// marked marco returns to the plaza, and death picks the nearest of plaza,
// cardinal gates, and those landings. Pure helpers stay testable; the
// TravelHomeSystem owns the return prompt so NotaSystem can keep marking.
import type { State, System } from 'vibegame';
import {
  Transform,
  PlayerController,
  defineQuery,
  isKeyDown,
  isPaused,
  registerInteractionTarget,
  unregisterInteractionTarget,
  getBodyForEntity,
  getTerrainHeightAt,
  getBvhSurfaceHeight,
  getBodyYForFeetAt,
  terrainReady,
  GROUND_CONTACT_SKIN,
  playSound,
} from 'vibegame';
import { teleportEntity } from '../../../shared/src/physics';
import { showToast } from '../../../shared/src/ui';
import { LOOKOUT_GATES } from './city-amenities';
import {
  NOTA_MARK_RADIUS,
  biomeOfLandmark,
  landmarkLabel,
  notaSnapshot,
  type BiomeId,
} from './nota';
import { findPlayer } from './player-query';

export const PLAZA_XZ: readonly [number, number] = [0, 0];
export const TRAVEL_FALLBACK_Y = 50;
/** Stand this far toward the city so the landing is not inside the mesh. */
export const LANDMARK_LANDING_OFFSET_M = 3.5;

const LANDING_ASSIST_TICK_MS = 100;
const LANDING_ASSIST_FALL_TOLERANCE = 0.6;
const LANDING_ASSIST_MAX_TICKS = 60;

export interface TravelStop {
  readonly id: string;
  readonly label: string;
  readonly biome: BiomeId;
  readonly x: number;
  readonly z: number;
}

export function landmarkLandingXZ(
  x: number,
  z: number,
  offsetM: number = LANDMARK_LANDING_OFFSET_M
): { x: number; z: number } {
  const len = Math.hypot(x, z);
  if (len < 1e-3) return { x, z };
  const s = offsetM / len;
  return { x: x - x * s, z: z - z * s };
}

/** Marked Nota landmarks that still exist in the scene, with a safe landing. */
export function travelDestinations(state: State): TravelStop[] {
  const out: TravelStop[] = [];
  for (const name of notaSnapshot().marked) {
    const biome = biomeOfLandmark(name);
    if (!biome) continue;
    const eid = state.getEntityByName(name);
    if (eid === null) continue;
    const land = landmarkLandingXZ(Transform.posX[eid], Transform.posZ[eid]);
    out.push({
      id: name,
      label: landmarkLabel(name),
      biome,
      x: land.x,
      z: land.z,
    });
  }
  return out.sort((a, b) => a.label.localeCompare(b.label, 'pt'));
}

export function respawnCandidates(
  state: State
): Array<readonly [number, number]> {
  const pts: Array<readonly [number, number]> = [
    PLAZA_XZ,
    ...LOOKOUT_GATES.map((g) => [g.x, g.z] as const),
  ];
  for (const stop of travelDestinations(state)) {
    pts.push([stop.x, stop.z]);
  }
  return pts;
}

export function nearestRespawn(
  candidates: ReadonlyArray<readonly [number, number]>,
  x: number,
  z: number
): readonly [number, number] {
  let best: readonly [number, number] = candidates[0] ?? PLAZA_XZ;
  let bestD2 = Infinity;
  for (const p of candidates) {
    const d2 = (p[0] - x) ** 2 + (p[1] - z) ** 2;
    if (d2 < bestD2) {
      bestD2 = d2;
      best = p;
    }
  }
  return best;
}

export function resolveFeetY(
  state: State,
  player: number,
  x: number,
  z: number,
  fallbackY: number
): number {
  if (!terrainReady(state)) return fallbackY;
  const terrainH = getTerrainHeightAt(state, x, z);
  const bvh = getBvhSurfaceHeight(state, x, 500, z);
  const groundY = Number.isFinite(terrainH)
    ? terrainH
    : Number.isFinite(bvh)
      ? (bvh as number)
      : null;
  if (groundY === null) return fallbackY;
  return getBodyYForFeetAt(state, player, groundY + GROUND_CONTACT_SKIN);
}

function startLandingAssist(
  state: State,
  player: number,
  x: number,
  y: number,
  z: number
): void {
  let ticks = 0;
  let stableTicks = 0;
  const iv = setInterval(() => {
    const body = getBodyForEntity(state, player);
    if (!body) {
      clearInterval(iv);
      return;
    }
    const bodyY = body.translation().y;
    if (y - bodyY <= LANDING_ASSIST_FALL_TOLERANCE) {
      if (++stableTicks >= 2) {
        clearInterval(iv);
        return;
      }
    } else {
      stableTicks = 0;
      if (++ticks > LANDING_ASSIST_MAX_TICKS) {
        clearInterval(iv);
        return;
      }
      teleportEntity(state, player, x, y, z);
    }
  }, LANDING_ASSIST_TICK_MS);
}

/** Teleport onto terrain and hold the body until far-chunk colliders exist. */
export function teleportPlayerToGround(
  state: State,
  player: number,
  x: number,
  z: number,
  fallbackY: number = TRAVEL_FALLBACK_Y
): void {
  const y = resolveFeetY(state, player, x, z, fallbackY);
  teleportEntity(state, player, x, y, z);
  startLandingAssist(state, player, x, y, z);
}

export function travelToStop(
  state: State,
  player: number,
  stop: { x: number; z: number; label: string }
): void {
  teleportPlayerToGround(state, player, stop.x, stop.z);
  playSound('save');
  showToast(`Caminho da Nota — ${stop.label}`, {
    color: '#ffe3a0',
    borderColor: '#c8a04a',
    background: 'rgba(20,15,10,0.95)',
    durationMs: 2200,
  });
}

export function travelToPlaza(state: State, player: number): void {
  teleportPlayerToGround(state, player, PLAZA_XZ[0], PLAZA_XZ[1]);
  playSound('save');
  showToast('A fogueira chama. Volta à praça.', {
    color: '#ffc070',
    borderColor: '#c06020',
    background: 'rgba(22,12,8,0.95)',
    durationMs: 2200,
  });
}

// ── [F] at a marked landmark → plaza ──────────────────────────────────────
const INTERACT_KEY = 'KeyF';
const playerQuery = defineQuery([PlayerController, Transform]);
const homeRegistered = new Map<number, string>();
let fHeld = false;

export const TravelHomeSystem: System = {
  name: 'TravelHomeSystem',
  group: 'simulation',
  before: ['NotaSystem'],
  update(state: State) {
    const pressed = isKeyDown(INTERACT_KEY);
    const justPressed = pressed && !fHeld;
    fHeld = pressed;

    const players = playerQuery(state.world);
    const player = players[0] ?? findPlayer(state);
    if (!player) return;
    const px = Transform.posX[player];
    const pz = Transform.posZ[player];
    const marked = new Set(notaSnapshot().marked);

    let nearest = 0;
    let nearestDist = Infinity;

    for (const [eid, name] of [...homeRegistered]) {
      if (!marked.has(name) || state.getEntityByName(name) !== eid) {
        unregisterInteractionTarget(state, eid);
        homeRegistered.delete(eid);
      }
    }

    for (const name of marked) {
      const biome = biomeOfLandmark(name);
      if (!biome) continue;
      const eid = state.getEntityByName(name);
      if (eid === null) continue;

      if (!homeRegistered.has(eid)) {
        registerInteractionTarget(state, eid, {
          label: 'Voltar à praça',
          key: 'F',
          kind: 'landmark',
          range: NOTA_MARK_RADIUS[biome],
        });
        homeRegistered.set(eid, name);
      }

      const dx = Transform.posX[eid] - px;
      const dz = Transform.posZ[eid] - pz;
      const d = dx * dx + dz * dz;
      const radiusSq = NOTA_MARK_RADIUS[biome] ** 2;
      if (d <= radiusSq && d < nearestDist) {
        nearestDist = d;
        nearest = eid;
      }
    }

    if (!justPressed || isPaused(state) || nearest === 0) return;
    travelToPlaza(state, player);
  },
};
