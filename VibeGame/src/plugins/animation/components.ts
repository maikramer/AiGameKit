import {
  defineComponent,
  F32,
  U32,
  U8,
} from '../../core/ecs/component-storage';

export const AnimatedCharacter = defineComponent({
  headEntity: U32,
  torsoEntity: U32,
  leftArmEntity: U32,
  rightArmEntity: U32,
  leftLegEntity: U32,
  rightLegEntity: U32,
  phase: F32,
  jumpTime: F32,
  fallTime: F32,
  animationState: U8,
  stateTransition: F32,
});

export const HasAnimator = {} as const;
