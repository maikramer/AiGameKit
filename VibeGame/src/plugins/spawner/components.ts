import { defineComponent, Types } from 'bitecs';

/** 0 = aguardando spawn; 1 = instâncias criadas. */
export const SpawnerPending = defineComponent({
  spawned: Types.ui8,
});
