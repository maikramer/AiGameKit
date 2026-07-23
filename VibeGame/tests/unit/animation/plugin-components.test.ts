import { describe, expect, it } from 'bun:test';
import { State, NULL_ENTITY, defineQuery } from 'vibegame';
import {
  AnimatedCharacter,
  HasAnimator,
  AnimationPlugin,
} from '../../../src/plugins/animation';
import {
  AnimatedCharacterInitializationSystem,
  AnimatedCharacterUpdateSystem,
} from '../../../src/plugins/animation/systems';

const CHARACTER_FIELDS = [
  'headEntity',
  'torsoEntity',
  'leftArmEntity',
  'rightArmEntity',
  'leftLegEntity',
  'rightLegEntity',
  'phase',
  'jumpTime',
  'fallTime',
  'animationState',
  'stateTransition',
] as const;

describe('AnimatedCharacter component', () => {
  for (const field of CHARACTER_FIELDS) {
    it(`${field} buffer exists`, () => {
      expect(AnimatedCharacter[field]).toBeDefined();
      expect(AnimatedCharacter[field].length).toBeGreaterThan(1);
    });
  }

  it('supports ECS query', () => {
    const state = new State();
    state.registerComponent('AnimatedCharacter', AnimatedCharacter);
    const eid = state.createEntity();
    state.addComponent(eid, AnimatedCharacter);
    const q = defineQuery([AnimatedCharacter])(state.world);
    expect(q).toContain(eid);
  });
});

describe('AnimationPlugin', () => {
  it('registers AnimatedCharacter and HasAnimator', () => {
    expect(AnimationPlugin.components?.AnimatedCharacter).toBe(
      AnimatedCharacter
    );
    expect(AnimationPlugin.components?.HasAnimator).toBe(HasAnimator);
  });

  it('registers init and update systems', () => {
    expect(AnimationPlugin.systems?.[0]).toBe(
      AnimatedCharacterInitializationSystem
    );
    expect(AnimationPlugin.systems?.[1]).toBe(AnimatedCharacterUpdateSystem);
  });

  for (const field of CHARACTER_FIELDS) {
    it(`defaults include animated-character.${field}`, () => {
      const defaults = AnimationPlugin.config?.defaults?.['animated-character'];
      expect(defaults).toBeDefined();
      expect(Object.prototype.hasOwnProperty.call(defaults, field)).toBe(true);
    });
  }

  it('defaults headEntity to NULL_ENTITY', () => {
    expect(
      AnimationPlugin.config?.defaults?.['animated-character']?.headEntity
    ).toBe(NULL_ENTITY);
  });

  it('defaults animationState to 0 (IDLE)', () => {
    expect(
      AnimationPlugin.config?.defaults?.['animated-character']?.animationState
    ).toBe(0);
  });
});

describe('Animation systems metadata', () => {
  it('init system is setup group', () => {
    expect(AnimatedCharacterInitializationSystem.group).toBe('setup');
  });

  it('update system is simulation group', () => {
    expect(AnimatedCharacterUpdateSystem.group).toBe('simulation');
  });

  it('both systems expose update', () => {
    expect(typeof AnimatedCharacterInitializationSystem.update).toBe(
      'function'
    );
    expect(typeof AnimatedCharacterUpdateSystem.update).toBe('function');
  });
});

describe('HasAnimator tag', () => {
  it('is an empty marker object', () => {
    expect(Object.keys(HasAnimator)).toHaveLength(0);
  });

  it('can be registered on State via plugin', () => {
    const state = new State();
    state.registerPlugin(AnimationPlugin);
    expect(state.getComponent('HasAnimator')).toBeDefined();
  });
});
