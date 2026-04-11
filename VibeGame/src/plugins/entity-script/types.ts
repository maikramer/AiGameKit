import type { Component } from 'bitecs';
import type { Object3D } from 'three';

import type { State } from '../../core';

/** Context passed to entity script lifecycle methods (MonoBehaviour-like). */
export interface EntityScriptContext {
  state: State;
  /** Entity id (bitECS). */
  entity: number;
  /** Root Three.js object for GLB loads when available; otherwise `null`. */
  object3d: Object3D | null;
  /** Shortcut for `state.time.deltaTime`. */
  deltaTime: number;
  /** Get a component by registered name on this entity. Returns null if not present. */
  getComponent(name: string): Component | null;
  /** Search this entity then its children (depth-first) for a component. */
  getComponentInChildren(name: string): Component | null;
  /** Search this entity then its ancestors for a component. */
  getComponentInParent(name: string): Component | null;
}

/** Module shape for `import.meta.glob` entries (named exports). */
export interface EntityScriptModule {
  awake?: (ctx: EntityScriptContext) => void;
  onEnable?: (ctx: EntityScriptContext) => void;
  onDisable?: (ctx: EntityScriptContext) => void;
  start?: (ctx: EntityScriptContext) => void | Promise<void>;
  update?: (ctx: EntityScriptContext) => void;
  fixedUpdate?: (ctx: EntityScriptContext) => void;
  lateUpdate?: (ctx: EntityScriptContext) => void;
  onDestroy?: (ctx: EntityScriptContext) => void;
}
