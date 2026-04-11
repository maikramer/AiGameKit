import type { Component } from 'bitecs';
import type { Object3D } from 'three';

import type { State } from '../../core';

/**
 * Unity-style proxy for the entity's "GameObject".
 * Provides quick access to name, tag, layer, and transform data.
 */
export interface GameObjectProxy {
  /** The bitECS entity id. */
  readonly id: number;
  /** Entity display name (from Name component if present). */
  readonly name: string;
  /** Tag string (from Tag component). */
  readonly tag: string;
  /** Layer index (from Layer component). */
  readonly layer: number;
}

/**
 * Context passed to MonoBehaviour lifecycle methods.
 * Provides Unity-style helpers alongside the raw ECS state.
 */
export interface MonoBehaviourContext {
  /** Core ECS state. */
  state: State;
  /** Entity id (bitECS). */
  entity: number;
  /** Root Three.js object for GLB loads when available; otherwise `null`. */
  object3d: Object3D | null;
  /** Shortcut for `state.time.deltaTime`. */
  deltaTime: number;
  /** Unity-style proxy for this entity's "GameObject". */
  gameObject: GameObjectProxy;
  /** Shortcut for Transform component data (position x/y/z). */
  transform: { readonly positionX: number; readonly positionY: number; readonly positionZ: number; readonly rotationX: number; readonly rotationY: number; readonly rotationZ: number; readonly scaleX: number; readonly scaleY: number; readonly scaleZ: number };
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

/** @deprecated Use MonoBehaviourContext. Alias kept for backward compatibility. */
export type EntityScriptContext = MonoBehaviourContext;

/** Other entity involved in a collision/trigger event. */
export interface CollisionOther {
  entity: number;
}

/** Module shape for `import.meta.glob` entries (named exports). */
export interface MonoBehaviourModule {
  awake?: (ctx: MonoBehaviourContext) => void;
  onEnable?: (ctx: MonoBehaviourContext) => void;
  onDisable?: (ctx: MonoBehaviourContext) => void;
  start?: (ctx: MonoBehaviourContext) => void | Promise<void>;
  update?: (ctx: MonoBehaviourContext) => void;
  fixedUpdate?: (ctx: MonoBehaviourContext) => void;
  lateUpdate?: (ctx: MonoBehaviourContext) => void;
  onDestroy?: (ctx: MonoBehaviourContext) => void;
  onCollisionEnter?: (ctx: MonoBehaviourContext, other: CollisionOther) => void;
  onCollisionStay?: (ctx: MonoBehaviourContext, other: CollisionOther) => void;
  onCollisionExit?: (ctx: MonoBehaviourContext, other: CollisionOther) => void;
  onTriggerEnter?: (ctx: MonoBehaviourContext, other: CollisionOther) => void;
  onTriggerStay?: (ctx: MonoBehaviourContext, other: CollisionOther) => void;
  onTriggerExit?: (ctx: MonoBehaviourContext, other: CollisionOther) => void;
}

/** @deprecated Use MonoBehaviourModule. Alias kept for backward compatibility. */
export type EntityScriptModule = MonoBehaviourModule;
