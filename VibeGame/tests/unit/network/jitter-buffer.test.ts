import { describe, expect, it } from 'bun:test';
import {
  JitterBuffer,
  type TransformSnapshot,
} from '../../../src/plugins/network/jitter-buffer';

function makeSnapshot(
  timestamp: number,
  x = 0,
  y = 0,
  z = 0
): TransformSnapshot {
  return {
    timestamp,
    posX: x,
    posY: y,
    posZ: z,
    rotX: 0,
    rotY: 0,
    rotZ: 0,
    rotW: 1,
    scaleX: 1,
    scaleY: 1,
    scaleZ: 1,
  };
}

describe('JitterBuffer', () => {
  it('push() adds samples to the buffer', () => {
    const buf = new JitterBuffer();
    expect(buf.length).toBe(0);

    buf.push(makeSnapshot(100));
    expect(buf.length).toBe(1);

    buf.push(makeSnapshot(200));
    expect(buf.length).toBe(2);
  });

  it('sample() returns interpolated state between two+ samples when timestamps straddle targetTime', () => {
    const buf = new JitterBuffer(0);
    buf.push(makeSnapshot(100, 0, 0, 0));
    buf.push(makeSnapshot(200, 10, 0, 0));

    const result = buf.sample(100);
    expect(result).not.toBeNull();
    expect(result!.posX).toBeCloseTo(0);
  });

  it('sample() returns last known state with fewer than 2 samples', () => {
    const buf = new JitterBuffer(50);
    buf.push(makeSnapshot(100, 5, 0, 0));

    const result = buf.sample(200);
    expect(result).not.toBeNull();
    expect(result!.posX).toBeCloseTo(5);
  });

  it('sample() returns null when buffer is empty', () => {
    const buf = new JitterBuffer(50);

    const result = buf.sample(200);
    expect(result).toBeNull();
  });

  it('sample() interpolates with 3+ samples where targetTime falls between surviving pair', () => {
    const buf = new JitterBuffer(0);
    buf.push(makeSnapshot(100, 0, 0, 0));
    buf.push(makeSnapshot(200, 10, 0, 0));
    buf.push(makeSnapshot(300, 20, 0, 0));

    const result = buf.sample(100);
    expect(result).not.toBeNull();
    expect(result!.posX).toBeCloseTo(0);
  });

  it('sample() with delay returns correct snapshot from surviving pair', () => {
    const buf = new JitterBuffer(50);
    buf.push(makeSnapshot(100, 0, 0, 0));
    buf.push(makeSnapshot(200, 10, 0, 0));

    const result = buf.sample(200);
    expect(result).not.toBeNull();
    expect(result!.posX).toBeCloseTo(10);
  });

  it('discard() removes old samples before renderTime - targetDelay', () => {
    const buf = new JitterBuffer(50);
    buf.push(makeSnapshot(50, 0));
    buf.push(makeSnapshot(100, 1));
    buf.push(makeSnapshot(150, 2));
    buf.push(makeSnapshot(200, 3));

    buf.discard(175);
    expect(buf.length).toBe(2);
  });

  it('discard() keeps at least one sample', () => {
    const buf = new JitterBuffer(50);
    buf.push(makeSnapshot(100, 1));

    buf.discard(300);
    expect(buf.length).toBe(1);
  });

  it('clear() empties the buffer', () => {
    const buf = new JitterBuffer(50);
    buf.push(makeSnapshot(100));
    buf.push(makeSnapshot(200));
    expect(buf.length).toBe(2);

    buf.clear();
    expect(buf.length).toBe(0);
  });

  it('push() sorts samples by timestamp', () => {
    const buf = new JitterBuffer(0);
    buf.push(makeSnapshot(300, 3));
    buf.push(makeSnapshot(100, 1));
    buf.push(makeSnapshot(200, 2));

    expect(buf.length).toBe(3);

    const result = buf.sample(100);
    expect(result).not.toBeNull();
    expect(result!.posX).toBeCloseTo(1);
  });

  it('returns correct quaternion from slerp for near-identity rotations', () => {
    const buf = new JitterBuffer(0);
    const a: TransformSnapshot = {
      timestamp: 0,
      posX: 0,
      posY: 0,
      posZ: 0,
      rotX: 0,
      rotY: 0,
      rotZ: 0,
      rotW: 1,
      scaleX: 1,
      scaleY: 1,
      scaleZ: 1,
    };
    const b: TransformSnapshot = {
      timestamp: 100,
      posX: 0,
      posY: 0,
      posZ: 0,
      rotX: 0,
      rotY: 0,
      rotZ: 0,
      rotW: 1,
      scaleX: 1,
      scaleY: 1,
      scaleZ: 1,
    };
    buf.push(a);
    buf.push(b);

    const result = buf.sample(100);
    expect(result).not.toBeNull();
    expect(result!.rotX).toBeCloseTo(0);
    expect(result!.rotY).toBeCloseTo(0);
    expect(result!.rotZ).toBeCloseTo(0);
    expect(result!.rotW).toBeCloseTo(1);
  });

  it('returns correct scale from surviving sample', () => {
    const buf = new JitterBuffer(0);
    const a: TransformSnapshot = {
      timestamp: 0,
      posX: 0,
      posY: 0,
      posZ: 0,
      rotX: 0,
      rotY: 0,
      rotZ: 0,
      rotW: 1,
      scaleX: 1,
      scaleY: 1,
      scaleZ: 1,
    };
    const b: TransformSnapshot = {
      timestamp: 100,
      posX: 0,
      posY: 0,
      posZ: 0,
      rotX: 0,
      rotY: 0,
      rotZ: 0,
      rotW: 1,
      scaleX: 2,
      scaleY: 3,
      scaleZ: 4,
    };
    buf.push(a);
    buf.push(b);

    const result = buf.sample(100);
    expect(result).not.toBeNull();
    expect(result!.scaleX).toBeCloseTo(2);
    expect(result!.scaleY).toBeCloseTo(3);
    expect(result!.scaleZ).toBeCloseTo(4);
  });
});
