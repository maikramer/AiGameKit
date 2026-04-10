import { defineComponent, Types } from 'bitecs';

/** 0 = aguardando spawn; 1 = instâncias criadas. */
export const SpawnerPending = defineComponent({
  spawned: Types.ui8,
});

/** Same semantics as {@link SpawnerPending}, for `<entity place="…">` (deterministic terrain placement). */
export const PlacePending = defineComponent({
  spawned: Types.ui8,
});
