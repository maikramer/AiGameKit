import type { Plugin } from '../../core';
import { Serializable } from './components';
import { registerRpgSaveSerializers } from './rpg-serializers';
import { SerializationIdSystem } from './systems';

export const SaveLoadPlugin: Plugin = {
  systems: [SerializationIdSystem],
  components: {
    serializable: Serializable,
  },
  config: {
    defaults: {
      serializable: {
        flag: 0,
        serializationId: 0,
      },
    },
  },
  initialize(state) {
    registerRpgSaveSerializers(state);
  },
};
