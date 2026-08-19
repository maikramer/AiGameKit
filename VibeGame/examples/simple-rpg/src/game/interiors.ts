/**
 * Interior destinations for building portals.
 * Missing registry entry = interact shows “em breve” stub.
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
 * `exit_market` é sentinela: o runtime remapeia para a banca de onde o
 * jogador entrou (a/b/c).
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
  | 'exit_house_b'
  | 'exit_house_c'
  | 'exit_shepherd_cottage'
  | 'exit_chapel'
  | 'exit_village_forge'
  | 'exit_village_barn'
  | 'exit_longhouse'
  | 'exit_market'
  | 'exit_market_stall_a'
  | 'exit_market_stall_b'
  | 'exit_market_stall_c';

const REGISTRY: Partial<Record<InteriorId, InteriorSpawn | null>> = {
  // Salas autoradas em public/world/interiors.xml — cena à parte, FORA do
  // terreno (x≈3017..3168; o mapa é ±2000) e fechada numa caixa preta.
  // Shells: chapel 24×18, forge 22×16, house 20×16, barn/longhouse 28×20,
  // market 18×14. Paredes 0.70 m, sem teto, vão −Z. y=0 porque fora dos
  // limites do heightmap `sampleHeightAt` devolve 0 e o chão de cada sala é
  // o <Pad> a 0.12 m; resolveFeetY ancora o spawn nesse collider (já não há
  // TerrainPad). Spawn no CENTRO: anti-bounce F (alcance portal 2.8 m;
  // exit no vão ≈ 0.9 m para dentro da parede sul).
  chapel: { x: 857, y: 153, z: 226 },
  village_forge: { x: 917, y: 153, z: 226 },
  house_a: { x: 797, y: 153, z: 226 },
  house_b: { x: 797, y: 153, z: 281 },
  house_c: { x: 857, y: 153, z: 281 },
  shepherd_cottage: { x: 917, y: 153, z: 281 },
  village_barn: { x: 797, y: 153, z: 336 },
  longhouse: { x: 862, y: 153, z: 336 },
  // Três bancas partilham a mesma sala; a saída volta à porta de entrada.
  market_stall_a: { x: 927, y: 153, z: 336 },
  market_stall_b: { x: 927, y: 153, z: 336 },
  market_stall_c: { x: 927, y: 153, z: 336 },
  // Saídas → portas exteriores (portals.xml).
  exit_chapel: { x: 7.46, y: 0, z: 22.46 },
  exit_village_forge: { x: -30.47, y: 0, z: -29.06 },
  exit_house_a: { x: 26.35, y: 0, z: 8.44 },
  exit_house_b: { x: -17.44, y: 0, z: 22.47 },
  exit_house_c: { x: -20.47, y: 0, z: -18.44 },
  exit_shepherd_cottage: { x: -22.33, y: 0, z: 12.95 },
  exit_village_barn: { x: -26.11, y: 0, z: 30 },
  exit_longhouse: { x: 35.53, y: 0, z: -37.53 },
  exit_market_stall_a: { x: 10.1, y: 0, z: -15.7 },
  exit_market_stall_b: { x: 11.7, y: 0, z: -14.4 },
  exit_market_stall_c: { x: 18.9, y: 0, z: -20.3 },
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
