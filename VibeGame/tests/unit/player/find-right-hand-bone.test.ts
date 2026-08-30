import { describe, expect, it } from 'bun:test';
import { Object3D } from 'three';
import { findRightHandBone } from 'aigamekit-vibegame';

function rigWith(name: string): Object3D {
  const root = new Object3D();
  const bone = new Object3D();
  bone.name = name;
  root.add(bone);
  return root;
}

describe('findRightHandBone — skeleton naming conventions', () => {
  it('resolves the classic Quaternius/UMA name (RightHand)', () => {
    const rig = rigWith('RightHand');
    expect(findRightHandBone(rig)?.name).toBe('RightHand');
  });

  it('resolves the Mixamo-style pool name (hand_r) — the pool-hero regression', () => {
    const rig = rigWith('hand_r');
    expect(findRightHandBone(rig)?.name).toBe('hand_r');
  });

  it('resolves other common spellings', () => {
    expect(findRightHandBone(rigWith('Hand_R'))?.name).toBe('Hand_R');
    expect(findRightHandBone(rigWith('right_hand'))?.name).toBe('right_hand');
  });

  it('fuzzy-resolves unconventional right-hand bones (R_hand)', () => {
    const rig = rigWith('R_hand');
    expect(findRightHandBone(rig)?.name).toBe('R_hand');
  });

  it('never resolves a finger or the left hand', () => {
    const root = new Object3D();
    const finger = new Object3D();
    finger.name = 'RightHandFinger1';
    const left = new Object3D();
    left.name = 'hand_l';
    root.add(finger, left);
    expect(findRightHandBone(root)).toBeNull();
  });

  it('prefers an exact candidate over the fuzzy match', () => {
    const root = new Object3D();
    const fuzzy = new Object3D();
    fuzzy.name = 'R_hand';
    const exact = new Object3D();
    exact.name = 'hand_r';
    // Fuzzy listed first on purpose: the exact list must win regardless of order.
    root.add(fuzzy, exact);
    expect(findRightHandBone(root)?.name).toBe('hand_r');
  });

  it('returns null for a rig without any hand bone', () => {
    expect(findRightHandBone(new Object3D())).toBeNull();
  });
});
