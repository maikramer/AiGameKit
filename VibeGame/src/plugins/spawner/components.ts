import { defineComponent, Types } from 'bitecs';

/** 0 = aguardando spawn; 1 = instâncias criadas. */
export const SpawnerPending = defineComponent({
  spawned: Types.ui8,
});

/** Same semantics as {@link SpawnerPending}, for `<entity place="…">` (deterministic terrain placement). */
export const PlacePending = defineComponent({
  spawned: Types.ui8,
});

/** Marks entities spawned on terrain; used to re-align Y after heightmap hot-reload. */
export const TerrainSpawned = defineComponent({
  yOffset: Types.f32,
  surfaceEpsilon: Types.f32,
});
