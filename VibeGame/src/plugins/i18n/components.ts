import { defineComponent, U32, U8 } from '../../core/ecs/component-storage';

export const I18nText = defineComponent({
  keyIndex: U32,
  resolved: U8,
});

export const I18nConfig = defineComponent({
  autoEngineDefaults: U8,
  applied: U8,
});
