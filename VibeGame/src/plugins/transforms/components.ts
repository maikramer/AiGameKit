import { defineComponent, F32, U8 } from '../../core/ecs/component-storage';

export const Transform = defineComponent({
  posX: F32,
  posY: F32,
  posZ: F32,
  rotX: F32,
  rotY: F32,
  rotZ: F32,
  rotW: F32,
  eulerX: F32,
  eulerY: F32,
  eulerZ: F32,
  scaleX: F32,
  scaleY: F32,
  scaleZ: F32,
  dirty: U8,
});

export const WorldTransform = defineComponent({
  posX: F32,
  posY: F32,
  posZ: F32,
  rotX: F32,
  rotY: F32,
  rotZ: F32,
  rotW: F32,
  eulerX: F32,
  eulerY: F32,
  eulerZ: F32,
  scaleX: F32,
  scaleY: F32,
  scaleZ: F32,
});
