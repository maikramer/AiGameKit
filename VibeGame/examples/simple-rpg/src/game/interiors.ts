/**
 * Interior destinations for building portals.
 * Empty / null = interact shows “em breve” stub until an area is authored.
 */
export interface InteriorSpawn {
  x: number;
  y: number;
  z: number;
}

/** Stable interior ids — match portal defs / future world Includes. */
export type InteriorId =
  | 'house_a'
  | 'house_b'
  | 'house_c'
  | 'shepherd_cottage'
  | 'chapel'
  | 'village_forge'
  | 'village_barn'
  | 'longhouse'
  | 'market_stall_a'
  | 'market_stall_b'
  | 'market_stall_c';

const REGISTRY: Partial<Record<InteriorId, InteriorSpawn | null>> = {
  house_a: null,
  house_b: null,
  house_c: null,
  shepherd_cottage: null,
  chapel: null,
  village_forge: null,
  village_barn: null,
  longhouse: null,
  market_stall_a: null,
  market_stall_b: null,
  market_stall_c: null,
};

export function getInteriorSpawn(id: string): InteriorSpawn | null {
  if (!(id in REGISTRY)) return null;
  return REGISTRY[id as InteriorId] ?? null;
}

export function setInteriorSpawn(
  id: InteriorId,
  spawn: InteriorSpawn | null
): void {
  REGISTRY[id] = spawn;
}

export function listInteriorIds(): InteriorId[] {
  return Object.keys(REGISTRY) as InteriorId[];
}
