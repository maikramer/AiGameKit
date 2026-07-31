/**
 * Per-asset door face in local space (game Y-up).
 * Omni eye-level 3/4 “front” is local +Z; some meshes put the door on a side.
 * Measured from lod0 screenshots + mesh recess (aigamekit-lab front/left/right/back).
 */
import type { DoorLocal } from 'vibegame';

export type BuildingDoorAsset =
  | 'village_house'
  | 'shepherd_cottage'
  | 'chapel'
  | 'village_forge'
  | 'village_barn'
  | 'village_longhouse'
  | 'market_stall';

/** size_m L×H×W from sample-gameassets/game.yaml — AABB half-extents ≈ L/2, W/2. */
const DOORS: Record<BuildingDoorAsset, DoorLocal> = {
  // 5×4.2×6 — door on long −X eave (not gable +Z); offset toward −Z
  village_house: {
    localX: -2.36,
    localZ: -1.35,
    faceYawDeg: -90,
    standOff: 1.2,
  },
  // 5.5×4×7.5 — primary door on +Z, left of center (also has rear opening)
  shepherd_cottage: {
    localX: -1.33,
    localZ: 3.75,
    faceYawDeg: 0,
    standOff: 1.2,
  },
  // 6×7×4.5 — arched door on +Z gable
  chapel: { localX: 0, localZ: 2.29, faceYawDeg: 0, standOff: 1.3 },
  // 6×5.5×5 — open workshop mouth on +Z, slightly +X
  village_forge: { localX: 0.53, localZ: 2.44, faceYawDeg: 0, standOff: 1.5 },
  // 8×6×11 — large barn doors on +Z gable (world east after yaw 90)
  village_barn: { localX: 0, localZ: 5.49, faceYawDeg: 0, standOff: 1.5 },
  // 5.5×5.5×10 — grand double doors on +Z gable
  village_longhouse: { localX: 0, localZ: 5.0, faceYawDeg: 0, standOff: 1.4 },
  // 3×2.5×2 — customer side under awning (+Z)
  market_stall: { localX: 0, localZ: 1.0, faceYawDeg: 0, standOff: 1.0 },
};

export function getDoorLocal(asset: BuildingDoorAsset): DoorLocal {
  return DOORS[asset];
}

export const DOOR_CATALOG: Readonly<Record<BuildingDoorAsset, DoorLocal>> =
  DOORS;
