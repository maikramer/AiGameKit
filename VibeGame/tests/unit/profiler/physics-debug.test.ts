import { describe, expect, it } from 'bun:test';
import { State, Transform } from 'vibegame';
import {
  BodyType,
  CharacterController,
  Collider,
  ColliderShape,
  Rigidbody,
} from '../../../src/plugins/physics/components';
import {
  bindPhysicsDebugState,
  getBoundPhysicsDebugSnapshot,
  getPhysicsDebugSnapshot,
  renderPhysicsTab,
  type PhysicsDebugSnapshot,
} from '../../../src/plugins/profiler/physics-debug';

describe('getPhysicsDebugSnapshot', () => {
  it('reports available=false without a physics context', () => {
    const state = new State();
    const snap = getPhysicsDebugSnapshot(state);
    expect(snap.available).toBe(false);
    expect(snap.bodies.total).toBe(0);
    expect(snap.rapier.bodyCount).toBe(0);
  });

  it('counts colliders by shape and sensors from ECS components', () => {
    const state = new State();

    const capsule = state.createEntity();
    state.addComponent(capsule, Transform);
    state.addComponent(capsule, Collider, { shape: ColliderShape.Capsule });
    Collider.radius[capsule] = 0.35;
    Collider.height[capsule] = 1.4;

    const cylinder = state.createEntity();
    state.addComponent(cylinder, Transform);
    state.addComponent(cylinder, Collider, { shape: ColliderShape.Cylinder });
    Collider.radius[cylinder] = 0.6;
    Collider.height[cylinder] = 1.0;
    Collider.isSensor[cylinder] = 1;

    const marker = state.createEntity();
    state.addComponent(marker, Transform);
    state.addComponent(marker, Collider, { shape: ColliderShape.Precompute });

    const snap = getPhysicsDebugSnapshot(state);
    expect(snap.colliders.total).toBe(3);
    expect(snap.colliders.sensors).toBe(1);
    expect(snap.colliders.byShape.capsule).toBe(1);
    expect(snap.colliders.byShape.cylinder).toBe(1);
    expect(snap.colliders.byShape.precompute).toBe(1);
    expect(snap.precompute).toEqual({
      capsules: 1,
      cylinders: 1,
      unresolved: 1,
    });
  });

  it('counts character controllers and grounded state', () => {
    const state = new State();

    const player = state.createEntity();
    state.addComponent(player, Transform);
    state.addComponent(player, CharacterController);
    CharacterController.grounded[player] = 1;

    const npc = state.createEntity();
    state.addComponent(npc, Transform);
    state.addComponent(npc, CharacterController);
    CharacterController.grounded[npc] = 0;

    const snap = getPhysicsDebugSnapshot(state);
    expect(snap.cct.total).toBe(2);
    expect(snap.cct.grounded).toBe(1);
  });

  it('tolerates Rigidbody present without a Rapier body', () => {
    const state = new State();
    const eid = state.createEntity();
    state.addComponent(eid, Transform);
    state.addComponent(eid, Rigidbody, { type: BodyType.Fixed });

    // No PhysicsInitializationSystem ran, so entityToRigidbody is empty; the
    // snapshot must not throw and must count what exists via ECS.
    const snap = getPhysicsDebugSnapshot(state);
    expect(snap.available).toBe(false);
    expect(snap.bodies.total).toBe(1);
    expect(snap.bodies.fixed).toBe(1);
    expect(snap.sync.rigidbodies).toBe(0);
  });
});

describe('renderPhysicsTab', () => {
  it('formats the unavailable message', () => {
    const snap: PhysicsDebugSnapshot = {
      available: false,
      frame: 0,
      bodies: {
        total: 0,
        fixed: 0,
        dynamic: 0,
        kinematic: 0,
        sleeping: 0,
        awake: 0,
      },
      colliders: { total: 0, sensors: 0, byShape: {} },
      cct: { total: 0, grounded: 0 },
      rapier: {
        bodyCount: 0,
        colliderCount: 0,
        controllerCount: 0,
        timestep: 0,
      },
      sync: {
        rigidbodies: 0,
        colliders: 0,
        failedBodies: 0,
        failedColliders: 0,
        removalsDirty: 0,
      },
      systems: [],
      precompute: { capsules: 0, cylinders: 0, unresolved: 0 },
    };
    expect(renderPhysicsTab(snap)).toContain('sem mundo Rapier');
  });

  it('formats bodies, shapes, CCTs, rapier and fixed systems', () => {
    const snap: PhysicsDebugSnapshot = {
      available: true,
      frame: 120,
      bodies: {
        total: 5,
        fixed: 3,
        dynamic: 1,
        kinematic: 1,
        sleeping: 4,
        awake: 1,
      },
      colliders: {
        total: 6,
        sensors: 1,
        byShape: { capsule: 3, cylinder: 2, trimesh: 1 },
      },
      cct: { total: 1, grounded: 1 },
      rapier: {
        bodyCount: 6,
        colliderCount: 7,
        controllerCount: 1,
        timestep: 0.0166667,
      },
      sync: {
        rigidbodies: 5,
        colliders: 6,
        failedBodies: 0,
        failedColliders: 0,
        removalsDirty: 0,
      },
      systems: [
        {
          name: 'PhysicsStepSystem',
          avgMs: 1.2,
          lastMs: 0.9,
          p95Ms: 1.8,
        },
        {
          name: 'PhysicsRapierSyncSystem',
          avgMs: 0.21,
          lastMs: 0.2,
          p95Ms: 0.4,
        },
      ],
      precompute: { capsules: 3, cylinders: 2, unresolved: 0 },
    };
    const text = renderPhysicsTab(snap);
    expect(text).toContain('bodies=5');
    expect(text).toContain('fixed 3');
    expect(text).toContain('4 dormindo');
    expect(text).toContain('capsule 3');
    expect(text).toContain('precompute capsules=3');
    expect(text).toContain('CCT');
    expect(text).toContain('grounded=1');
    expect(text).toContain('timestep=0.0167');
    expect(text).toContain('PhysicsStepSystem');
    expect(text).toContain('PhysicsRapierSyncSystem');
  });
});

describe('bindPhysicsDebugState', () => {
  it('returns null before any bind', () => {
    expect(getBoundPhysicsDebugSnapshot()).toBeNull();
  });

  it('returns the bound state snapshot after bind', () => {
    const state = new State();
    bindPhysicsDebugState(state);
    const snap = getBoundPhysicsDebugSnapshot();
    expect(snap).not.toBeNull();
    expect(snap!.available).toBe(false);
  });
});
