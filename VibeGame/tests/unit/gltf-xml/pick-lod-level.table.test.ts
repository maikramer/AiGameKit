import { describe, expect, it } from 'bun:test';
import { pickLodLevel } from '../../../src/plugins/gltf-xml/gltf-lod-level';

describe('pickLodLevel — fresh selection (no prevLevel)', () => {
  const near = 10;
  const mid = 30;

  for (let dist = 0; dist <= 50; dist += 1) {
    it(`dist=${dist} without hysteresis`, () => {
      const level = pickLodLevel(dist, near, mid);
      if (dist < near) expect(level).toBe(0);
      else if (dist < mid) expect(level).toBe(1);
      else expect(level).toBe(2);
    });
  }
});

describe('pickLodLevel — hysteresis from level 0', () => {
  const near = 10;
  const mid = 30;

  for (let dist = 0; dist <= 40; dist += 1) {
    it(`prev=0 dist=${dist}`, () => {
      const level = pickLodLevel(dist, near, mid, 0);
      expect(level).toBe(dist > near ? 1 : 0);
    });
  }
});

describe('pickLodLevel — hysteresis from level 1', () => {
  const near = 10;
  const mid = 30;
  const upgradeNear = near * 0.85;

  for (let dist = 0; dist <= 45; dist += 1) {
    it(`prev=1 dist=${dist}`, () => {
      const level = pickLodLevel(dist, near, mid, 1);
      if (dist < upgradeNear) expect(level).toBe(0);
      else if (dist > mid) expect(level).toBe(2);
      else expect(level).toBe(1);
    });
  }
});

describe('pickLodLevel — hysteresis from level 2', () => {
  const near = 10;
  const mid = 30;
  const upgradeMid = mid * 0.85;

  for (let dist = 0; dist <= 45; dist += 1) {
    it(`prev=2 dist=${dist}`, () => {
      const level = pickLodLevel(dist, near, mid, 2);
      expect(level).toBe(dist < upgradeMid ? 1 : 2);
    });
  }
});

describe('pickLodLevel — alternate near/mid thresholds', () => {
  const configs: Array<{ near: number; mid: number }> = [
    { near: 5, mid: 20 },
    { near: 15, mid: 45 },
    { near: 8, mid: 24 },
  ];

  for (const { near, mid } of configs) {
    for (let dist = 0; dist <= mid + 5; dist += 5) {
      it(`n=${near} m=${mid} d=${dist} fresh`, () => {
        const level = pickLodLevel(dist, near, mid);
        if (dist < near) expect(level).toBe(0);
        else if (dist < mid) expect(level).toBe(1);
        else expect(level).toBe(2);
      });
    }
  }
});

describe('pickLodLevel — invalid prev falls back to fresh', () => {
  it('prev=99 behaves like undefined at dist=5', () => {
    expect(pickLodLevel(5, 10, 30, 99)).toBe(0);
  });

  it('prev=-1 behaves like undefined at dist=35', () => {
    expect(pickLodLevel(35, 10, 30, -1)).toBe(2);
  });
});
