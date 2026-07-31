export {
  addEntity,
  createWorld,
  entityExists,
  getAllEntities,
  removeEntity,
  type World as IWorld,
} from 'bitecs';

export { defineQuery, defineQueryLive } from './ecs/query';
export type { Component } from './ecs/types';

export {
  createSnapshot,
  formatSnapshot,
  NULL_ENTITY,
  Parent,
  restoreSnapshot,
  Scene,
  State,
  TIME_CONSTANTS,
  Time,
  type InstantiateOptions,
  type TemplateData,
} from './ecs';
export type {
  Adapter,
  ComponentDefaults,
  ComponentEnums,
  Config,
  CoroutineEntry,
  EntitySnapshot,
  EnumMapping,
  Parser,
  ParserParams,
  Plugin,
  Recipe,
  RestoreResult,
  SequenceSnapshot,
  ShorthandMapping,
  SnapshotOptions,
  System,
  ValidationRule,
  WorldSnapshot,
} from './ecs';
export { Tag, addTag, getTagId, getTagName } from './ecs';
export { Layer, LayerMask } from './ecs';
export {
  cleanupEntityCoroutines,
  CoroutineFixedUpdateSystem,
  CoroutineLateFrameSystem,
  CoroutineRunnerSystem,
  getActiveCoroutines,
  getCoroutine,
  getTotalActiveCoroutineCount,
  startCoroutine,
  stopAllCoroutines,
  stopCoroutine,
  WaitForSeconds,
  WaitForSecondsRealtime,
  WaitForEndOfFrame,
  WaitForFixedUpdate,
  WaitUntil,
  WaitWhile,
} from './ecs';
export type {
  CoroutineYieldValue,
  WaitForSecondsInstruction,
  WaitForSecondsRealtimeInstruction,
  WaitForEndOfFrameInstruction,
  WaitForFixedUpdateInstruction,
  WaitUntilInstruction,
  WaitWhileInstruction,
  YieldInstruction,
} from './ecs';
export {
  addEventListener,
  removeEventListener,
  addEventListenerOnce,
  dispatchEvent,
  removeAllListeners,
} from './ecs';
export { eulerToQuaternion, lerp, quaternionToEuler, slerp } from './math';
export {
  entityRecipe,
  fromEuler,
  ParseContext,
  parseXMLToEntities,
  transformRecipe,
  type EntityCreationResult,
} from './recipes';
export { toCamelCase, toKebabCase } from './utils';
export {
  findElements,
  traverseElements,
  XMLParser,
  XMLValueParser,
  MAX_INCLUDE_DEPTH,
  createFetchIncludeLoader,
  expandIncludes,
  unwrapIncludeFragment,
} from './xml';
export type {
  ParsedElement,
  XMLValue,
  ExpandIncludesOptions,
  IncludeLoader,
} from './xml';

export {
  getRecipeSchema,
  isValidRecipeName,
  safeValidateRecipeAttributes,
  validateHTMLContent,
  validateRecipeAttributes,
  validateXMLContent,
} from './validation';

export type {
  BodyTypeValue,
  Color,
  RecipeAttributes,
  RecipeName,
  Shape,
  ValidationOptions,
  ValidationResult,
  Vector2,
  Vector3,
} from './validation';

export {
  disposeAllRuntimes,
  ensureRuntimePageTeardown,
  registerRuntime,
  releaseRuntimeGpuResources,
  unregisterRuntime,
} from './runtime-manager';

export {
  getLoadingProgress,
  getReadyGates,
  isLoadingEnforced,
  isPhysicsHeld,
  isWorldLoadedLatched,
  isWorldReady,
  registerReadyGate,
  resetLoadingGate,
  setLoadingEnforcement,
} from './loading-gate';
export type { ReadyCheck } from './loading-gate';

export {
  _resetProfilerForTests,
  beginSpan,
  copyProfilerSnapshot,
  disableProfiler,
  downloadProfilerSnapshot,
  enableProfiler,
  endSpan,
  freezeProfiler,
  getProfilerMode,
  getProfilerSnapshot,
  getProfilerTop,
  isProfilerEnabled,
  isProfilerFrozen,
  defineSystem,
  namedSystem,
  recordCustomTiming,
  resetProfiler,
  setProfilerMode,
  toggleProfilerFreeze,
  unfreezeProfiler,
  withSpan,
} from './profiler';
export type {
  ProfilerGroup,
  ProfilerGroupStats,
  ProfilerMode,
  ProfilerSnapshot,
  ProfilerTimingStats,
} from './profiler';
