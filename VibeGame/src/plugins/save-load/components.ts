import { defineComponent, U32, U8 } from '../../core/ecs/component-storage';

export const Serializable = defineComponent({
  flag: U8,
  serializationId: U32,
});
