import { MAX_ENTITIES } from '../../core/ecs/constants';

export const PlayerController = {
  speed: new Float32Array(MAX_ENTITIES),
  jumpHeight: new Float32Array(MAX_ENTITIES),
  rotationSpeed: new Float32Array(MAX_ENTITIES),
  canJump: new Uint8Array(MAX_ENTITIES),
  isJumping: new Uint8Array(MAX_ENTITIES),
  jumpCooldown: new Float32Array(MAX_ENTITIES),
  lastGroundedTime: new Float32Array(MAX_ENTITIES),
  jumpBufferTime: new Float32Array(MAX_ENTITIES),
  cameraEntity: new Uint32Array(MAX_ENTITIES),
  inheritedVelX: new Float32Array(MAX_ENTITIES),
  inheritedVelZ: new Float32Array(MAX_ENTITIES),
  inheritedAngVelX: new Float32Array(MAX_ENTITIES),
  inheritedAngVelY: new Float32Array(MAX_ENTITIES),
  inheritedAngVelZ: new Float32Array(MAX_ENTITIES),
  platformOffsetX: new Float32Array(MAX_ENTITIES),
  platformOffsetY: new Float32Array(MAX_ENTITIES),
  platformOffsetZ: new Float32Array(MAX_ENTITIES),
  lastPlatform: new Uint32Array(MAX_ENTITIES),
} as const;

export const PlayerGltfConfig = {
  modelUrlIndex: new Uint32Array(MAX_ENTITIES),
  loaded: new Uint8Array(MAX_ENTITIES),
  animatorRegistryIndex: new Uint32Array(MAX_ENTITIES),
  idleClipIndex: new Uint32Array(MAX_ENTITIES),
  walkClipIndex: new Uint32Array(MAX_ENTITIES),
  runClipIndex: new Uint32Array(MAX_ENTITIES),
  jumpClipIndex: new Uint32Array(MAX_ENTITIES),
  overrideLock: new Uint8Array(MAX_ENTITIES),
  overrideClipIndex: new Uint32Array(MAX_ENTITIES),
} as const;
