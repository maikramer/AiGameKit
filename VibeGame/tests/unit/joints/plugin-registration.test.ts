import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { JointsPlugin } from '../../../src/plugins/joints/plugin';
import { Joint } from '../../../src/plugins/joints/components';

describe('JointsPlugin Registration', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(JointsPlugin);
  });

  it('should have a recipe named "joint" with components ["physicsJoint"]', () => {
    expect(JointsPlugin.recipes!).toHaveLength(1);
    expect(JointsPlugin.recipes![0].name).toBe('joint');
    expect(JointsPlugin.recipes![0].components).toEqual(['physicsJoint']);
  });

  it('should register the physicsJoint component', () => {
    const entity = state.createEntity();
    state.addComponent(entity, Joint);
    expect(state.hasComponent(entity, Joint)).toBe(true);
  });

  it('should register the joint recipe', () => {
    const recipe = state.getRecipe('joint');
    expect(recipe).toBeDefined();
    expect(recipe?.components).toContain('physicsJoint');
  });

  it('should have two systems registered (JointCleanupSystem + JointCreateSystem)', () => {
    expect(JointsPlugin.systems).toHaveLength(2);
  });

  it('should have config.defaults for physicsJoint', () => {
    const defaults = JointsPlugin.config!.defaults!.physicsJoint;
    expect(defaults).toBeDefined();
    expect(defaults.bodyA).toBe(0);
    expect(defaults.bodyB).toBe(0);
    expect(defaults.jointType).toBe(1);
    expect(defaults.anchorAX).toBe(0);
    expect(defaults.anchorAY).toBe(0);
    expect(defaults.anchorAZ).toBe(0);
    expect(defaults.anchorBX).toBe(0);
    expect(defaults.anchorBY).toBe(0);
    expect(defaults.anchorBZ).toBe(0);
    expect(defaults.axisX).toBe(0);
    expect(defaults.axisY).toBe(1);
    expect(defaults.axisZ).toBe(0);
    expect(defaults.limitsMin).toBe(0);
    expect(defaults.limitsMax).toBeCloseTo(6.28);
    expect(defaults.motorSpeed).toBe(0);
    expect(defaults.motorMaxForce).toBe(0);
    expect(defaults.ropeLength).toBe(1);
    expect(defaults.springStiffness).toBe(10);
    expect(defaults.springDamping).toBe(1);
    expect(defaults.created).toBe(0);
  });

  it('should have config.enums for physicsJoint type', () => {
    const enums = JointsPlugin.config!.enums!.physicsJoint;
    expect(enums).toBeDefined();
    expect(enums.type).toBeDefined();
    expect(enums.type.fixed).toBe(0);
    expect(enums.type.revolute).toBe(1);
    expect(enums.type.prismatic).toBe(2);
    expect(enums.type.spherical).toBe(3);
    expect(enums.type.rope).toBe(4);
    expect(enums.type.spring).toBe(5);
  });
});
