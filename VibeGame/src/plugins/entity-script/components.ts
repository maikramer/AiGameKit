import { defineComponent, Types } from 'bitecs';

/** Marks an entity that runs a TS module from XML `script="…"`. */
export const MonoBehaviour = defineComponent({
  /** 0 = setup not done; 1 = setup completed. */
  ready: Types.ui8,
  /** 1 = call `update` each frame after setup. */
  enabled: Types.ui8,
});
