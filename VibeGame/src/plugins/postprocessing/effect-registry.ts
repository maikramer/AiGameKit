import type { Camera, Scene, WebGLRenderer } from 'three';
import type { Pass } from 'postprocessing';
import type { Component } from '../../core';

export type EffectComponentState = Record<
  string,
  Float32Array | Uint8Array | Uint32Array
>;

export interface EffectDefinition {
  readonly key: string;
  readonly component?: Component;
  create(
    state: EffectComponentState,
    entity: number,
    renderer: WebGLRenderer,
    scene: Scene,
    camera: Camera
  ): Pass | null;
  update?(state: EffectComponentState, entity: number, pass: Pass): void;
  readonly position?: 'first' | 'last';
  /** Sort key within the same `position` bucket (lower runs earlier;
   * default 0, ties keep registration order). Lets a scene-re-rendering pass
   * like SSR run before the AA passes that share `position: 'first'`. */
  readonly order?: number;
}

const effects: EffectDefinition[] = [];

export function registerEffect(definition: EffectDefinition): void {
  const idx = effects.findIndex((d) => d.key === definition.key);
  if (idx !== -1) effects[idx] = definition;
  else effects.push(definition);
}

export function getEffectDefinitions(): readonly EffectDefinition[] {
  return effects;
}

export function unregisterEffect(key: string): boolean {
  const idx = effects.findIndex((d) => d.key === key);
  if (idx !== -1) {
    effects.splice(idx, 1);
    return true;
  }
  return false;
}
