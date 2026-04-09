import { defineComponent, Types } from 'bitecs';

/** Painel world-space (three-mesh-ui Block). */
export const HudPanel = defineComponent({
  width: Types.f32,
  height: Types.f32,
  bgR: Types.f32,
  bgG: Types.f32,
  bgB: Types.f32,
  opacity: Types.f32,
  textIndex: Types.ui32,
  built: Types.ui8,
});
