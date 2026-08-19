import { defineComponent, U32, U8 } from '../../core/ecs/component-storage';

export const InventoryComponent = defineComponent({
  slots: U32,
  capacity: U8,
  version: U32,
});
