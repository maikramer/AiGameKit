import type { Plugin } from '../../core';
import {
  Rigidbody,
  Collider,
  CharacterController,
  CharacterMovement,
} from './components';
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
    CharacterController,
    CharacterMovement,
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
      'character-controller': {
        offset: 0.08,
        maxSlope: 45 * (Math.PI / 180),
        maxSlide: 30 * (Math.PI / 180),
        snapDist: 0.5,
        autoStep: 1,
        maxStepHeight: 0.3,
        minStepWidth: 0.05,
        upX: 0,
        upY: 1,
        upZ: 0,
      },
      'character-movement': {
        desiredVelX: 0,
        desiredVelY: 0,
        desiredVelZ: 0,
        velocityY: 0,
      },
    },
    enums: {
      rigidbody: {
        type: {
          dynamic: 0,
          fixed: 1,
          'kinematic-position': 2,
          'kinematic-position-based': 2,
          'kinematic-velocity': 3,
          'kinematic-velocity-based': 3,
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
