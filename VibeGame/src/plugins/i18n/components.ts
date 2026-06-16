import { MAX_ENTITIES } from '../../core/ecs/constants';

export const I18nText = {
  keyIndex: new Uint32Array(MAX_ENTITIES),
  resolved: new Uint8Array(MAX_ENTITIES),
} as const;

export const I18nConfig = {
  autoEngineDefaults: new Uint8Array(MAX_ENTITIES),
  applied: new Uint8Array(MAX_ENTITIES),
} as const;
