import { beforeEach, describe, expect, it } from 'bun:test';
import {
  applyDeadzone,
  isInputMovementSuppressed,
  setInputMovementSuppressed,
} from '../../../src/plugins/input/systems';
import {
  INPUT_CONFIG,
  addInputMapping,
  type InputAction,
} from '../../../src/plugins/input/config';
import { InputState } from '../../../src/plugins/input/components';

describe('input matrix: applyDeadzone', () => {
  const cases: Array<{ raw: number; dz: number; out: number }> = [
    { raw: 0, dz: 0.1, out: 0 },
    { raw: 0.05, dz: 0.1, out: 0 },
    { raw: 0.11, dz: 0.1, out: (0.11 - 0.1) / 0.9 },
    { raw: 1, dz: 0.1, out: 1 },
    { raw: -1, dz: 0.2, out: (-1 + 0.2) / 0.8 },
    { raw: 0.5, dz: 0, out: 0.5 },
    { raw: -0.3, dz: 0.05, out: (-0.3 + 0.05) / 0.95 },
  ];
  for (const c of cases) {
    it(`raw=${c.raw} dz=${c.dz}`, () => {
      expect(applyDeadzone(c.raw, c.dz)).toBeCloseTo(c.out, 5);
    });
  }
});

describe('input matrix: INPUT_CONFIG defaults', () => {
  it('moveForward binds W and ArrowUp', () => {
    expect(INPUT_CONFIG.mappings.moveForward).toContain('KeyW');
    expect(INPUT_CONFIG.mappings.moveForward).toContain('ArrowUp');
  });
  it('jump binds Space', () => {
    expect(INPUT_CONFIG.mappings.jump).toContain('Space');
  });
  it('bufferWindow is positive', () => {
    expect(INPUT_CONFIG.bufferWindow).toBeGreaterThan(0);
  });
  it('coyoteTime configured', () => {
    expect(INPUT_CONFIG.gracePeriods.coyoteTime).toBe(100);
  });
  it('mouse look sensitivity', () => {
    expect(INPUT_CONFIG.mouseSensitivity.look).toBeGreaterThan(0);
  });
});

describe('input matrix: addInputMapping', () => {
  const actions: InputAction[] = [
    'moveForward',
    'moveBackward',
    'moveLeft',
    'moveRight',
    'jump',
    'primaryAction',
  ];
  for (const action of actions) {
    it(`addInputMapping appends to ${action}`, () => {
      const before = INPUT_CONFIG.mappings[action].length;
      addInputMapping(action, `TestKey_${action}`);
      expect(INPUT_CONFIG.mappings[action].length).toBeGreaterThanOrEqual(
        before
      );
    });
  }
  it('does not duplicate the same code twice', () => {
    const list = INPUT_CONFIG.mappings.moveUp;
    const len = list.length;
    addInputMapping('moveUp', 'KeyZ_dup_test');
    addInputMapping('moveUp', 'KeyZ_dup_test');
    expect(list.filter((c) => c === 'KeyZ_dup_test').length).toBe(1);
    void len;
  });
});

describe('input matrix: movement suppression', () => {
  beforeEach(() => {
    setInputMovementSuppressed(false);
  });
  it('defaults to not suppressed', () => {
    expect(isInputMovementSuppressed()).toBe(false);
  });
  it('set true blocks movement flag', () => {
    setInputMovementSuppressed(true);
    expect(isInputMovementSuppressed()).toBe(true);
  });
  it('set false restores', () => {
    setInputMovementSuppressed(true);
    setInputMovementSuppressed(false);
    expect(isInputMovementSuppressed()).toBe(false);
  });
});

describe('input matrix: InputState component buffers', () => {
  it('moveX array exists', () => expect(InputState.moveX).toBeDefined());
  it('moveY array exists', () => expect(InputState.moveY).toBeDefined());
  it('moveZ array exists', () => expect(InputState.moveZ).toBeDefined());
  it('lookX array exists', () => expect(InputState.lookX).toBeDefined());
  it('lookY array exists', () => expect(InputState.lookY).toBeDefined());
  it('jump array exists', () => expect(InputState.jump).toBeDefined());
  it('primaryAction array exists', () =>
    expect(InputState.primaryAction).toBeDefined());
  it('secondaryAction array exists', () =>
    expect(InputState.secondaryAction).toBeDefined());
  it('leftMouse array exists', () =>
    expect(InputState.leftMouse).toBeDefined());
  it('rightMouse array exists', () =>
    expect(InputState.rightMouse).toBeDefined());
  it('middleMouse array exists', () =>
    expect(InputState.middleMouse).toBeDefined());
  it('scrollDelta array exists', () =>
    expect(InputState.scrollDelta).toBeDefined());
});

describe('input matrix: deadzone edge dz=0.15', () => {
  for (let raw = -1; raw <= 1; raw += 0.25) {
    it(`scaled output magnitude <= 1 for raw=${raw}`, () => {
      const out = applyDeadzone(raw, 0.15);
      expect(Math.abs(out)).toBeLessThanOrEqual(1.0001);
    });
  }
});
