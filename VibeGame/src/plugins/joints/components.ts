import { defineComponent, Types } from 'bitecs';

/** 0 fixed, 1 revolute, 2 prismatic, 3 spherical, 4 rope, 5 spring */
export const PhysicsJoint = defineComponent({
  bodyA: Types.eid,
  bodyB: Types.eid,
  jointType: Types.ui8,
  anchorAX: Types.f32,
  anchorAY: Types.f32,
  anchorAZ: Types.f32,
  anchorBX: Types.f32,
  anchorBY: Types.f32,
  anchorBZ: Types.f32,
  axisX: Types.f32,
  axisY: Types.f32,
  axisZ: Types.f32,
  limitsMin: Types.f32,
  limitsMax: Types.f32,
  motorSpeed: Types.f32,
  motorMaxForce: Types.f32,
  ropeLength: Types.f32,
  springStiffness: Types.f32,
  springDamping: Types.f32,
  created: Types.ui8,
});
