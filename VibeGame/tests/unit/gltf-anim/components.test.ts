import { describe, expect, it } from 'bun:test';
import { GltfAnimationState } from 'vibegame';

const MAX_ENTITIES = 100000;

describe('GltfAnimationState component', () => {
  it('expõe registryIndex como Uint32Array com tamanho MAX_ENTITIES', () => {
    expect(GltfAnimationState.registryIndex).toBeInstanceOf(Uint32Array);
    expect(GltfAnimationState.registryIndex.length).toBe(MAX_ENTITIES);
  });

  it('expõe activeClipIndex como Uint8Array com tamanho MAX_ENTITIES', () => {
    expect(GltfAnimationState.activeClipIndex).toBeInstanceOf(Uint8Array);
    expect(GltfAnimationState.activeClipIndex.length).toBe(MAX_ENTITIES);
  });

  it('expõe isPlaying como Uint8Array com tamanho MAX_ENTITIES', () => {
    expect(GltfAnimationState.isPlaying).toBeInstanceOf(Uint8Array);
    expect(GltfAnimationState.isPlaying.length).toBe(MAX_ENTITIES);
  });

  it('expõe crossfadeDuration como Float32Array com tamanho MAX_ENTITIES', () => {
    expect(GltfAnimationState.crossfadeDuration).toBeInstanceOf(Float32Array);
    expect(GltfAnimationState.crossfadeDuration.length).toBe(MAX_ENTITIES);
  });

  it('expõe exatamente os 4 campos esperados (nem mais nem menos)', () => {
    expect(Object.keys(GltfAnimationState).sort()).toEqual([
      'activeClipIndex',
      'crossfadeDuration',
      'isPlaying',
      'registryIndex',
    ]);
  });

  it('inicia com zeros em todos os campos', () => {
    expect(GltfAnimationState.registryIndex[0]).toBe(0);
    expect(GltfAnimationState.activeClipIndex[0]).toBe(0);
    expect(GltfAnimationState.isPlaying[0]).toBe(0);
    expect(GltfAnimationState.crossfadeDuration[0]).toBe(0);
  });

  it('faz round-trip de escrita/leitura em cada campo tipado', () => {
    const eid = 7;

    GltfAnimationState.registryIndex[eid] = 42;
    GltfAnimationState.activeClipIndex[eid] = 3;
    GltfAnimationState.isPlaying[eid] = 1;
    GltfAnimationState.crossfadeDuration[eid] = 0.3;

    expect(GltfAnimationState.registryIndex[eid]).toBe(42);
    expect(GltfAnimationState.activeClipIndex[eid]).toBe(3);
    expect(GltfAnimationState.isPlaying[eid]).toBe(1);
    expect(GltfAnimationState.crossfadeDuration[eid]).toBeCloseTo(0.3);

    GltfAnimationState.registryIndex[eid] = 0;
    GltfAnimationState.activeClipIndex[eid] = 0;
    GltfAnimationState.isPlaying[eid] = 0;
    GltfAnimationState.crossfadeDuration[eid] = 0;
  });
});
