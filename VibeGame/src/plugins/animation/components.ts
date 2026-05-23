import { MAX_ENTITIES } from '../../core/ecs/constants';

export const AnimatedCharacter = {
  headEntity: new Uint32Array(MAX_ENTITIES),
  torsoEntity: new Uint32Array(MAX_ENTITIES),
  leftArmEntity: new Uint32Array(MAX_ENTITIES),
  rightArmEntity: new Uint32Array(MAX_ENTITIES),
  leftLegEntity: new Uint32Array(MAX_ENTITIES),
  rightLegEntity: new Uint32Array(MAX_ENTITIES),
  phase: new Float32Array(MAX_ENTITIES),
  jumpTime: new Float32Array(MAX_ENTITIES),
  fallTime: new Float32Array(MAX_ENTITIES),
  animationState: new Uint8Array(MAX_ENTITIES),
  stateTransition: new Float32Array(MAX_ENTITIES),
} as const;

export const HasAnimator = {} as const;
