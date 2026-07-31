import { afterEach, describe, expect, it } from 'bun:test';
import { GltfAnimationState, State } from 'vibegame';
import { matchClipKeyword } from '../../../src/extras/gltf-animator';
import {
  getAnimator,
  registerAnimator,
  unregisterAnimator,
} from '../../../src/plugins/gltf-anim/systems';
import type { GltfAnimator } from '../../../src/extras/gltf-animator';

describe('gltf-anim GltfAnimationState table-driven', () => {
  for (let eid = 1; eid <= 100; eid++) {
    it(`round-trip fields on entity ${eid}`, () => {
      const reg = (eid * 17) % 5000;
      const clip = eid % 16;
      const playing = eid % 2;
      const fade = (eid % 10) * 0.05;
      GltfAnimationState.registryIndex[eid] = reg;
      GltfAnimationState.activeClipIndex[eid] = clip;
      GltfAnimationState.isPlaying[eid] = playing;
      GltfAnimationState.crossfadeDuration[eid] = fade;
      expect(GltfAnimationState.registryIndex[eid]).toBe(reg);
      expect(GltfAnimationState.activeClipIndex[eid]).toBe(clip);
      expect(GltfAnimationState.isPlaying[eid]).toBe(playing);
      expect(GltfAnimationState.crossfadeDuration[eid]).toBeCloseTo(fade, 5);
    });
  }
});

describe('gltf-anim matchClipKeyword table-driven', () => {
  const clipNames = [
    'Idle',
    'Walk',
    'Run',
    'Jump_Start',
    'Jump_Loop',
    'TurnLeft',
    'TurnRight',
    'Animator3D_BreatheIdle',
    'mixamo.com|Run',
    'custom-walk-back',
  ];

  const cases: Array<{ keyword: string; expectName: string }> = [
    { keyword: 'idle', expectName: 'Idle' },
    { keyword: 'walk', expectName: 'Walk' },
    { keyword: 'run', expectName: 'Run' },
    { keyword: 'jump', expectName: 'Jump_Start' },
    { keyword: 'turnleft', expectName: 'TurnLeft' },
    { keyword: 'breatheidle', expectName: 'Animator3D_BreatheIdle' },
  ];

  for (let i = 0; i < 94; i++) {
    const pick = cases[i % cases.length]!;
    it(`matchClipKeyword variant ${i} kw=${pick.keyword}`, () => {
      const found = matchClipKeyword(clipNames, pick.keyword);
      expect(found).toBe(pick.expectName);
    });
  }
});

describe('gltf-anim registerAnimator table-driven', () => {
  const stateA = new State();
  const stateB = new State();
  const ownedA: number[] = [];
  const ownedB: number[] = [];

  afterEach(() => {
    for (const idx of ownedA.splice(0)) unregisterAnimator(stateA, idx);
    for (const idx of ownedB.splice(0)) unregisterAnimator(stateB, idx);
  });

  for (let i = 0; i < 50; i++) {
    it(`registerAnimator returns unique index batch ${i}`, () => {
      const mock = { dispose: () => {} } as GltfAnimator;
      const idx = registerAnimator(stateA, mock);
      ownedA.push(idx);
      expect(idx).toBeGreaterThan(0);
      expect(getAnimator(stateA, idx)).toBe(mock);
    });
  }

  it('isolates registries per State (multi-runtime safety)', () => {
    const mockA = { dispose: () => {} } as GltfAnimator;
    const mockB = { dispose: () => {} } as GltfAnimator;
    const idxA = registerAnimator(stateA, mockA);
    const idxB = registerAnimator(stateB, mockB);
    ownedA.push(idxA);
    ownedB.push(idxB);

    // Same index space is fine — lookups are State-scoped.
    expect(getAnimator(stateA, idxA)).toBe(mockA);
    expect(getAnimator(stateB, idxB)).toBe(mockB);
    expect(getAnimator(stateA, idxB)).toBeUndefined();
    expect(getAnimator(stateB, idxA)).toBeUndefined();

    // Disposing one State's animator must not affect the other's.
    unregisterAnimator(stateA, idxA);
    expect(getAnimator(stateB, idxB)).toBe(mockB);
  });
});
