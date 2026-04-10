import type { Effect } from 'postprocessing';
import type { Component } from 'bitecs';
import type { State } from '../../core';

/**
 * Defines how a postprocessing effect integrates with the ECS system.
 */
export interface EffectDefinition {
  /** Unique effect key (e.g. 'bloom', 'smaa') */
  readonly key: string;

  /** The bitecs component that toggles this effect on an entity */
  readonly component: Component;

  /**
   * Create the Effect instance for a given entity.
   * Called once when the component is first detected.
   */
  create(state: State, entity: number): Effect;

  /**
   * Update the effect's properties each frame.
   * Return `true` if the effect pass needs rebuilding (effect was replaced).
   */
  update?(state: State, entity: number, effect: Effect): boolean | void;

  /**
   * Position hint for the effect pass.
   * 'first' = runs before other effects (e.g. SMAA).
   * 'last' = runs after other effects (e.g. tonemapping).
   * undefined = runs in order of registration.
   */
  readonly position?: 'first' | 'last';

  /**
   * Whether this effect requires convolution (screen-space effect).
   * Convolution effects need special handling in the effect pass.
   */
  readonly convolution?: boolean;
}

const registry: EffectDefinition[] = [];

/**
 * Register a postprocessing effect definition.
 */
export function registerEffect(definition: EffectDefinition): void {
  const existing = registry.findIndex((d) => d.key === definition.key);
  if (existing >= 0) {
    registry[existing] = definition;
  } else {
    registry.push(definition);
  }
}

/**
 * Get all registered effect definitions.
 */
export function getEffectDefinitions(): readonly EffectDefinition[] {
  return registry;
}

/**
 * Unregister a postprocessing effect by key.
 */
export function unregisterEffect(key: string): boolean {
  const idx = registry.findIndex((d) => d.key === key);
  if (idx >= 0) {
    registry.splice(idx, 1);
    return true;
  }
  return false;
}
