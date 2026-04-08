import { defineComponent, Types } from 'bitecs';

/** Configuração de sky environment por entidade. */
export const Sky = defineComponent({
  /** URL da textura equirectangular (2:1 PNG/JPG/HDR). */
  urlIndex: Types.ui32,
  /** Rotação horizontal em graus (0-360). */
  rotationDeg: Types.f32,
  /** Se 1, aplica como background; se 0, só iluminação IBL. */
  setBackground: Types.ui8,
  /** Se 1, sky já foi aplicado. */
  loaded: Types.ui8,
});
