import * as RAPIER from "@dimforge/rapier3d-simd";

const GRAVITY_Y = -60;
const TIMESTEP = 1 / 50;

let world: RAPIER.World | null = null;
let initialized = false;

export async function initPhysics(): Promise<void> {
  if (initialized) return;
  await RAPIER.init();
  initialized = true;
}

export function getOrCreateWorld(): RAPIER.World {
  if (!world) {
    world = new RAPIER.World(new RAPIER.Vector3(0, GRAVITY_Y, 0));
    world.timestep = TIMESTEP;
  }
  return world;
}

export function getWorld(): RAPIER.World | null {
  return world;
}

export function stepWorld(): void {
  if (world) {
    world.step();
  }
}
