export { PlacePending, SpawnerPending } from './components';
export { SpawnerPlugin } from './plugin';
export { entitySpawnerRecipe, spawnGroupRecipe } from './recipes';
export { spawnGroupParser } from './parser';
export { entityParser } from './entity-parser';
export { TerrainPlaceSystem } from './place-system';
export { TerrainSpawnSystem } from './systems';
export { spawnTemplateAtTerrain } from './spawn-template';
export type { PlacementSpec } from './place-types';
export { getPlacementSpecs, setPlacementSpec } from './place-context';
export {
  isNormalWithinSlopeLimit,
  normalFromHeightSampler,
  sampleTerrainSurface,
} from './surface';
export type { TerrainSurfaceSample } from './surface';
export {
  applyChildTemplateProfile,
  getGroupSpawnDefaults,
  isKnownGroupProfileForTests,
  normalizeGroupProfileId,
  optBool,
  optNumber,
  resolveGroupSpawnFields,
} from './profiles';
export type {
  ChildTemplateProfileId,
  GroundAlignMode,
  GroupSpawnDefaults,
  SpawnGroupProfileId,
} from './profiles';
export type {
  SpawnGroupSpec,
  SpawnTemplateRole,
  SpawnTemplateSpec,
} from './types';
