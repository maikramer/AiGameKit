import { defineComponent, Types } from 'bitecs';

export const BodyType = { Dynamic: 0, Fixed: 1 } as const;

export const ColliderShape = { Box: 0, Sphere: 1, Capsule: 2 } as const;

export const Rigidbody = defineComponent({
  type: Types.ui8,
  mass: Types.f32,
  gravityScale: Types.f32,
  lockRotX: Types.ui8,
  lockRotY: Types.ui8,
  lockRotZ: Types.ui8,

  posX: Types.f32,
  posY: Types.f32,
  posZ: Types.f32,
  rotX: Types.f32,
  rotY: Types.f32,
  rotZ: Types.f32,
  rotW: Types.f32,
  eulerX: Types.f32,
  eulerY: Types.f32,
  eulerZ: Types.f32,

  velX: Types.f32,
  velY: Types.f32,
  velZ: Types.f32,
});

export const Collider = defineComponent({
  shape: Types.ui8,
  sizeX: Types.f32,
  sizeY: Types.f32,
  sizeZ: Types.f32,
  radius: Types.f32,
  height: Types.f32,
  friction: Types.f32,
  restitution: Types.f32,
  density: Types.f32,
  sensor: Types.ui8,
  membershipGroups: Types.ui16,
  filterGroups: Types.ui16,
  posOffsetX: Types.f32,
  posOffsetY: Types.f32,
  posOffsetZ: Types.f32,
  rotOffsetX: Types.f32,
  rotOffsetY: Types.f32,
  rotOffsetZ: Types.f32,
  rotOffsetW: Types.f32,
});
