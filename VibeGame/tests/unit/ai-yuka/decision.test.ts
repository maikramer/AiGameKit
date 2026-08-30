import { beforeAll, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import {
  decide,
  Health,
  Transform,
  YUKA_BEHAVIOR_ARRIVE,
  YUKA_BEHAVIOR_EVADE,
  YUKA_BEHAVIOR_FLEE,
  YUKA_BEHAVIOR_FLOCK,
  YUKA_BEHAVIOR_HOLD_RING,
  YUKA_BEHAVIOR_PURSUIT,
  YUKA_BEHAVIOR_SEPARATION,
  YUKA_BEHAVIOR_WANDER,
  type CreatureDecisionProfile,
} from 'aigamekit-vibegame';

const HERO_EID = 1;
const CREATURE_EID = 2;
const DT = 0.016;

function place(eid: number, x: number, z: number): void {
  Transform.posX[eid] = x;
  Transform.posY[eid] = 0;
  Transform.posZ[eid] = z;
}

function setHp(eid: number, hp: number, max = 100): void {
  Health.current[eid] = hp;
  Health.max[eid] = hp > 0 ? max : max;
  void DT;
}

// A minimal state stub — decide() only reads Transform/Health arrays, but its
// signature takes a State for forward-compat (LOS, neighbor queries). We pass a
// loosely-typed object; the function never touches state in the branches we test.
const stubState = {} as never;

beforeAll(() => {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
  globalThis.DOMParser = dom.window.DOMParser;
});

describe('ai-yuka decide() — utility-AI steering mask', () => {
  it('wanders when there is no target', () => {
    setHp(CREATURE_EID, 100);
    const r = decide({
      state: stubState,
      eid: CREATURE_EID,
      targetEid: 0,
      profile: {},
    });
    expect(r.targetEid).toBe(0);
    expect(r.mask & YUKA_BEHAVIOR_WANDER).not.toBe(0);
  });

  it('flees below the flee HP threshold', () => {
    place(CREATURE_EID, 0, 0);
    place(HERO_EID, 2, 0);
    setHp(CREATURE_EID, 15);
    setHp(HERO_EID, 100);
    const profile: CreatureDecisionProfile = { fleeBelowHpFrac: 0.25 };
    const r = decide({
      state: stubState,
      eid: CREATURE_EID,
      targetEid: HERO_EID,
      profile,
    });
    expect(r.targetEid).toBe(HERO_EID);
    expect(r.mask & YUKA_BEHAVIOR_FLEE).not.toBe(0);
  });

  it('pursues when the target is beyond stand-off', () => {
    place(CREATURE_EID, 0, 0);
    place(HERO_EID, 12, 0);
    setHp(CREATURE_EID, 100);
    setHp(HERO_EID, 100);
    const profile: CreatureDecisionProfile = { standOffRange: 4 };
    const r = decide({
      state: stubState,
      eid: CREATURE_EID,
      targetEid: HERO_EID,
      profile,
    });
    expect(r.mask & YUKA_BEHAVIOR_PURSUIT).not.toBe(0);
  });

  it('evades (kites) when the target is closer than stand-off and kite is on', () => {
    place(CREATURE_EID, 0, 0);
    place(HERO_EID, 1, 0);
    setHp(CREATURE_EID, 100);
    setHp(HERO_EID, 100);
    const profile: CreatureDecisionProfile = { standOffRange: 6, kite: true };
    const r = decide({
      state: stubState,
      eid: CREATURE_EID,
      targetEid: HERO_EID,
      profile,
    });
    expect(r.mask & YUKA_BEHAVIOR_EVADE).not.toBe(0);
  });

  it('holds the ring (arrive) when inside the stand-off band', () => {
    place(CREATURE_EID, 0, 0);
    place(HERO_EID, 4, 0);
    setHp(CREATURE_EID, 100);
    setHp(HERO_EID, 100);
    const profile: CreatureDecisionProfile = { standOffRange: 4 };
    const r = decide({
      state: stubState,
      eid: CREATURE_EID,
      targetEid: HERO_EID,
      profile,
    });
    expect(r.mask & YUKA_BEHAVIOR_ARRIVE).not.toBe(0);
    expect(r.mask & YUKA_BEHAVIOR_HOLD_RING).not.toBe(0);
  });

  it('layers separation/flock onto the chosen behavior', () => {
    place(CREATURE_EID, 0, 0);
    place(HERO_EID, 20, 0);
    setHp(CREATURE_EID, 100);
    setHp(HERO_EID, 100);
    const profile: CreatureDecisionProfile = {
      standOffRange: 4,
      separate: true,
      flock: true,
    };
    const r = decide({
      state: stubState,
      eid: CREATURE_EID,
      targetEid: HERO_EID,
      profile,
    });
    expect(r.mask & YUKA_BEHAVIOR_PURSUIT).not.toBe(0);
    expect(r.mask & YUKA_BEHAVIOR_SEPARATION).not.toBe(0);
    expect(r.mask & YUKA_BEHAVIOR_FLOCK).not.toBe(0);
  });

  it('drops aggro when the target dies (treats dead as no target)', () => {
    place(CREATURE_EID, 0, 0);
    place(HERO_EID, 2, 0);
    setHp(CREATURE_EID, 100);
    setHp(HERO_EID, 0);
    const r = decide({
      state: stubState,
      eid: CREATURE_EID,
      targetEid: HERO_EID,
      profile: { standOffRange: 4 },
    });
    expect(r.targetEid).toBe(0);
    expect(r.mask & YUKA_BEHAVIOR_WANDER).not.toBe(0);
  });
});
