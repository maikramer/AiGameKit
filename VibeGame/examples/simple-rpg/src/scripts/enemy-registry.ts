// Tracks living "normal" enemies (goblins, slimes) so the boss can gate its
// appearance on "all of them defeated". Creatures register on spawn and
// unregister on death/destroy; the boss polls aliveEnemyCount()/everSpawned().

const alive = new Set<number>();
let spawnedAny = false;

export function registerEnemy(eid: number): void {
  alive.add(eid);
  spawnedAny = true;
}

export function unregisterEnemy(eid: number): void {
  alive.delete(eid);
}

export function aliveEnemyCount(): number {
  return alive.size;
}

/** True once at least one normal enemy has spawned (so the gate doesn't open
 * during the empty pre-spawn frame). */
export function everSpawned(): boolean {
  return spawnedAny;
}
