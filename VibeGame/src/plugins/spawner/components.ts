import { MAX_ENTITIES } from '../../core/ecs/constants';

/** 0 = aguardando spawn; 1 = instâncias criadas. */
export const SpawnerPending = {
  spawned: new Uint8Array(MAX_ENTITIES),
} as const;

/** Same semantics as {@link SpawnerPending}, for `<entity place="…">` (deterministic terrain placement). */
export const PlacePending = {
  spawned: new Uint8Array(MAX_ENTITIES),
} as const;

/** Marks entities spawned on terrain; used to re-align Y after heightmap hot-reload. */
export const TerrainSpawned = {
  yOffset: new Float32Array(MAX_ENTITIES),
  surfaceEpsilon: new Float32Array(MAX_ENTITIES),
} as const;
