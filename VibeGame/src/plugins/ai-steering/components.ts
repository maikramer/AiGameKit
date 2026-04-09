import { defineComponent, Types } from 'bitecs';

/** 0 seek, 1 wander, 2 flee */
export const SteeringAgent = defineComponent({
  behavior: Types.ui8,
  maxSpeed: Types.f32,
  maxForce: Types.f32,
  active: Types.ui8,
});

/** Se `targetEntity` > 0, segue essa entidade; senão usa targetX/Y/Z. */
export const SteeringTarget = defineComponent({
  targetEntity: Types.eid,
  targetX: Types.f32,
  targetY: Types.f32,
  targetZ: Types.f32,
});
