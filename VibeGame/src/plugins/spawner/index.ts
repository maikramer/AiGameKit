export { parseAt, parseSemicolonPlaceString } from './place-fields';
export {
  composeSpawnRotation,
  defaultTransformParts,
  formatTransformAttr,
  parseTransformAttr,
} from './transform-merge';
export { PlacePending, SpawnerPending, TerrainSpawned } from './components';
export { SpawnerPlugin } from './plugin';
export { entitySpawnerRecipe, spawnGroupRecipe } from './recipes';
export { spawnGroupParser } from './parser';
export { entityParser } from './entity-parser';
export { TerrainPlaceSystem } from './place-system';
export { TerrainSpawnBoundsCatchUpSystem, TerrainSpawnSystem } from './systems';
export { spawnTemplateAtTerrain } from './spawn-template';
export { templateVisualUrl } from './template-url';
export {
  applyTerrainSpawnedY,
  terrainSpawnedWorldY,
} from './terrain-spawned-y';
export {
  SpawnExclusion,
  registerSpawnFootprint,
  isSpawnAreaFree,
  clearSpawnOccupancy,
} from './occupancy';
export type { PlacementSpec } from './place-types';
export { getPlacementSpecs, setPlacementSpec } from './place-context';
export {
  isGroundMutationPending,
  isGroundReadyForPlacement,
  isTerrainHeightmapPending,
  isNormalWithinSlopeLimit,
  normalFromHeightSampler,
  placementDeferDecision,
  sampleMeshSurfaceHeight,
  sampleTerrainSurface,
  sampleTerrainSurfaceMatrix,
  slopeAngleRad,
  partialAlignEuler,
} from './surface';
export type { TerrainSurfaceSample } from './surface';
export {
  applyChildTemplateProfile,
  getGroupSpawnDefaults,
  isKnownGroupProfileForTests,
  normalizeGroupProfileId,
  optBool,
  optNumber,
  parseSpaceSeparatedNumbers,
  resolveGroupSpawnFields,
  roleToProfile,
  yawAnglesFromStepDeg,
} from './profiles';
export type {
  ChildTemplateProfileId,
  GroundAlignMode,
  GroupSpawnDefaults,
  SpawnGroupProfileId,
} from './profiles';
export type {
  ScaleDistributionMode,
  SpawnCountMode,
  SpawnGroupSpec,
  SpawnTemplateRole,
  SpawnTemplateSpec,
  YawDistributionMode,
} from './types';
export {
  SpawnVariation,
  defaultVariationForGroupProfile,
  getVariationPreset,
  hashWorldXZ,
  normalizeVariationPresetId,
  resolveVariationSpec,
  sampleVariation,
  writeSpawnVariation,
} from '../spawn-variation';
export type {
  VariationGeometryInput,
  VariationPresetId,
  VariationSample,
  VariationVisualSpec,
} from '../spawn-variation';
export { describeSpawnPending } from './ready-gate';
