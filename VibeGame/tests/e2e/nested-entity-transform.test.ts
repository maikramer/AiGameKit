import { beforeEach, describe, expect, it } from 'bun:test';
import { State, TIME_CONSTANTS } from 'vibegame';
import {
  createHeadlessState,
  parseWorldXml,
  queryEntities,
} from 'vibegame/cli';
import { DefaultPlugins } from 'vibegame/defaults';
import { Parent, Transform, WorldTransform } from 'vibegame/transforms';

describe('E2E: Nested Entity Transform Hierarchy', () => {
  let state: State;

  beforeEach(async () => {
    state = createHeadlessState({ plugins: DefaultPlugins });
    await state.initializePlugins();
  });

  it('should establish parent-child relationship for nested entities', () => {
    const xml = `
      <GameObject transform="pos: 0 0 0">
        <GameObject transform="pos: 2 0 0"></GameObject>
      </GameObject>
    `;

    parseWorldXml(state, xml);

    const entities = queryEntities(state, 'transform');
    expect(entities.length).toBe(2);

    const childEntity = entities.find((e) => state.hasComponent(e, Parent));
    expect(childEntity).toBeDefined();

    const parentEntity = entities.find((e) => !state.hasComponent(e, Parent));
    expect(parentEntity).toBeDefined();

    if (childEntity && parentEntity) {
      expect(Parent.entity[childEntity]).toBe(parentEntity);
    }
  });

  it('should handle multi-level nested entities', () => {
    const initialEntityCount = queryEntities(state, 'transform').length;

    const xml = `
      <GameObject transform="pos: 0 0 0">
        <GameObject transform="pos: 1 0 0">
          <GameObject transform="pos: 1 0 0"></GameObject>
        </GameObject>
      </GameObject>
    `;

    parseWorldXml(state, xml);

    const allEntities = queryEntities(state, 'transform');
    const newEntities = allEntities.slice(initialEntityCount);
    expect(newEntities.length).toBe(3);

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    const worldTransforms = newEntities
      .filter((e) => state.hasComponent(e, WorldTransform))
      .map((e) => ({
        entity: e,
        worldX: WorldTransform.posX[e],
        localX: Transform.posX[e],
        hasParent: state.hasComponent(e, Parent),
      }))
      .sort((a, b) => a.worldX - b.worldX);

    expect(worldTransforms.length).toBe(3);
    expect(worldTransforms[0].worldX).toBe(0);
    expect(worldTransforms[1].worldX).toBe(1);
    expect(worldTransforms[2].worldX).toBe(2);
  });

  it('should rotate child with parent when parent rotates', () => {
    const xml = `
      <GameObject name="parent" transform="pos: 0 0 0">
        <GameObject transform="pos: 2 0 0"></GameObject>
      </GameObject>
      <Tween target="parent" attr="rotation" from="0 0 0" to="0 180 0" duration="1"></Tween>
    `;

    parseWorldXml(state, xml);

    const entities = queryEntities(state, 'transform');
    const childEntity = entities.find((e) => state.hasComponent(e, Parent));

    expect(childEntity).toBeDefined();
    if (!childEntity) return;

    // Drive the tween to completion in small steps. A single large step (e.g.
    // 0.5s) is clamped by the scheduler's max-fixed-steps-per-frame guard, so
    // the fixed-group tween would not advance the full delta. Throughout the
    // rotation the child must stay on its orbit (radius 2 around the parent).
    const radius = () =>
      Math.hypot(WorldTransform.posX[childEntity], WorldTransform.posZ[childEntity]);

    let progressed = false;
    for (let t = 0; t < 1.3; t += 0.05) {
      state.step(0.05);
      expect(radius()).toBeCloseTo(2, 1);
      if (WorldTransform.posZ[childEntity] < -1) progressed = true;
    }

    // Passed through the -Z quadrant mid-rotation (child rotated with parent).
    expect(progressed).toBe(true);
    // Tween finished at 180°: child (2,0,0) → (-2,0,0).
    expect(WorldTransform.posX[childEntity]).toBeCloseTo(-2, 1);
    expect(WorldTransform.posZ[childEntity]).toBeCloseTo(0, 1);
  });
});
