import { defineSystem, type System } from '../../core';
import { assignSerializationIds } from './serializer';

export const SerializationIdSystem: System = defineSystem({
  name: 'SerializationIdSystem',
  group: 'setup',
  update: (state) => {
    assignSerializationIds(state);
  },
});
