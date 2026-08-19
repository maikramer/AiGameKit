import {
  defineComponent,
  F32,
  U32,
  U8,
} from '../../core/ecs/component-storage';

/** Painel world-space (@pmndrs/uikit Container). */
export const HudPanel = defineComponent({
  width: F32,
  height: F32,
  bgR: F32,
  bgG: F32,
  bgB: F32,
  opacity: F32,
  textIndex: U32,
  built: U8,
});
