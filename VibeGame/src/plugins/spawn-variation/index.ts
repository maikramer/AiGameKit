export { SpawnVariation, writeSpawnVariation } from './components';
export { findSpawnVariation } from './lookup';
export type { SpawnVariationValues } from './lookup';
export {
  maybePatchInstanceVariationMaterial,
  INSTANCE_VARIATION_UNIFORM_SCHEMA,
} from './material-patch';
export {
  defaultVariationForGroupProfile,
  getVariationPreset,
  normalizeVariationPresetId,
} from './presets';
export { resolveVariationSpec } from './resolve';
export { hashWorldXZ, sampleVariation } from './sample';
export type {
  VariationGeometryInput,
  VariationPresetId,
  VariationSample,
  VariationVisualSpec,
} from './types';
