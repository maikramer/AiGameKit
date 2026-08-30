import { describe, expect, it } from 'bun:test';
import * as THREE from 'three';
import { WeaponTrail, bladeEndpoints } from '../../../src/extras/weapon-trail';

const base = (x: number) => new THREE.Vector3(x, 1, 0);
const tip = (x: number) => new THREE.Vector3(x, 2, 0);

describe('WeaponTrail', () => {
  it('draws nothing until it has two samples', () => {
    const trail = new WeaponTrail();

    expect(trail.object3D.geometry.drawRange.count).toBe(0);
    trail.push(base(0), tip(0), 0);
    expect(trail.object3D.geometry.drawRange.count).toBe(0);

    trail.push(base(0.5), tip(0.5), 0.016);
    expect(trail.object3D.geometry.drawRange.count).toBe(6); // one quad
  });

  it('drops samples the blade barely moved between', () => {
    const trail = new WeaponTrail({ minDistance: 0.1 });

    trail.push(base(0), tip(0), 0);
    trail.push(base(0), new THREE.Vector3(0.01, 2, 0), 0.016);

    expect(trail.sampleCount).toBe(1);
  });

  it('never grows past its ring buffer', () => {
    const trail = new WeaponTrail({ segments: 4, lifetime: 10 });

    for (let i = 0; i < 20; i++) trail.push(base(i), tip(i), i * 0.016);

    expect(trail.sampleCount).toBe(4);
    expect(trail.object3D.geometry.drawRange.count).toBe(18); // 3 quads
  });

  it('fades the tail: alpha falls off with sample age', () => {
    const trail = new WeaponTrail({ lifetime: 0.2, opacity: 1 });

    trail.push(base(0), tip(0), 0);
    trail.push(base(1), tip(1), 0.1);

    const alphas = trail.object3D.geometry.getAttribute('aAlpha');
    // Vertex 1 = tip of the oldest sample (half-aged), vertex 3 = newest tip.
    expect(alphas.getX(1)).toBeCloseTo(0.5, 3);
    expect(alphas.getX(3)).toBeCloseTo(1, 3);
    // Hilt side stays dimmer than the edge so the ribbon reads as a swept edge.
    expect(alphas.getX(2)).toBeLessThan(alphas.getX(3));
  });

  it('expires samples once they outlive the trail, with no new pushes', () => {
    const trail = new WeaponTrail({ lifetime: 0.1 });

    trail.push(base(0), tip(0), 0);
    trail.push(base(1), tip(1), 0.02);
    expect(trail.sampleCount).toBe(2);

    trail.update(0.15); // both samples older than the lifetime
    expect(trail.sampleCount).toBe(0);
    expect(trail.object3D.geometry.drawRange.count).toBe(0);
  });

  it('is world-space: the ribbon mesh carries no transform of its own', () => {
    const trail = new WeaponTrail();

    expect(trail.object3D.matrixAutoUpdate).toBe(false);
    expect(trail.object3D.frustumCulled).toBe(false);
  });

  it('clear() empties the ribbon immediately', () => {
    const trail = new WeaponTrail();
    trail.push(base(0), tip(0), 0);
    trail.push(base(1), tip(1), 0.016);

    trail.clear();

    expect(trail.sampleCount).toBe(0);
    expect(trail.object3D.geometry.drawRange.count).toBe(0);
  });

  it('writes the pushed endpoints straight into the strip', () => {
    const trail = new WeaponTrail();
    trail.push(new THREE.Vector3(1, 2, 3), new THREE.Vector3(4, 5, 6), 0);
    trail.push(new THREE.Vector3(7, 8, 9), new THREE.Vector3(10, 11, 12), 0.01);

    const pos = trail.object3D.geometry.getAttribute('position');
    expect([pos.getX(0), pos.getY(0), pos.getZ(0)]).toEqual([1, 2, 3]);
    expect([pos.getX(1), pos.getY(1), pos.getZ(1)]).toEqual([4, 5, 6]);
    expect([pos.getX(3), pos.getY(3), pos.getZ(3)]).toEqual([10, 11, 12]);
  });
});

describe('bladeEndpoints', () => {
  it("measures along the object's longest axis", () => {
    // A blade 2m long on Y, thin on X/Z, sitting at the origin.
    const blade = new THREE.Mesh(new THREE.BoxGeometry(0.1, 2, 0.1));
    blade.updateWorldMatrix(true, false);

    const ends = bladeEndpoints(blade)!;

    expect(ends.base.y).toBeCloseTo(-1, 3);
    expect(ends.tip.y).toBeCloseTo(1, 3);
  });

  it('follows the object into world space (hand bone transform)', () => {
    const hand = new THREE.Object3D();
    hand.position.set(5, 1, 0);
    const blade = new THREE.Mesh(new THREE.BoxGeometry(0.1, 2, 0.1));
    hand.add(blade);
    hand.updateWorldMatrix(true, true);

    const ends = bladeEndpoints(blade)!;

    expect(ends.tip.x).toBeCloseTo(5, 3);
    expect(ends.tip.y).toBeCloseTo(2, 3);
  });

  it('returns null for an object with no geometry to measure', () => {
    expect(bladeEndpoints(new THREE.Object3D())).toBeNull();
  });

  it('extends the sweep past the modelled tip when asked', () => {
    const blade = new THREE.Mesh(new THREE.BoxGeometry(0.1, 2, 0.1));
    blade.updateWorldMatrix(true, false);

    const ends = bladeEndpoints(blade, { extend: 0.25 })!;

    expect(ends.tip.y).toBeCloseTo(1.25, 3);
    expect(ends.base.y).toBeCloseTo(-0.75, 3);
  });

  it('anchors the ribbon partway up the blade with inset', () => {
    const blade = new THREE.Mesh(new THREE.BoxGeometry(0.1, 2, 0.1));
    blade.updateWorldMatrix(true, false);

    const ends = bladeEndpoints(blade, { inset: 0.5 })!;

    // Half the blade: hilt end sits at the middle, tip unchanged.
    expect(ends.base.y).toBeCloseTo(0, 3);
    expect(ends.tip.y).toBeCloseTo(1, 3);
  });
});
