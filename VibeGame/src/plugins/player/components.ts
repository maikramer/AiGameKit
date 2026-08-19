import {
  defineComponent,
  F32,
  U32,
  U8,
} from '../../core/ecs/component-storage';

export const PlayerController = defineComponent({
  speed: F32,
  sprintMultiplier: F32,
  jumpHeight: F32,
  rotationSpeed: F32,
  canJump: U8,
  isJumping: U8,
  jumpCooldown: F32,
  lastGroundedTime: F32,
  jumpBufferTime: F32,
  cameraEntity: U32,
  inheritedVelX: F32,
  inheritedVelZ: F32,
  inheritedAngVelX: F32,
  inheritedAngVelY: F32,
  inheritedAngVelZ: F32,
  platformOffsetX: F32,
  platformOffsetY: F32,
  platformOffsetZ: F32,
  lastPlatform: U32,
});

export const PlayerGltfConfig = defineComponent({
  modelUrlIndex: U32,
  loaded: U8,
  animatorRegistryIndex: U32,
  idleClipIndex: U32,
  walkClipIndex: U32,
  runClipIndex: U32,
  jumpClipIndex: U32,
  overrideLock: U8,
  overrideClipIndex: U32,
});
