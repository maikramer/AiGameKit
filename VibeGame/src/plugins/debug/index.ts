export { DebugPlugin, DebugOverlaySystem } from './plugin';
export type { VibeGameDebugBridge } from './plugin';
export {
  getDebugRegistry,
  getDebugRegistryHandle,
  registerDebugAction,
  registerDebugVar,
} from './registry';
export type {
  DebugActionEntry,
  DebugRegistry,
  DebugRegistryHandle,
  DebugVarEntry,
  RegisterDebugActionOptions,
} from './registry';
export {
  PostFxToggleSystem,
  postFxToggleRecipe,
  parsePostFxBindings,
  DEFAULT_POSTFX_BINDINGS,
  getPostFxToggleState,
  setPostFxBindings,
  applyPostFxToggle,
} from './postfx-toggle';
export type {
  PostFxEffectField,
  PostFxKeyBindings,
  IsKeyDownFn,
  PostFxToggleOptions,
  PostFxToggleResult,
} from './postfx-toggle';
