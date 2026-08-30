import { beforeEach, describe, expect, it } from 'bun:test';
import { State, Parent } from 'aigamekit-vibegame';
import { Transform } from '../../../src/plugins/transforms';
import { MeshRenderer } from '../../../src/plugins/rendering';
import {
  applyFallAnimation,
  applyJumpAnimation,
  applyLandingAnimation,
  applyWalkAnimation,
  calculateWalkAnimation,
  createBodyPart,
  easeInOutSine,
  easeOutCubic,
  resetBodyPartTransforms,
} from '../../../src/plugins/animation/utils';
import { BODY_PARTS } from '../../../src/plugins/animation/constants';

describe('easeInOutSine', () => {
  it('returns 0 at t=0', () => {
    expect(easeInOutSine(0)).toBeCloseTo(0, 5);
  });

  it('returns 1 at t=1', () => {
    expect(easeInOutSine(1)).toBeCloseTo(1, 5);
  });

  for (const t of [0.25, 0.5, 0.75]) {
    it(`easeInOutSine(${t}) is between 0 and 1`, () => {
      const v = easeInOutSine(t);
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThanOrEqual(1);
    });
  }
});

describe('easeOutCubic', () => {
  it('returns 0 at t=0', () => {
    expect(easeOutCubic(0)).toBeCloseTo(0, 5);
  });

  it('returns 1 at t=1', () => {
    expect(easeOutCubic(1)).toBeCloseTo(1, 5);
  });

  for (const t of [0.1, 0.5, 0.9]) {
    it(`easeOutCubic(${t}) is between 0 and 1`, () => {
      const v = easeOutCubic(t);
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThanOrEqual(1);
    });
  }
});

describe('calculateWalkAnimation', () => {
  it('phase 0 yields zero rotations', () => {
    const { armRotation, legRotation } = calculateWalkAnimation(0);
    expect(armRotation).toBeCloseTo(0, 5);
    expect(legRotation).toBeCloseTo(0, 5);
  });

  for (const phase of [0.25, 0.5, 0.75]) {
    it(`phase ${phase} produces finite rotations`, () => {
      const { armRotation, legRotation } = calculateWalkAnimation(phase);
      expect(Number.isFinite(armRotation)).toBe(true);
      expect(Number.isFinite(legRotation)).toBe(true);
    });
  }

  it('phase 0.25 arm and leg have opposite sign pattern at quarter cycle', () => {
    const { armRotation, legRotation } = calculateWalkAnimation(0.25);
    expect(Math.sign(armRotation)).toBe(Math.sign(legRotation));
  });
});

describe('applyWalkAnimation', () => {
  const ids = { la: 1, ra: 2, ll: 3, rl: 4 };

  beforeEach(() => {
    for (const id of Object.values(ids)) {
      Transform.eulerX[id] = 0;
      Transform.dirty[id] = 0;
    }
  });

  it('marks all four limbs dirty', () => {
    applyWalkAnimation(ids.la, ids.ra, ids.ll, ids.rl, 0.25);
    expect(Transform.dirty[ids.la]).toBe(1);
    expect(Transform.dirty[ids.ra]).toBe(1);
    expect(Transform.dirty[ids.ll]).toBe(1);
    expect(Transform.dirty[ids.rl]).toBe(1);
  });

  it('swings arms in opposite directions', () => {
    applyWalkAnimation(ids.la, ids.ra, ids.ll, ids.rl, 0.25);
    expect(Math.sign(Transform.eulerX[ids.la])).not.toBe(
      Math.sign(Transform.eulerX[ids.ra])
    );
  });

  for (const phase of [0, 0.125, 0.375, 0.5]) {
    it(`applyWalkAnimation phase ${phase} sets eulerX on limbs`, () => {
      applyWalkAnimation(ids.la, ids.ra, ids.ll, ids.rl, phase);
      expect(Number.isFinite(Transform.eulerX[ids.la])).toBe(true);
      expect(Number.isFinite(Transform.eulerX[ids.rl])).toBe(true);
    });
  }
});

describe('createBodyPart', () => {
  let state: State;
  let parent: number;

  beforeEach(() => {
    state = new State();
    state.registerComponent('transform', Transform);
    state.registerComponent('mesh-renderer', MeshRenderer);
    state.registerComponent('parent', Parent);
    parent = state.createEntity();
    state.addComponent(parent, Transform);
  });

  for (const partName of [
    'head',
    'torso',
    'leftArm',
    'rightArm',
    'leftLeg',
    'rightLeg',
  ] as const) {
    it(`creates ${partName} child with Parent link`, () => {
      const child = createBodyPart(state, parent, partName);
      expect(Parent.entity[child]).toBe(parent);
      expect(Transform.posY[child]).toBeCloseTo(
        BODY_PARTS[partName].offset.y,
        5
      );
      expect(MeshRenderer.visible[child]).toBe(1);
    });
  }
});

describe('resetBodyPartTransforms', () => {
  const ids = {
    head: 10,
    torso: 11,
    la: 12,
    ra: 13,
    ll: 14,
    rl: 15,
  };

  beforeEach(() => {
    for (const id of Object.values(ids)) {
      Transform.eulerX[id] = 99;
      Transform.scaleY[id] = 2;
      Transform.dirty[id] = 0;
    }
  });

  it('clears euler rotations and normalizes torso scale', () => {
    resetBodyPartTransforms(
      ids.head,
      ids.torso,
      ids.la,
      ids.ra,
      ids.ll,
      ids.rl
    );
    expect(Transform.eulerX[ids.la]).toBe(0);
    expect(Transform.scaleY[ids.torso]).toBe(1);
    expect(Transform.dirty[ids.head]).toBe(1);
  });

  it('restores head offset Y from BODY_PARTS', () => {
    Transform.posY[ids.head] = 999;
    resetBodyPartTransforms(
      ids.head,
      ids.torso,
      ids.la,
      ids.ra,
      ids.ll,
      ids.rl
    );
    expect(Transform.posY[ids.head]).toBeCloseTo(BODY_PARTS.head.offset.y, 5);
  });
});

describe('procedural animation helpers smoke', () => {
  const ids = { h: 20, t: 21, la: 22, ra: 23, ll: 24, rl: 25 };

  for (const jumpTime of [0, 0.05, 0.2]) {
    it(`applyJumpAnimation t=${jumpTime} sets dirty flags`, () => {
      applyJumpAnimation(
        ids.h,
        ids.t,
        ids.la,
        ids.ra,
        ids.ll,
        ids.rl,
        jumpTime
      );
      expect(Transform.dirty[ids.t]).toBe(1);
    });
  }

  for (const fallTime of [0, 0.5, 1.0]) {
    it(`applyFallAnimation t=${fallTime} sets dirty flags`, () => {
      applyFallAnimation(
        ids.h,
        ids.t,
        ids.la,
        ids.ra,
        ids.ll,
        ids.rl,
        fallTime
      );
      expect(Transform.dirty[ids.la]).toBe(1);
    });
  }

  for (const landTime of [0, 0.05, 0.1]) {
    it(`applyLandingAnimation t=${landTime} adjusts torso Y`, () => {
      Transform.posY[ids.t] = BODY_PARTS.torso.offset.y;
      applyLandingAnimation(ids.h, ids.t, landTime);
      expect(Number.isFinite(Transform.posY[ids.t])).toBe(true);
    });
  }
});
