import { describe, expect, it } from 'bun:test';
import { State } from '../../../src/core/ecs/state';
import {
  NavMeshAgent,
  NavMeshSurface,
  NavMeshWalkable,
  clearAgentTarget,
  collectWaterObstacles,
} from '../../../src/plugins/navmesh';
import {
  registerWaterBody,
  unregisterWaterBody,
} from '../../../src/plugins/water/registry';
import type { WaterBody } from '../../../src/plugins/water/registry';

const SEG = 16;
const EXPECT_VERTS = SEG * 2;
const EXPECT_TRIS = SEG * 2;

function lakeAt(
  x: number,
  z: number,
  radius: number,
  waterY: number
): WaterBody {
  return {
    kind: 'lake',
    x,
    z,
    radius,
    shoreRadius: radius * 0.7,
    waterY,
  };
}

describe('navmesh bulk: NavMeshAgent defaults', () => {
  for (let eid = 0; eid < 20; eid++) {
    it(`NavMeshAgent.agentIndex[${eid}] defaults to -1`, () => {
      expect(NavMeshAgent.agentIndex[eid]).toBe(-1);
    });
    it(`NavMeshAgent.radius[${eid}] defaults to 0.4`, () => {
      expect(NavMeshAgent.radius[eid]).toBeCloseTo(0.4);
    });
    it(`NavMeshAgent.height[${eid}] defaults to 1.0`, () => {
      expect(NavMeshAgent.height[eid]).toBeCloseTo(1.0);
    });
    it(`NavMeshAgent.enabled[${eid}] defaults to 1`, () => {
      expect(NavMeshAgent.enabled[eid]).toBe(1);
    });
    it(`NavMeshAgent.faceVelocity[${eid}] defaults to 1`, () => {
      expect(NavMeshAgent.faceVelocity[eid]).toBe(1);
    });
  }
});

describe('navmesh bulk: agent target SOA / clearAgentTarget', () => {
  for (let i = 0; i < 25; i++) {
    it(`NavMeshAgent target fields for entity slot ${i + 1}`, () => {
      const eid = i + 1;
      const x = i * 1.1;
      const y = i * 0.5;
      const z = -i * 0.9;
      NavMeshAgent.targetX[eid] = x;
      NavMeshAgent.targetY[eid] = y;
      NavMeshAgent.targetZ[eid] = z;
      NavMeshAgent.hasTarget[eid] = 1;
      expect(NavMeshAgent.targetX[eid]).toBeCloseTo(x);
      expect(NavMeshAgent.targetY[eid]).toBeCloseTo(y);
      expect(NavMeshAgent.targetZ[eid]).toBeCloseTo(z);
      expect(NavMeshAgent.hasTarget[eid]).toBe(1);
    });

    it(`clearAgentTarget clears hasTarget for entity slot ${i + 1}`, () => {
      const state = new State();
      const eid = i + 1;
      NavMeshAgent.hasTarget[eid] = 1;
      clearAgentTarget(state, eid);
      expect(NavMeshAgent.hasTarget[eid]).toBe(0);
    });
  }
});

describe('navmesh bulk: NavMeshSurface / NavMeshWalkable', () => {
  for (let eid = 0; eid < 15; eid++) {
    it(`NavMeshSurface.enabled[${eid}] default 1`, () => {
      expect(NavMeshSurface.enabled[eid]).toBe(1);
    });
    it(`NavMeshSurface.generated[${eid}] default 0`, () => {
      expect(NavMeshSurface.generated[eid]).toBe(0);
    });
    it(`NavMeshWalkable.enabled[${eid}] default 1`, () => {
      expect(NavMeshWalkable.enabled[eid]).toBe(1);
    });
  }
});

describe('navmesh bulk: collectWaterObstacles cylinders', () => {
  for (let i = 0; i < 20; i++) {
    it(`lake at (${i}, ${-i}) yields ${EXPECT_VERTS} vertices`, () => {
      const state = new State();
      const body = lakeAt(i, -i, 3 + (i % 5), i * 0.2);
      registerWaterBody(state, body);
      const geom = collectWaterObstacles(state, 200);
      expect(geom).not.toBeNull();
      expect(geom!.positions.length).toBe(EXPECT_VERTS * 3);
      expect(geom!.indices.length).toBe(EXPECT_TRIS * 3);
      unregisterWaterBody(state, body);
    });
  }

  for (let r = 1; r <= 15; r++) {
    it(`lake radius ${r} keeps vertices on cylinder wall`, () => {
      const state = new State();
      const body = lakeAt(0, 0, r, 5);
      registerWaterBody(state, body);
      const geom = collectWaterObstacles(state, 120)!;
      const p = geom.positions;
      for (let vi = 0; vi < p.length; vi += 3) {
        const dx = p[vi]!;
        const dz = p[vi + 2]!;
        expect(Math.hypot(dx, dz)).toBeCloseTo(r, 2);
      }
      unregisterWaterBody(state, body);
    });
  }
});

describe('navmesh bulk: agent tuning writes', () => {
  for (let i = 0; i < 10; i++) {
    it(`manual speed/radius/height assignment ${i}`, () => {
      const eid = 100 + i;
      NavMeshAgent.speed[eid] = 2 + i * 0.3;
      NavMeshAgent.radius[eid] = 0.2 + i * 0.05;
      NavMeshAgent.height[eid] = 1.2 + i * 0.1;
      expect(NavMeshAgent.speed[eid]).toBeCloseTo(2 + i * 0.3);
      expect(NavMeshAgent.radius[eid]).toBeCloseTo(0.2 + i * 0.05);
      expect(NavMeshAgent.height[eid]).toBeCloseTo(1.2 + i * 0.1);
    });
  }
});
