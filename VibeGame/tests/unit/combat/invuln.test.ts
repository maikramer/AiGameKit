import { describe, expect, it } from 'bun:test';
import {
  Health,
  bindCombatState,
  damageHealth,
  grantInvulnerability,
  healHealth,
} from 'aigamekit-vibegame';
import type { State } from 'aigamekit-vibegame';

const EID = 3;

function bind(): void {
  bindCombatState({} as State);
}

describe('grantInvulnerability — i-frames', () => {
  it('damageHealth is ignored while the timer is active (no HP change)', () => {
    bind();
    Health.current[EID] = 100;
    Health.max[EID] = 100;
    Health.invulnTimer[EID] = 0;
    grantInvulnerability(EID, 0.35);
    damageHealth(EID, 20);
    expect(Health.current[EID]).toBe(100);
    expect(Health.invulnTimer[EID]).toBeCloseTo(0.35, 5);
  });

  it('damage lands again once the timer expires', () => {
    bind();
    Health.current[EID] = 100;
    Health.max[EID] = 100;
    Health.invulnTimer[EID] = 0.01;
    // Expire the window (the CombatInvulnSystem does this in a real game).
    Health.invulnTimer[EID] = 0;
    damageHealth(EID, 20);
    expect(Health.current[EID]).toBe(80);
  });

  it('new grants extend, never shorten', () => {
    Health.invulnTimer[EID] = 0;
    grantInvulnerability(EID, 0.3);
    grantInvulnerability(EID, 0.1);
    expect(Health.invulnTimer[EID]).toBeCloseTo(0.3, 5);
  });

  it('non-positive grants are ignored', () => {
    Health.invulnTimer[EID] = 0;
    grantInvulnerability(EID, 0);
    grantInvulnerability(EID, -1);
    expect(Health.invulnTimer[EID]).toBe(0);
  });

  it('healing is NOT blocked by i-frames', () => {
    bind();
    Health.current[EID] = 40;
    Health.max[EID] = 100;
    Health.invulnTimer[EID] = 0.5;
    healHealth(EID, 10);
    expect(Health.current[EID]).toBe(50);
  });
});
