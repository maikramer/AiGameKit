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
  /** Start a coroutine (generator) on this entity. Returns coroutine ID. */
  StartCoroutine(genOrFn: Generator | (() => Generator)): number;
  /** Stop a running coroutine by ID. */
  StopCoroutine(coroutineId: number): void;
  /** Stop all coroutines on this entity. */
  StopAllCoroutines(): void;
}

/** Other entity involved in a collision/trigger event. */
export interface CollisionOther {
  entity: number;
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
  onCollisionEnter?: (ctx: EntityScriptContext, other: CollisionOther) => void;
  onCollisionStay?: (ctx: EntityScriptContext, other: CollisionOther) => void;
  onCollisionExit?: (ctx: EntityScriptContext, other: CollisionOther) => void;
  onTriggerEnter?: (ctx: EntityScriptContext, other: CollisionOther) => void;
  onTriggerStay?: (ctx: EntityScriptContext, other: CollisionOther) => void;
  onTriggerExit?: (ctx: EntityScriptContext, other: CollisionOther) => void;
}
