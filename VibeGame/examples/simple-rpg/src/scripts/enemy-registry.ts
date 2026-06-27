// Tracks living "normal" enemies (goblins, slimes) so the boss can gate its
// appearance on "all of them defeated". Creatures register on spawn and
// unregister on death/destroy; the boss polls aliveEnemyCount()/everSpawned().

const BIOME_IDS = ['dark-forest', 'desert', 'swamp', 'frozen-peaks'] as const;
export type BiomeId = (typeof BIOME_IDS)[number];

const alive = new Set<number>();
const biomeOf = new Map<number, string>();
const counts = new Map<string, number>();
let spawnedAny = false;

// Biome regions are the 4 diagonal wedges from <BiomeRegion polygon> in index.html; the predicates mirror those polygons exactly.
export function biomeAtPosition(x: number, z: number): string | null {
  if (x <= -Math.abs(z)) return 'frozen-peaks';
  if (x >= Math.abs(z)) return 'desert';
  if (z >= Math.abs(x)) return 'dark-forest';
  if (z <= -Math.abs(x)) return 'swamp';
  return null;
}

export function registerEnemy(eid: number, x: number, z: number): void {
  alive.add(eid);
  const b = biomeAtPosition(x, z);
  if (b) {
    biomeOf.set(eid, b);
    counts.set(b, (counts.get(b) ?? 0) + 1);
  }
  spawnedAny = true;
}

export function unregisterEnemy(eid: number): void {
  if (!alive.delete(eid)) return;
  const b = biomeOf.get(eid);
  if (b) {
    biomeOf.delete(eid);
    const next = (counts.get(b) ?? 0) - 1;
    if (next <= 0) counts.delete(b);
    else counts.set(b, next);
  }
}

export function aliveEnemyCount(): number {
  return alive.size;
}

export function aliveInBiome(biome: string): number {
  return counts.get(biome) ?? 0;
}

/** True once at least one normal enemy has spawned (so the gate doesn't open
 * during the empty pre-spawn frame). */
export function everSpawned(): boolean {
  return spawnedAny;
}
