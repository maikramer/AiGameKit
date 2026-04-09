import type { Plugin } from '../../core';
import { PhysicsJoint } from './components';
import { jointRecipe } from './recipes';
import { JointCleanupSystem, JointCreateSystem } from './systems';

export const JointsPlugin: Plugin = {
  systems: [JointCleanupSystem, JointCreateSystem],
  recipes: [jointRecipe],
  components: {
    physicsJoint: PhysicsJoint,
  },
  config: {
    defaults: {
      physicsJoint: {
        bodyA: 0,
        bodyB: 0,
        jointType: 1,
        anchorAX: 0,
        anchorAY: 0,
        anchorAZ: 0,
        anchorBX: 0,
        anchorBY: 0,
        anchorBZ: 0,
        axisX: 0,
        axisY: 1,
        axisZ: 0,
        limitsMin: 0,
        limitsMax: 6.28,
        motorSpeed: 0,
        motorMaxForce: 0,
        ropeLength: 1,
        springStiffness: 10,
        springDamping: 1,
        created: 0,
      },
    },
    enums: {
      physicsJoint: {
        type: {
          fixed: 0,
          revolute: 1,
          prismatic: 2,
          spherical: 3,
          rope: 4,
          spring: 5,
        },
      },
    },
  },
};
