export { MonoBehaviour } from './components';
export {
  getEntityScriptsGlob,
  registerEntityScripts,
  resolveEntityScriptGlobKey,
} from './context';
export { coerceMonoBehaviourModule } from './context';
/** @deprecated Use coerceMonoBehaviourModule. */
export { coerceMonoBehaviourModule as coerceEntityScriptModule } from './context';
export {
  getCachedMonoBehaviourModule,
  setCachedMonoBehaviourModule,
} from './context';
/** @deprecated Use getCachedMonoBehaviourModule. */
export { getCachedMonoBehaviourModule as getCachedEntityScriptModule } from './context';
/** @deprecated Use setCachedMonoBehaviourModule. */
export { setCachedMonoBehaviourModule as setCachedEntityScriptModule } from './context';
export { getOrLoadMonoBehaviourModule } from './context';
/** @deprecated Use getOrLoadMonoBehaviourModule. */
export { getOrLoadMonoBehaviourModule as getOrLoadEntityScriptModule } from './context';
export { EntityScriptPlugin } from './plugin';
export {
  EntityScriptCollisionBridgeSystem,
  EntityScriptSystem,
} from './system';
export type {
  CollisionOther,
  EntityScriptContext,
  EntityScriptModule,
  MonoBehaviourContext,
  MonoBehaviourModule,
  GameObjectProxy,
} from './types';
