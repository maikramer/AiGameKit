import type { State } from '../core';

// Per-State scene generation. GLTF loaders capture getSceneGeneration() when a
// load starts and bail in the .then handler if it changed by resolve time, so a
// group never attaches to a scene that was swapped/reloaded or a State that was
// disposed mid-load. bumpSceneGeneration() is called on scene init/reload and
// plugin teardown. WeakMap -> entry reclaimed with the State.
const generationByState = new WeakMap<State, number>();

export function getSceneGeneration(state: State): number {
  return generationByState.get(state) ?? 0;
}

export function bumpSceneGeneration(state: State): number {
  const next = (generationByState.get(state) ?? 0) + 1;
  generationByState.set(state, next);
  return next;
}
