import { defineComponent, U8 } from '../../core/ecs/component-storage';

/** Marks an entity that runs a TS module from XML `script="…"`. */
export const MonoBehaviour = defineComponent({
  ready: U8,
  enabled: U8,
});
