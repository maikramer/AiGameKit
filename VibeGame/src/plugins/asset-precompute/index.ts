export { PrecomputePlugin } from './plugin';
export { PrecomputeColliderSystem, resetPrecomputeForTests } from './systems';
export {
  DEFAULT_PRECOMPUTE_MANIFEST_URL,
  getPrecomputeIndexSync,
  getPrecomputeManifestState,
  loadPrecomputeManifest,
  resolvePrecompute,
} from './manifest';
export type {
  AssetPrecompute,
  PrecomputeColliderSpec,
  PrecomputeIndex,
} from './manifest';
