import { describe, expect, it } from 'bun:test';
import {
  doorWorldOffset,
  doorWorldPosition,
  rotateYawXZ,
} from '../../../src/extras/door-offset';

describe('doorWorldOffset', () => {
  it('rotates +Z front to west when building yaw is -90', () => {
    const door = { localX: 0, localZ: 3, faceYawDeg: 0, standOff: 1.2 };
    const off = doorWorldOffset(door, -90);
    expect(off.x).toBeCloseTo(-4.2, 5);
    expect(off.z).toBeCloseTo(0, 5);
  });

  it('places house_a portal on village_house −X door after yaw -90', () => {
    const door = {
      localX: -2.36,
      localZ: -1.35,
      faceYawDeg: -90,
      standOff: 1.2,
    };
    const pos = doorWorldPosition(25, 12, -90, door);
    expect(pos.x).toBeCloseTo(26.35, 2);
    expect(pos.z).toBeCloseTo(8.44, 2);
  });

  it('places barn portal east when yaw is 90', () => {
    const door = { localX: 0, localZ: 5.49, faceYawDeg: 0, standOff: 1.5 };
    const pos = doorWorldPosition(-33.1, 30, 90, door);
    expect(pos.x).toBeCloseTo(-26.11, 2);
    expect(pos.z).toBeCloseTo(30, 5);
  });

  it('offsets side-face door along −X then rotates with building yaw', () => {
    const door = {
      localX: -2.36,
      localZ: 0,
      faceYawDeg: -90,
      standOff: 1.2,
    };
    const off = doorWorldOffset(door, 0);
    expect(off.x).toBeCloseTo(-3.56, 5);
    expect(off.z).toBeCloseTo(0, 5);
  });

  it('rotateYawXZ matches Three.js Y-up for 90 deg', () => {
    const r = rotateYawXZ(0, 1, 90);
    expect(r.x).toBeCloseTo(1, 5);
    expect(r.z).toBeCloseTo(0, 5);
  });
});
