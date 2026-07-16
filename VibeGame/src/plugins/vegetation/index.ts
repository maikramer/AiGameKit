export { VegetationPlugin, vegetationRecipe } from './plugin';
export { Vegetation } from './components';
export { parseVegetationMeshes, toBoolAttr } from './parse-meshes';
export {
  classifyVegetationRole,
  parseMeshRoleOverrides,
  type VegetationRole,
} from './roles';
export {
  sizeTierFromHeight,
  sizeTierFromFilename,
  resolveSizeTier,
  type VegetationSizeTier,
} from './size-tier';
export { buildVegetationPlan, type VegetationPatchPlan } from './plan';
export {
  VegetationWindSystem,
  registerVegetationWindUrl,
  getVegetationWindUrls,
  maybePatchVegetationWindMaterial,
  _resetVegetationWindUrls,
} from './wind';
export { VegetationPlannerSystem } from './planner-system';
export {
  getVegetationHubs,
  setVegetationHubs,
  generateVegetationHubs,
  _resetVegetationHubs,
} from './hubs';
export {
  getVegetationPatch,
  setVegetationPatch,
  _resetVegetationPatches,
} from './patch-context';
