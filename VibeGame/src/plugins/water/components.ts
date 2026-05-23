import { MAX_ENTITIES } from '../../core/ecs/constants';

export enum WaterSubmersionState {
  Outside = 0,
  Entering = 1,
  Submerged = 2,
  Exiting = 3,
}

export const Water = {
  size: new Float32Array(MAX_ENTITIES),
  waterLevel: new Float32Array(MAX_ENTITIES),
  opacity: new Float32Array(MAX_ENTITIES),
  tintR: new Float32Array(MAX_ENTITIES),
  tintG: new Float32Array(MAX_ENTITIES),
  tintB: new Float32Array(MAX_ENTITIES),
  waveSpeed: new Float32Array(MAX_ENTITIES),
  waveScale: new Float32Array(MAX_ENTITIES),
  wireframe: new Uint8Array(MAX_ENTITIES),
  underwaterFogColorR: new Float32Array(MAX_ENTITIES),
  underwaterFogColorG: new Float32Array(MAX_ENTITIES),
  underwaterFogColorB: new Float32Array(MAX_ENTITIES),
  underwaterFogDensity: new Float32Array(MAX_ENTITIES),
} as const;

export const PlayerWaterState = {
  state: new Uint8Array(MAX_ENTITIES),
  waterEntity: new Uint32Array(MAX_ENTITIES),
  entryTime: new Float32Array(MAX_ENTITIES),
  submersionDepth: new Float32Array(MAX_ENTITIES),
  swimTriggered: new Uint8Array(MAX_ENTITIES),
  swimZoneEntity: new Uint32Array(MAX_ENTITIES),
} as const;

export const SwimTriggerZone = {
  waterEntity: new Uint32Array(MAX_ENTITIES),
  enabled: new Uint8Array(MAX_ENTITIES),
  swimSpeed: new Float32Array(MAX_ENTITIES),
  buoyancyForce: new Float32Array(MAX_ENTITIES),
  maxSwimDepth: new Float32Array(MAX_ENTITIES),
} as const;
