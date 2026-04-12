import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { SteeringRow } from '../../../src/plugins/ai-steering/context';
import {
  SteeringAgent,
  SteeringTarget,
} from '../../../src/plugins/ai-steering/components';
import { Transform } from '../../../src/plugins/transforms';
import { Rigidbody } from '../../../src/plugins/physics/components';
import { Collider } from '../../../src/plugins/physics/components';
import { AiSteeringPlugin } from '../../../src/plugins/ai-steering/plugin';
import { GltfXmlPlugin } from '../../../src/plugins/gltf-xml/plugin';
import { getSteeringMap } from '../../../src/plugins/ai-steering/context';

describe('SteeringRow obstacle field', () => {
  it('should have optional obstacle field on SteeringRow interface', () => {
    // Type check: SteeringRow is an interface, we verify via structure
    const row: SteeringRow = {
      vehicle: {} as any,
      obstacle: {} as any,
    };
    expect(row).toBeDefined();
    expect(row.obstacle).toBeDefined();
  });

  it('should allow SteeringRow without obstacle (optional)', () => {
    const row: SteeringRow = {
      vehicle: {} as any,
    };
    expect(row.obstacle).toBeUndefined();
  });
});

describe('ObstacleAvoidanceBehavior integration', () => {
  let state: State;
  let eid: number;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(AiSteeringPlugin);
    eid = state.createEntity();
    state.addComponent(eid, SteeringAgent);
    state.addComponent(eid, SteeringTarget);
    state.addComponent(eid, Transform);
    SteeringAgent.active[eid] = 1;
  });

  it('should create ObstacleAvoidanceBehavior when vehicle is ensured', () => {
    const map = getSteeringMap(state);
    expect(map.has(eid)).toBe(false);

    // Trigger ensureVehicle by running the system's internal logic
    // We access it indirectly: system update populates the map
    state.step();

    const row = map.get(eid);
    expect(row).toBeDefined();
    expect(row!.obstacle).toBeDefined();
  });

  it('should set OA weight to 1.5', () => {
    state.step();

    const row = getSteeringMap(state).get(eid)!;
    expect(row.obstacle!.weight).toBeCloseTo(1.5);
  });

  it('should keep OA active regardless of behavior value', () => {
    for (const behavior of [0, 1, 2]) {
      SteeringAgent.behavior[eid] = behavior;
      state.step();

      const row = getSteeringMap(state).get(eid)!;
      expect(row.obstacle!.active).toBe(true);
    }
  });
});

describe('Obstacle list from Fixed-body entities', () => {
  let state: State;
  let npcEid: number;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(AiSteeringPlugin);
    state.registerPlugin(GltfXmlPlugin);

    // Create NPC
    npcEid = state.createEntity();
    state.addComponent(npcEid, SteeringAgent);
    state.addComponent(npcEid, SteeringTarget);
    state.addComponent(npcEid, Transform);
    SteeringAgent.active[npcEid] = 1;
  });

  it('should populate obstacles from Fixed-body entities', () => {
    // Create obstacle: Rigidbody type=Fixed + Collider + Transform
    const obsEid = state.createEntity();
    state.addComponent(obsEid, Rigidbody);
    state.addComponent(obsEid, Collider);
    state.addComponent(obsEid, Transform);

    // Set Fixed body type (BodyType.Fixed = 1)
    Rigidbody.type[obsEid] = 1;
    // Set position
    Transform.posX[obsEid] = 5;
    Transform.posY[obsEid] = 0;
    Transform.posZ[obsEid] = 10;
    // Set collider radius
    Collider.radius[obsEid] = 2;

    state.step();

    const row = getSteeringMap(state).get(npcEid)!;
    expect(row.obstacle!.obstacles).toBeDefined();
    expect(row.obstacle!.obstacles.length).toBeGreaterThanOrEqual(1);

    const obs = row.obstacle!.obstacles[0];
    expect(obs.position.x).toBeCloseTo(5);
    expect(obs.position.y).toBeCloseTo(0);
    expect(obs.position.z).toBeCloseTo(10);
    expect(obs.boundingRadius).toBeCloseTo(2);
  });

  it('should ignore dynamic-body entities', () => {
    // Dynamic body should not appear in obstacle list
    const dynEid = state.createEntity();
    state.addComponent(dynEid, Rigidbody);
    state.addComponent(dynEid, Collider);
    state.addComponent(dynEid, Transform);
    Rigidbody.type[dynEid] = 0; // Dynamic

    state.step();

    const row = getSteeringMap(state).get(npcEid)!;
    expect(row.obstacle!.obstacles.length).toBe(0);
  });
});
