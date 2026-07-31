import { afterEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import type { GltfAnimator } from '../../../src/extras/gltf-animator';
import {
  getAnimator,
  GltfAnimationUpdateSystem,
  registerAnimator,
  unregisterAnimator,
} from '../../../src/plugins/gltf-anim/systems';

function makeMockAnimator(): {
  animator: GltfAnimator;
  isDisposed: () => boolean;
} {
  let disposed = false;
  return {
    animator: {
      dispose: () => {
        disposed = true;
      },
    } as unknown as GltfAnimator,
    isDisposed: () => disposed,
  };
}

describe('gltf-anim registerAnimator (per-State)', () => {
  const state = new State();
  const owned: number[] = [];

  afterEach(() => {
    for (const idx of owned.splice(0)) unregisterAnimator(state, idx);
  });

  it('registerAnimator adiciona uma entrada recuperável por get', () => {
    const { animator } = makeMockAnimator();

    const idx = registerAnimator(state, animator);
    owned.push(idx);

    expect(idx).toBeGreaterThanOrEqual(1);
    expect(getAnimator(state, idx)).toBe(animator);
  });

  it('aloca índices crescentes e consecutivos a cada registro', () => {
    const a = makeMockAnimator().animator;
    const b = makeMockAnimator().animator;
    const c = makeMockAnimator().animator;

    const i1 = registerAnimator(state, a);
    const i2 = registerAnimator(state, b);
    const i3 = registerAnimator(state, c);
    owned.push(i1, i2, i3);

    expect(i2).toBe(i1 + 1);
    expect(i3).toBe(i2 + 1);
    expect(getAnimator(state, i1)).toBe(a);
    expect(getAnimator(state, i2)).toBe(b);
    expect(getAnimator(state, i3)).toBe(c);
  });

  it('reserva o índice 0 como sentinela "sem animator" (nunca usado)', () => {
    expect(getAnimator(state, 0)).toBeUndefined();
  });

  it('lookup de índice desconhecido retorna undefined', () => {
    expect(getAnimator(state, 999999)).toBeUndefined();
  });

  it('registrar a mesma instância duas vezes cria duas entradas distintas (não sobrescreve, não lança)', () => {
    const { animator } = makeMockAnimator();

    const idx1 = registerAnimator(state, animator);
    const idx2 = registerAnimator(state, animator);
    owned.push(idx1, idx2);

    expect(idx1).not.toBe(idx2);
    expect(getAnimator(state, idx1)).toBe(animator);
    expect(getAnimator(state, idx2)).toBe(animator);
  });
});

describe('gltf-anim unregisterAnimator', () => {
  const state = new State();
  const owned: number[] = [];

  afterEach(() => {
    for (const idx of owned.splice(0)) unregisterAnimator(state, idx);
  });

  it('remove a entrada e chama dispose no animator', () => {
    const { animator, isDisposed } = makeMockAnimator();
    const idx = registerAnimator(state, animator);
    owned.push(idx);

    expect(isDisposed()).toBe(false);
    expect(getAnimator(state, idx)).toBeDefined();

    unregisterAnimator(state, idx);

    expect(isDisposed()).toBe(true);
    expect(getAnimator(state, idx)).toBeUndefined();
  });

  it('é no-op para um índice desconhecido (não lança)', () => {
    expect(() => unregisterAnimator(state, 888888)).not.toThrow();
  });
});

describe('gltf-anim GltfAnimationUpdateSystem (shape estática)', () => {
  it('é exportado como objeto System com group "draw" e funções update/dispose', () => {
    expect(GltfAnimationUpdateSystem).toBeDefined();
    expect(typeof GltfAnimationUpdateSystem).toBe('object');
    expect(GltfAnimationUpdateSystem.group).toBe('draw');
    expect(typeof GltfAnimationUpdateSystem.update).toBe('function');
    expect(typeof GltfAnimationUpdateSystem.dispose).toBe('function');
  });
});
