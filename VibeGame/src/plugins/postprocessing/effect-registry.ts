import type { Camera, Scene, WebGLRenderer } from 'three';
import type { Effect } from 'postprocessing';
import type { Component } from '../../core';

export interface EffectDefinition {
  readonly key: string;
  readonly component?: Component;
  create(
    state: Record<string, Float32Array | Uint8Array>,
    entity: number,
    renderer: WebGLRenderer,
    scene: Scene,
    camera: Camera
  ): Effect | null;
  update?(
    state: Record<string, Float32Array | Uint8Array>,
    entity: number,
    effect: Effect
  ): void;
  readonly position?: 'first' | 'last';
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
