import { describe, expect, it } from 'bun:test';
import {
  animatorMap,
  HandoffAnimatorTickSystem,
} from '../../../src/plugins/scene-manifest/loader';

describe('HandoffAnimatorTickSystem and animatorMap (T8)', () => {
  it('animatorMap is exported as a Map', () => {
    expect(animatorMap).toBeInstanceOf(Map);
  });

  it('animatorMap starts empty', () => {
    expect(animatorMap.size).toBe(0);
  });

  it('HandoffAnimatorTickSystem is in draw group', () => {
    expect(HandoffAnimatorTickSystem.group).toBe('draw');
  });

  it('HandoffAnimatorTickSystem has update method', () => {
    expect(typeof HandoffAnimatorTickSystem.update).toBe('function');
  });
});
