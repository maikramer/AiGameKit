import { defineComponent, Types } from 'bitecs';

export const Player = defineComponent({
  speed: Types.f32,
  jumpHeight: Types.f32,
  rotationSpeed: Types.f32,
  canJump: Types.ui8,
  isJumping: Types.ui8,
  jumpCooldown: Types.f32,
  lastGroundedTime: Types.f32,
  jumpBufferTime: Types.f32,
  cameraEntity: Types.eid,
  inheritedVelX: Types.f32,
  inheritedVelZ: Types.f32,
  inheritedAngVelX: Types.f32,
  inheritedAngVelY: Types.f32,
  inheritedAngVelZ: Types.f32,
  platformOffsetX: Types.f32,
  platformOffsetY: Types.f32,
  platformOffsetZ: Types.f32,
  lastPlatform: Types.eid,
});

export const PlayerGltfConfig = defineComponent({
  modelUrlIndex: Types.ui32,
  loaded: Types.ui8,
  animatorRegistryIndex: Types.ui32,
});
