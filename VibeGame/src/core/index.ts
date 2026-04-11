export {
  addComponent,
  addEntity,
  createWorld,
  defineComponent,
  defineQuery,
  getAllEntities,
  hasComponent,
  removeComponent,
  removeEntity,
  Types,
  type Component,
  type IWorld,
} from 'bitecs';

export {
  createSnapshot,
  formatSnapshot,
  NULL_ENTITY,
  Parent,
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
  GameTime,
  Parser,
  ParserParams,
  Plugin,
  Recipe,
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
} from './xml';
export type { ParsedElement, XMLValue } from './xml';

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
  registerRuntime,
  unregisterRuntime,
} from './runtime-manager';
