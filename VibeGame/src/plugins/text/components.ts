import { MAX_ENTITIES } from '../../core/ecs/constants';

export const Paragraph = {
  gap: new Float32Array(MAX_ENTITIES),
  align: new Uint8Array(MAX_ENTITIES),
  anchorX: new Uint8Array(MAX_ENTITIES),
  anchorY: new Uint8Array(MAX_ENTITIES),
  damping: new Float32Array(MAX_ENTITIES),
} as const;

export const Word = {
  fontSize: new Float32Array(MAX_ENTITIES),
  color: new Uint32Array(MAX_ENTITIES),
  letterSpacing: new Float32Array(MAX_ENTITIES),
  lineHeight: new Float32Array(MAX_ENTITIES),
  outlineWidth: new Float32Array(MAX_ENTITIES),
  outlineColor: new Uint32Array(MAX_ENTITIES),
  outlineBlur: new Float32Array(MAX_ENTITIES),
  outlineOffsetX: new Float32Array(MAX_ENTITIES),
  outlineOffsetY: new Float32Array(MAX_ENTITIES),
  outlineOpacity: new Float32Array(MAX_ENTITIES),
  strokeWidth: new Float32Array(MAX_ENTITIES),
  strokeColor: new Uint32Array(MAX_ENTITIES),
  strokeOpacity: new Float32Array(MAX_ENTITIES),
  fillOpacity: new Float32Array(MAX_ENTITIES),
  curveRadius: new Float32Array(MAX_ENTITIES),
  width: new Float32Array(MAX_ENTITIES),
  dirty: new Uint8Array(MAX_ENTITIES),
} as const;

export enum Align {
  Left = 0,
  Center = 1,
  Right = 2,
}
