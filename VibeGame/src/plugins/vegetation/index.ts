export { VegetationPlugin, vegetationRecipe } from './plugin';
export { Vegetation } from './components';
export { parseVegetationMeshes, toBoolAttr } from './parse-meshes';
export {
  VegetationWindSystem,
  registerVegetationWindUrl,
  getVegetationWindUrls,
  maybePatchVegetationWindMaterial,
  _resetVegetationWindUrls,
} from './wind';
