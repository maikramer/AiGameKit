import type { State } from './state';

let stateRef: State | null = null;

function requireState(): State {
  if (!stateRef) {
    throw new Error('[VibeGame] Time not initialized. Call Time.init(state) first.');
  }
  return stateRef;
}

export const Time = {
  init(state: State): void {
    stateRef = state;
  },

  get timeScale(): number {
    return requireState().time.timeScale;
  },
  set timeScale(value: number) {
    requireState().time.timeScale = value;
  },

  get deltaTime(): number {
    return requireState().time.deltaTime;
  },

  get unscaledDeltaTime(): number {
    return requireState().time.unscaledDeltaTime;
  },

  get frameCount(): number {
    return requireState().time.frameCount;
  },

  get fixedTime(): number {
    return requireState().time.fixedTime;
  },

  get fixedDeltaTime(): number {
    return requireState().time.fixedDeltaTime;
  },

  get realtimeSinceStartup(): number {
    return requireState().time.realtimeSinceStartup;
  },

  get time(): number {
    return requireState().time.realtimeSinceStartup;
  },
};
