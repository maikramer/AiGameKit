import type { Object3D } from 'three';

import type { State } from '../../core';

/** Context passed to entity script `setup` / `update` (MonoBehaviour-like). */
export interface EntityScriptContext {
  state: State;
  /** Entity id (bitECS). */
  entity: number;
  /** Root Three.js object for GLB loads when available; otherwise `null`. */
  object3d: Object3D | null;
  /** Shortcut for `state.time.deltaTime`. */
  deltaTime: number;
}

/** Module shape for `import.meta.glob` entries (named exports). */
export interface EntityScriptModule {
  awake?: (ctx: EntityScriptContext) => void;
  onEnable?: (ctx: EntityScriptContext) => void;
  onDisable?: (ctx: EntityScriptContext) => void;
  setup?: (ctx: EntityScriptContext) => void | Promise<void>;
  update?: (ctx: EntityScriptContext) => void;
  onDestroy?: (ctx: EntityScriptContext) => void;
}
