import { afterEach, describe, expect, it } from 'bun:test';
import type { GltfAnimator } from '../../../src/extras/gltf-animator';
import {
  animatorRegistry,
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

describe('gltf-anim animatorRegistry + registerAnimator', () => {
  const owned: number[] = [];

  afterEach(() => {
    for (const idx of owned.splice(0)) unregisterAnimator(idx);
  });

  it('inicia vazio (nenhum animator registrado)', () => {
    expect(animatorRegistry.size).toBe(0);
  });

  it('registerAnimator adiciona uma entrada recuperável por get', () => {
    const { animator } = makeMockAnimator();
    const sizeBefore = animatorRegistry.size;

    const idx = registerAnimator(animator);
    owned.push(idx);

    expect(idx).toBeGreaterThanOrEqual(1);
    expect(animatorRegistry.size).toBe(sizeBefore + 1);
    expect(animatorRegistry.get(idx)).toBe(animator);
    expect(animatorRegistry.has(idx)).toBe(true);
  });

  it('aloca índices crescentes e consecutivos a cada registro', () => {
    const a = makeMockAnimator().animator;
    const b = makeMockAnimator().animator;
    const c = makeMockAnimator().animator;

    const i1 = registerAnimator(a);
    const i2 = registerAnimator(b);
    const i3 = registerAnimator(c);
    owned.push(i1, i2, i3);

    expect(i2).toBe(i1 + 1);
    expect(i3).toBe(i2 + 1);
    expect(animatorRegistry.get(i1)).toBe(a);
    expect(animatorRegistry.get(i2)).toBe(b);
    expect(animatorRegistry.get(i3)).toBe(c);
  });

  it('reserva o índice 0 como sentinela "sem animator" (nunca usado)', () => {
    expect(animatorRegistry.has(0)).toBe(false);
    expect(animatorRegistry.get(0)).toBeUndefined();
  });

  it('lookup de índice desconhecido retorna undefined', () => {
    expect(animatorRegistry.get(999999)).toBeUndefined();
    expect(animatorRegistry.has(999999)).toBe(false);
  });

  it('registrar a mesma instância duas vezes cria duas entradas distintas (não sobrescreve, não lança)', () => {
    const { animator } = makeMockAnimator();
    const sizeBefore = animatorRegistry.size;

    const idx1 = registerAnimator(animator);
    const idx2 = registerAnimator(animator);
    owned.push(idx1, idx2);

    expect(idx1).not.toBe(idx2);
    expect(animatorRegistry.size).toBe(sizeBefore + 2);
    expect(animatorRegistry.get(idx1)).toBe(animator);
    expect(animatorRegistry.get(idx2)).toBe(animator);
  });
});

describe('gltf-anim unregisterAnimator', () => {
  const owned: number[] = [];

  afterEach(() => {
    for (const idx of owned.splice(0)) unregisterAnimator(idx);
  });

  it('remove a entrada e chama dispose no animator', () => {
    const { animator, isDisposed } = makeMockAnimator();
    const idx = registerAnimator(animator);
    owned.push(idx);

    expect(isDisposed()).toBe(false);
    expect(animatorRegistry.has(idx)).toBe(true);

    unregisterAnimator(idx);

    expect(isDisposed()).toBe(true);
    expect(animatorRegistry.has(idx)).toBe(false);
    expect(animatorRegistry.get(idx)).toBeUndefined();
  });

  it('é no-op para um índice desconhecido (não lança)', () => {
    expect(() => unregisterAnimator(888888)).not.toThrow();
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
