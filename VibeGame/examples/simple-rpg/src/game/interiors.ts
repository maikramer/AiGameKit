/**
 * Interior destinations for building portals.
 * Empty / null = interact shows “em breve” stub until an area is authored.
 */
export interface InteriorSpawn {
  x: number;
  y: number;
  z: number;
}

/**
 * Stable interior ids — match portal defs / world Includes.
 * Entradas `<id>`: spawn dentro da sala (public/world/interiors.xml).
 * Entradas `exit_<id>`: posição da porta EXTERIOR (portals.xml) — o portal de
 * saída dentro da sala teleporta para cá. y é fallback; o runtime resolve a
 * altura do terreno no ponto (resolveFeetY).
 */
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
  | 'market_stall_c'
  | 'exit_house_a'
  | 'exit_chapel'
  | 'exit_village_forge';

const REGISTRY: Partial<Record<InteriorId, InteriorSpawn | null>> = {
  house_b: null,
  house_c: null,
  shepherd_cottage: null,
  // Salas autoradas em public/world/interiors.xml (zona (-410, 120)).
  // Shells: chapel 16×12, forge 14×11, house_a 12×10 — exit em −Z (>3 m do
  // centro). y = fallback; resolveFeetY + TerrainPad (~150.6) ancoram o spawn.
  // Spawn no CENTRO: anti-bounce F (alcance portal 2.8 m).
  chapel: { x: -410, y: 150.6, z: 120 },
  village_forge: { x: -388, y: 150.6, z: 120 },
  house_a: { x: -432, y: 150.6, z: 120 },
  village_barn: null,
  longhouse: null,
  market_stall_a: null,
  market_stall_b: null,
  market_stall_c: null,
  // Saídas → portas exteriores (portals.xml).
  exit_chapel: { x: 7.46, y: 0, z: 22.46 },
  exit_village_forge: { x: -30.47, y: 0, z: -29.06 },
  exit_house_a: { x: 26.35, y: 0, z: 8.44 },
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
