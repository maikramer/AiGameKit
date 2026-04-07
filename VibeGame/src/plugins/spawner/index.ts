export { SpawnerPending } from './components';
export { SpawnerPlugin } from './plugin';
export { spawnGroupRecipe } from './recipes';
export { spawnGroupParser } from './parser';
export { TerrainSpawnSystem } from './systems';
export {
  isNormalWithinSlopeLimit,
  normalFromHeightSampler,
  sampleTerrainSurface,
} from './surface';
export type { TerrainSurfaceSample } from './surface';
export {
  applyChildTemplateProfile,
  getGroupSpawnDefaults,
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
