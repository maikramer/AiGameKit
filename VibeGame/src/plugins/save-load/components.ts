import { defineComponent, Types } from 'bitecs';

export const Serializable = defineComponent({
  flag: Types.ui8,
  serializationId: Types.ui32,
});
