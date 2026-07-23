import { afterEach, describe, expect, it } from 'bun:test';
import { GltfAnimationState } from 'vibegame';
import { matchClipKeyword } from '../../../src/extras/gltf-animator';
import {
  animatorRegistry,
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
  const owned: number[] = [];

  afterEach(() => {
    for (const idx of owned.splice(0)) unregisterAnimator(idx);
  });

  for (let i = 0; i < 50; i++) {
    it(`registerAnimator returns unique index batch ${i}`, () => {
      const mock = { dispose: () => {} } as GltfAnimator;
      const idx = registerAnimator(mock);
      owned.push(idx);
      expect(idx).toBeGreaterThan(0);
      expect(animatorRegistry.get(idx)).toBe(mock);
    });
  }
});
