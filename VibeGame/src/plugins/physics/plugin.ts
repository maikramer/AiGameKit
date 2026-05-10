import type { Plugin } from '../../core';
import { Rigidbody, Collider } from './components';
import {
  PhysicsInitSystem,
  ApplyMovementSystem,
  ApplyJumpSystem,
  PhysicsStepSystem,
  PhysicsSyncSystem,
} from './systems';
import { initPhysics } from './world';
import { rigidbodyRecipe, colliderRecipe, dynamicPartRecipe } from './recipes';

export const PhysicsPlugin: Plugin = {
  initialize: initPhysics,
  systems: [
    PhysicsInitSystem,
    ApplyMovementSystem,
    ApplyJumpSystem,
    PhysicsStepSystem,
    PhysicsSyncSystem,
  ],
  recipes: [rigidbodyRecipe, colliderRecipe, dynamicPartRecipe],
  components: {
    Rigidbody,
    Collider,
  },
  config: {
    defaults: {
      rigidbody: {
        type: 0,
        mass: 1,
        gravityScale: 1,
        rotW: 1,
      },
      collider: {
        shape: 0,
        sizeX: 1,
        sizeY: 1,
        sizeZ: 1,
        radius: 0.5,
        height: 1,
        friction: 0.5,
        restitution: 0,
        density: 1,
        sensor: 0,
        membershipGroups: 0xffff,
        filterGroups: 0xffff,
        posOffsetX: 0,
        posOffsetY: 0,
        posOffsetZ: 0,
        rotOffsetX: 0,
        rotOffsetY: 0,
        rotOffsetZ: 0,
        rotOffsetW: 1,
      },
    },
    enums: {
      rigidbody: {
        type: {
          dynamic: 0,
          fixed: 1,
        },
      },
      collider: {
        shape: {
          box: 0,
          sphere: 1,
          capsule: 2,
        },
      },
    },
  },
};
