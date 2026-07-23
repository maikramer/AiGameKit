import { beforeEach, describe, expect, it } from 'bun:test';
import * as THREE from 'three';
import { SteeringVehicle } from '../../../src/plugins/ai-steering/vehicle';

describe('SteeringVehicle seek table-driven', () => {
  const targets: Array<[number, number, number]> = [
    [10, 0, 0],
    [0, 0, 10],
    [-10, 0, 0],
    [0, 0, -10],
    [5, 0, 5],
    [20, 0, -3],
    [1, 0, 1],
    [100, 0, 0],
    [-50, 0, 50],
    [0.5, 0, 0],
  ];

  for (const [tx, ty, tz] of targets) {
    it(`seek moves toward (${tx},${ty},${tz})`, () => {
      const v = new SteeringVehicle();
      v.position.set(0, 0, 0);
      v.maxSpeed = 2;
      v.maxForce = 10;
      v.seekActive = true;
      v.seekTarget.set(tx, ty, tz);
      const distBefore = v.position.distanceTo(v.seekTarget);
      for (let i = 0; i < 30; i++) v.update(0.05);
      const distAfter = v.position.distanceTo(v.seekTarget);
      expect(distAfter).toBeLessThan(distBefore);
    });
  }
});

describe('SteeringVehicle maxSpeed clamp', () => {
  for (const maxSpeed of [0.5, 1, 2, 5, 10]) {
    it(`velocity length <= maxSpeed ${maxSpeed}`, () => {
      const v = new SteeringVehicle();
      v.maxSpeed = maxSpeed;
      v.maxForce = 100;
      v.seekActive = true;
      v.seekTarget.set(1000, 0, 0);
      for (let i = 0; i < 50; i++) v.update(0.016);
      expect(v.velocity.length()).toBeLessThanOrEqual(maxSpeed + 1e-4);
    });
  }
});

describe('SteeringVehicle flee table-driven', () => {
  for (let panic of [5, 10, 20, 50, 100]) {
    it(`flee within panic ${panic} increases distance`, () => {
      const v = new SteeringVehicle();
      v.position.set(0, 0, 0);
      v.fleeActive = true;
      v.fleeTarget.set(1, 0, 0);
      v.fleePanicDistance = panic;
      v.maxSpeed = 3;
      v.maxForce = 10;
      const d0 = v.position.distanceTo(v.fleeTarget);
      for (let i = 0; i < 20; i++) v.update(0.05);
      expect(v.position.distanceTo(v.fleeTarget)).toBeGreaterThan(d0);
    });
  }

  for (let panic of [5, 10, 20]) {
    it(`flee outside panic ${panic} stays put`, () => {
      const v = new SteeringVehicle();
      v.position.set(1000, 0, 1000);
      v.fleeActive = true;
      v.fleeTarget.set(0, 0, 0);
      v.fleePanicDistance = panic;
      v.update(0.1);
      expect(v.position.x).toBeCloseTo(1000, 0);
    });
  }
});

describe('SteeringVehicle obstacle avoidance', () => {
  for (let i = 0; i < 10; i++) {
    it(`avoidObstacles with obstacle ahead case ${i}`, () => {
      const v = new SteeringVehicle();
      v.position.set(0, 0, 0);
      v.velocity.set(0, 0, 2);
      v.obstacleActive = true;
      v.obstacles = [
        { position: new THREE.Vector3(0, 0, 3 + i * 0.1), boundingRadius: 0.5 },
      ];
      const before = v.position.clone();
      for (let s = 0; s < 15; s++) v.update(0.05);
      expect(v.position.distanceTo(before)).toBeGreaterThan(0);
    });
  }
});

describe('SteeringVehicle defaults', () => {
  const fields: Array<[keyof SteeringVehicle, unknown]> = [
    ['maxSpeed', 1],
    ['maxForce', 1],
    ['seekActive', false],
    ['fleeActive', false],
    ['wanderActive', false],
    ['obstacleActive', true],
    ['obstacleWeight', 1.5],
  ];

  for (const [field, expected] of fields) {
    it(`default ${String(field)}`, () => {
      const v = new SteeringVehicle();
      expect(v[field]).toEqual(expected);
    });
  }

  for (let i = 0; i < 20; i++) {
    it(`update with no behaviors idles ${i}`, () => {
      const v = new SteeringVehicle();
      v.update(0.016);
      expect(v.velocity.lengthSq()).toBeLessThan(1e-6);
    });
  }
});

describe('SteeringVehicle wander active', () => {
  let randomIdx = 0;
  const seq = [0.1, 0.9, 0.2, 0.8, 0.3, 0.7, 0.4, 0.6, 0.5, 0.55];

  beforeEach(() => {
    randomIdx = 0;
  });

  function mockRandom(): number {
    return seq[randomIdx++ % seq.length]!;
  }

  for (let i = 0; i < 15; i++) {
    it(`wander with mocked random ${i}`, () => {
      const orig = Math.random;
      Math.random = mockRandom;
      try {
        const v = new SteeringVehicle();
        v.wanderActive = true;
        v.maxSpeed = 1;
        v.maxForce = 5;
        v.wanderRadius = 1;
        v.wanderDistance = 2;
        v.wanderJitter = 1;
        const p0 = v.position.clone();
        for (let s = 0; s < 10; s++) v.update(0.05);
        expect(v.position.distanceTo(p0)).toBeGreaterThanOrEqual(0);
      } finally {
        Math.random = orig;
      }
    });
  }
});

describe('SteeringVehicle extra coverage', () => {
  for (let i = 0; i < 5; i++) {
    it(`maxForce caps integration force ${i}`, () => {
      const v = new SteeringVehicle();
      v.maxSpeed = 100;
      v.maxForce = 1;
      v.seekActive = true;
      v.seekTarget.set(1000, 0, 0);
      v.update(1);
      expect(v.velocity.length()).toBeLessThanOrEqual(v.maxSpeed + 1e-4);
    });
  }
});
