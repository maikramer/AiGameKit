import { describe, expect, it } from 'bun:test';
import type { GltfAnimator } from 'vibegame';
import { NpcIdleAnimator } from '../../../examples/simple-rpg/src/game/npc-anims';

/**
 * Gesture pools are authored against a clip library, but each NPC GLB pack
 * ships a subset (the guard pack has no `foldarms`/`lean`). The animator must
 * pick variety only from clips that exist — never request a missing clip
 * (each miss warns via GltfAnimator).
 */
function makeAnimator(clips: string[]): GltfAnimator & { played: string[] } {
  const played: string[] = [];
  return {
    clipNames: clips,
    played,
    play(name: string) {
      if (!clips.includes(name)) return null;
      played.push(name);
      return {} as ReturnType<GltfAnimator['play']>;
    },
    playOverride(name: string) {
      if (!clips.includes(name)) return null;
      played.push(name);
      return {} as ReturnType<GltfAnimator['playOverride']>;
    },
  } as unknown as GltfAnimator & { played: string[] };
}

// The actual clip set shipped by the npc_guard pack.
const GUARD_CLIPS = ['idle', 'lantern', 'no', 'talk', 'walk', 'yes'];

describe('NpcIdleAnimator gesture filtering', () => {
  it('only plays gestures the pack actually ships', () => {
    const animator = makeAnimator(GUARD_CLIPS);
    const npc = new NpcIdleAnimator({
      idle: 'idle',
      gestures: ['talk', 'lean', 'foldarms'],
      minInterval: 0,
      maxInterval: 0,
    });

    npc.start(animator);
    for (let i = 0; i < 200; i++) {
      npc.update(0.016, animator);
    }

    expect(animator.played.length).toBeGreaterThan(0);
    // 'lean' and 'foldarms' do not exist in this pack — they must never be
    // requested; the only gesture that fires is 'talk'.
    for (const clip of animator.played) {
      expect(GUARD_CLIPS).toContain(clip);
    }
    expect(animator.played).toContain('talk');
  });

  it('stays on idle when no gesture clip exists in the pack', () => {
    const animator = makeAnimator(['idle', 'walk']);
    const npc = new NpcIdleAnimator({
      idle: 'idle',
      gestures: ['foldarms', 'lean'],
      minInterval: 0,
      maxInterval: 0,
    });

    npc.start(animator);
    for (let i = 0; i < 100; i++) {
      npc.update(0.016, animator);
    }

    expect(animator.played).toEqual(['idle']);
  });

  it('react() ignores reaction clips missing from the pack', () => {
    const animator = makeAnimator(GUARD_CLIPS);
    const npc = new NpcIdleAnimator({ idle: 'idle', gestures: ['talk'] });

    npc.start(animator);
    npc.react(animator, 'nod');
    expect(animator.played).toEqual(['idle']);

    npc.react(animator, 'yes');
    expect(animator.played).toEqual(['idle', 'yes']);
  });

  it('picks from the full pool when every gesture exists (merchant pack)', () => {
    const merchantClips = [
      'call',
      'eat',
      'foldarms',
      'idle',
      'no',
      'talk',
      'walk',
      'yes',
    ];
    const animator = makeAnimator(merchantClips);
    const npc = new NpcIdleAnimator({
      idle: 'idle',
      gestures: ['call', 'talk', 'foldarms'],
      minInterval: 0,
      maxInterval: 0,
    });

    npc.start(animator);
    const seen = new Set<string>();
    for (let i = 0; i < 300; i++) {
      npc.update(0.016, animator);
      seen.add(animator.played[animator.played.length - 1]!);
    }

    expect(seen.has('call')).toBe(true);
    expect(seen.has('talk')).toBe(true);
    expect(seen.has('foldarms')).toBe(true);
  });
});
