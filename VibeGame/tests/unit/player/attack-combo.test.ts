import { describe, expect, it } from 'bun:test';
import {
  advancePlayerAttackCombo,
  getPlayerAttackClip,
  setPlayerAttackClip,
} from 'aigamekit-vibegame';

const POOL: string[] = ['sword', 'sworda', 'swordb', 'swordc'];

function reset(): void {
  setPlayerAttackClip(null);
}

describe('player attack combo pools', () => {
  it('cycle mode walks the pool in order', () => {
    reset();
    setPlayerAttackClip([...POOL]);
    expect(getPlayerAttackClip()).toBe('sword');
    advancePlayerAttackCombo(true);
    expect(getPlayerAttackClip()).toBe('sworda');
    advancePlayerAttackCombo(true);
    advancePlayerAttackCombo(true);
    expect(getPlayerAttackClip()).toBe('swordc');
    advancePlayerAttackCombo(true);
    expect(getPlayerAttackClip()).toBe('sword'); // wraps
  });

  it('re-registering the same pool reference does not reset the combo (per-frame set is a no-op)', () => {
    reset();
    // Same array REFERENCE — games pass a stable pool object every frame.
    // A freshly built array is treated as a new intent and resets (below).
    const pool = [...POOL];
    setPlayerAttackClip(pool);
    advancePlayerAttackCombo(true); // now 'sworda'
    setPlayerAttackClip(pool);
    expect(getPlayerAttackClip()).toBe('sworda');
  });

  it('a NEW pool reference resets the combo position', () => {
    reset();
    setPlayerAttackClip([...POOL]);
    advancePlayerAttackCombo(true);
    setPlayerAttackClip([...POOL]); // different array → new intent
    expect(getPlayerAttackClip()).toBe('sword');
  });

  it('random mode never repeats the previous entry', () => {
    reset();
    setPlayerAttackClip([...POOL], { mode: 'random' });
    let last = getPlayerAttackClip()!;
    expect(POOL).toContain(last);
    for (let i = 0; i < 40; i++) {
      advancePlayerAttackCombo(true);
      const now = getPlayerAttackClip()!;
      expect(POOL).toContain(now);
      expect(now).not.toBe(last);
      last = now;
    }
  });

  it('alternating mode swaps the mirror side every strike with a varied base', () => {
    reset();
    setPlayerAttackClip([...POOL], { mode: 'alternating' });
    const bases: string[] = [];
    const sides: boolean[] = [];
    for (let i = 0; i < 30; i++) {
      const kw = getPlayerAttackClip()!;
      expect(kw).toBeTruthy();
      const mirrored = kw.endsWith('_m');
      const base = mirrored ? kw.slice(0, -2) : kw;
      expect(POOL).toContain(base);
      sides.push(mirrored);
      bases.push(base);
      advancePlayerAttackCombo(true);
    }
    // Side strictly alternates: L,R,L,R… (the readable left↔right chain).
    for (let i = 1; i < sides.length; i++) {
      expect(sides[i]).not.toBe(sides[i - 1]);
    }
    // First strike is the un-mirrored base.
    expect(sides[0]).toBe(false);
    // Bases vary across the chain (not stuck on one move).
    expect(new Set(bases).size).toBeGreaterThan(1);
  });

  it('a non-advanced strike (fallback swing) does not burn a combo step', () => {
    reset();
    setPlayerAttackClip([...POOL]);
    advancePlayerAttackCombo(true); // 'sworda'
    advancePlayerAttackCombo(false); // ignored
    expect(getPlayerAttackClip()).toBe('sworda');
  });

  it('single-string hints pass through untouched', () => {
    reset();
    setPlayerAttackClip('chop');
    expect(getPlayerAttackClip()).toBe('chop');
    advancePlayerAttackCombo(true);
    expect(getPlayerAttackClip()).toBe('chop');
  });
});
