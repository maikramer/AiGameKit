import type { System } from '../../core';
import { assignSerializationIds } from './serializer';

export const SerializationIdSystem: System = {
  group: 'setup',
  update: (state) => {
    assignSerializationIds(state);
  },
};
