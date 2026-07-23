import { describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import {
  bumpSceneGeneration,
  getSceneGeneration,
} from '../../../src/extras/scene-generation';

describe('scene-generation', () => {
  it('getSceneGeneration retorna 0 para um State novo', () => {
    const state = new State();
    expect(getSceneGeneration(state)).toBe(0);
  });

  it('bumpSceneGeneration retorna a nova geração e atualiza getSceneGeneration', () => {
    const state = new State();

    const next = bumpSceneGeneration(state);

    expect(next).toBe(1);
    expect(getSceneGeneration(state)).toBe(1);
  });

  it('incrementa de forma monotónica a cada bump', () => {
    const state = new State();
    const before = getSceneGeneration(state);

    for (let i = 1; i <= 5; i++) {
      const last = bumpSceneGeneration(state);
      expect(last).toBe(before + i);
    }

    expect(getSceneGeneration(state)).toBe(before + 5);
  });

  it('chamar bump N vezes aumenta a geração em N', () => {
    const state = new State();
    const before = getSceneGeneration(state);

    for (let i = 0; i < 10; i++) bumpSceneGeneration(state);

    expect(getSceneGeneration(state)).toBe(before + 10);
  });

  it('isola contadores entre States distintos (WeakMap por State)', () => {
    const a = new State();
    const b = new State();

    bumpSceneGeneration(a);
    bumpSceneGeneration(a);

    expect(getSceneGeneration(a)).toBe(2);
    expect(getSceneGeneration(b)).toBe(0);

    bumpSceneGeneration(b);

    expect(getSceneGeneration(a)).toBe(2);
    expect(getSceneGeneration(b)).toBe(1);
  });
});
