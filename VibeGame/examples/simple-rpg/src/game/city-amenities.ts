// Plaza / forge amenity numbers — pure data so tests can lock the loop
// without booting the runtime. Scripts import these. travel.ts reads
// LOOKOUT_GATES for death respawn (plus marked Nota landings).

export const CAMPFIRE_HEAL = 40;
export const CAMPFIRE_COOLDOWN = 45;

export const WELL_HEAL = 12;
export const WELL_COOLDOWN = 15;

export const BOMB_CRAFT_STONE = 2;
export const BOMB_CRAFT_WOOD = 1;

export function canCraftBomb(stone: number, wood: number): boolean {
  return stone >= BOMB_CRAFT_STONE && wood >= BOMB_CRAFT_WOOD;
}

export interface LookoutGate {
  readonly id: string;
  readonly x: number;
  readonly z: number;
  readonly label: string;
  readonly color: string;
}

/** Cardinal gates — same XZ as RespawnSystem's gate checkpoints. */
export const LOOKOUT_GATES: readonly LookoutGate[] = [
  { id: 'lookout:forest', x: 0, z: 50, label: 'Floresta', color: '#6dbf6a' },
  { id: 'lookout:desert', x: 50, z: 0, label: 'Deserto', color: '#e0b050' },
  { id: 'lookout:swamp', x: 0, z: -50, label: 'Pântano', color: '#7a9a6a' },
  { id: 'lookout:peaks', x: -50, z: 0, label: 'Picos', color: '#9ec8e8' },
];

export const LOOKOUT_WAYPOINT_PREFIX = 'lookout:';
