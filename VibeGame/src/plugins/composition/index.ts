export { CompositionPlugin } from './plugin';
export { CompositionPending } from './components';
export { compositionRecipe } from './recipes';
export { compositionParser } from './parser';
export {
  CompositionColliderSystem,
  CompositionSetupSystem,
  CompositionSyncSystem,
} from './systems';
export {
  forEachCompositionGroup,
  getCompositionGroup,
  registerCompositionGroup,
} from './group-registry';
export {
  buildPrimitiveMesh,
  getCompositionData,
  isPrimitiveTag,
  type ColliderMode,
  type CompositionData,
  type PrimitiveKind,
  type PrimitiveSpec,
} from './primitives';
