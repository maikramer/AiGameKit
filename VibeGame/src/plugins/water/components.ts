import { defineComponent, Types } from 'bitecs';

export enum WaterSubmersionState {
  Outside = 0,
  Entering = 1,
  Submerged = 2,
  Exiting = 3,
}

export const Water = defineComponent({
  size: Types.f32,
  waterLevel: Types.f32,
  opacity: Types.f32,
  tintR: Types.f32,
  tintG: Types.f32,
  tintB: Types.f32,
  waveSpeed: Types.f32,
  waveScale: Types.f32,
  wireframe: Types.ui8,
  underwaterFogColorR: Types.f32,
  underwaterFogColorG: Types.f32,
  underwaterFogColorB: Types.f32,
  underwaterFogDensity: Types.f32,
});

export const PlayerWaterState = defineComponent({
  state: Types.ui8,
  waterEntity: Types.eid,
  entryTime: Types.f32,
  submersionDepth: Types.f32,
  swimTriggered: Types.ui8,
  swimZoneEntity: Types.eid,
});

export const SwimTriggerZone = defineComponent({
  waterEntity: Types.eid,
  enabled: Types.ui8,
  swimSpeed: Types.f32,
  buoyancyForce: Types.f32,
  maxSwimDepth: Types.f32,
});
