import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { State } from 'vibegame';
import {
  InputPlugin,
  InputState,
  GamepadInput,
  applyDeadzone,
} from 'vibegame/input';
import { setRenderingCanvas } from 'vibegame/rendering';
import {
  setTargetCanvas,
  setFocusedCanvas,
} from '../../../src/plugins/input/utils';

function makeMockGamepad(overrides: Partial<Gamepad> = {}): Gamepad {
  return {
    id: 'mock-gamepad',
    index: 0,
    connected: true,
    timestamp: performance.now(),
    mapping: 'standard',
    axes: [0, 0, 0, 0],
    buttons: Array.from({ length: 17 }, () => ({
      pressed: false,
      touched: false,
      value: 0,
    })),
    ...overrides,
  } as Gamepad;
}

describe('GamepadInput', () => {
  let state: State;
  let entity: number;

  beforeEach(() => {
    const dom = new JSDOM(
      '<!DOCTYPE html><html><body><canvas id="game-canvas"></canvas></body></html>'
    );
    global.window = dom.window as any;
    global.document = dom.window.document;
    global.performance = {
      now: () => Date.now(),
    } as any;
    global.navigator = {
      getGamepads: () => [],
    } as any;

    state = new State();
    state.registerPlugin(InputPlugin);
    entity = state.createEntity();
    state.addComponent(entity, InputState);
    state.addComponent(entity, GamepadInput);

    const canvas = global.document.getElementById(
      'game-canvas'
    ) as HTMLCanvasElement;
    setTargetCanvas(canvas);
    setFocusedCanvas(canvas);
    setRenderingCanvas(state, canvas);

    GamepadInput.deadzone[entity] = 0.15;

    const allSystems = Array.from(state.systems);
    for (const s of allSystems) {
      s.setup?.(state);
    }
  });

  describe('applyDeadzone', () => {
    it('should return 0 for values below threshold', () => {
      expect(applyDeadzone(0.1, 0.15)).toBe(0);
      expect(applyDeadzone(-0.1, 0.15)).toBe(0);
      expect(applyDeadzone(0.0, 0.15)).toBe(0);
    });

    it('should normalize values above threshold', () => {
      const result = applyDeadzone(0.5, 0.15);
      expect(result).toBeCloseTo((0.5 - 0.15) / (1 - 0.15), 5);
    });

    it('should handle negative values above threshold', () => {
      const result = applyDeadzone(-0.5, 0.15);
      expect(result).toBeCloseTo((-0.5 + 0.15) / (1 - 0.15), 5);
    });

    it('should return ~1 for full positive input', () => {
      const result = applyDeadzone(1.0, 0.15);
      expect(result).toBeCloseTo(1.0, 5);
    });

    it('should return ~-1 for full negative input', () => {
      const result = applyDeadzone(-1.0, 0.15);
      expect(result).toBeCloseTo(-1.0, 5);
    });

    it('should return 0 at exactly the deadzone boundary', () => {
      expect(applyDeadzone(0.15, 0.15)).toBe(0);
      expect(applyDeadzone(-0.15, 0.15)).toBe(0);
    });

    it('should handle zero deadzone', () => {
      expect(applyDeadzone(0.5, 0)).toBeCloseTo(0.5, 5);
      expect(applyDeadzone(-0.5, 0)).toBeCloseTo(-0.5, 5);
    });
  });

  describe('GamepadInput Component', () => {
    it('should initialize with default values', () => {
      const freshEntity = state.createEntity();
      state.addComponent(freshEntity, GamepadInput);

      expect(GamepadInput.connected[freshEntity]).toBe(0);
      expect(GamepadInput.deadzone[freshEntity]).toBe(0);
      expect(GamepadInput.leftStickX[freshEntity]).toBe(0);
      expect(GamepadInput.leftStickY[freshEntity]).toBe(0);
      expect(GamepadInput.rightStickX[freshEntity]).toBe(0);
      expect(GamepadInput.rightStickY[freshEntity]).toBe(0);
      expect(GamepadInput.buttonA[freshEntity]).toBe(0);
      expect(GamepadInput.buttonB[freshEntity]).toBe(0);
      expect(GamepadInput.buttonX[freshEntity]).toBe(0);
      expect(GamepadInput.buttonY[freshEntity]).toBe(0);
      expect(GamepadInput.leftBumper[freshEntity]).toBe(0);
      expect(GamepadInput.rightBumper[freshEntity]).toBe(0);
      expect(GamepadInput.leftTrigger[freshEntity]).toBe(0);
      expect(GamepadInput.rightTrigger[freshEntity]).toBe(0);
    });
  });

  describe('Connected State', () => {
    it('should set connected to 0 when no gamepad available', () => {
      global.navigator.getGamepads = () => [];

      const gpSystem = Array.from(state.systems).find(
        (s) => s !== Array.from(state.systems)[0]
      );
      gpSystem?.update?.(state);

      expect(GamepadInput.connected[entity]).toBe(0);
    });

    it('should set connected to 1 when a gamepad is available', () => {
      const mockGamepad = makeMockGamepad();
      global.navigator.getGamepads = () => [mockGamepad];

      const gpSystem = Array.from(state.systems).find(
        (s) => s !== Array.from(state.systems)[0]
      );
      gpSystem?.update?.(state);

      expect(GamepadInput.connected[entity]).toBe(1);
    });
  });

  describe('Stick Input', () => {
    it('should read left stick axes with deadzone applied', () => {
      const mockGamepad = makeMockGamepad({
        axes: [0.5, -0.7, 0, 0],
      });
      global.navigator.getGamepads = () => [mockGamepad];

      const gpSystem = Array.from(state.systems).find(
        (s) => s !== Array.from(state.systems)[0]
      );
      gpSystem?.update?.(state);

      const dz = 0.15;
      expect(GamepadInput.leftStickX[entity]).toBeCloseTo(
        applyDeadzone(0.5, dz),
        5
      );
      expect(GamepadInput.leftStickY[entity]).toBeCloseTo(
        applyDeadzone(-0.7, dz),
        5
      );
    });

    it('should read right stick axes with deadzone applied', () => {
      const mockGamepad = makeMockGamepad({
        axes: [0, 0, 0.3, -0.9],
      });
      global.navigator.getGamepads = () => [mockGamepad];

      const gpSystem = Array.from(state.systems).find(
        (s) => s !== Array.from(state.systems)[0]
      );
      gpSystem?.update?.(state);

      const dz = 0.15;
      expect(GamepadInput.rightStickX[entity]).toBeCloseTo(
        applyDeadzone(0.3, dz),
        5
      );
      expect(GamepadInput.rightStickY[entity]).toBeCloseTo(
        applyDeadzone(-0.9, dz),
        5
      );
    });

    it('should zero out axes within deadzone', () => {
      const mockGamepad = makeMockGamepad({
        axes: [0.05, -0.1, 0.12, 0.14],
      });
      global.navigator.getGamepads = () => [mockGamepad];

      const gpSystem = Array.from(state.systems).find(
        (s) => s !== Array.from(state.systems)[0]
      );
      gpSystem?.update?.(state);

      expect(GamepadInput.leftStickX[entity]).toBe(0);
      expect(GamepadInput.leftStickY[entity]).toBe(0);
      expect(GamepadInput.rightStickX[entity]).toBe(0);
      expect(GamepadInput.rightStickY[entity]).toBe(0);
    });
  });

  describe('Button Input', () => {
    it('should read face button states', () => {
      const buttons = Array.from({ length: 17 }, (_, i) => ({
        pressed: i < 4,
        touched: i < 4,
        value: i < 4 ? 1 : 0,
      }));
      const mockGamepad = makeMockGamepad({ buttons });
      global.navigator.getGamepads = () => [mockGamepad];

      const gpSystem = Array.from(state.systems).find(
        (s) => s !== Array.from(state.systems)[0]
      );
      gpSystem?.update?.(state);

      expect(GamepadInput.buttonA[entity]).toBe(1);
      expect(GamepadInput.buttonB[entity]).toBe(1);
      expect(GamepadInput.buttonX[entity]).toBe(1);
      expect(GamepadInput.buttonY[entity]).toBe(1);
    });

    it('should read bumper states', () => {
      const buttons = Array.from({ length: 17 }, (_, i) => ({
        pressed: i === 4 || i === 5,
        touched: i === 4 || i === 5,
        value: i === 4 || i === 5 ? 1 : 0,
      }));
      const mockGamepad = makeMockGamepad({ buttons });
      global.navigator.getGamepads = () => [mockGamepad];

      const gpSystem = Array.from(state.systems).find(
        (s) => s !== Array.from(state.systems)[0]
      );
      gpSystem?.update?.(state);

      expect(GamepadInput.leftBumper[entity]).toBe(1);
      expect(GamepadInput.rightBumper[entity]).toBe(1);
    });

    it('should read trigger values', () => {
      const buttons = Array.from({ length: 17 }, (_, i) => ({
        pressed: false,
        touched: false,
        value: i === 6 ? 0.75 : i === 7 ? 0.5 : 0,
      }));
      const mockGamepad = makeMockGamepad({ buttons });
      global.navigator.getGamepads = () => [mockGamepad];

      const gpSystem = Array.from(state.systems).find(
        (s) => s !== Array.from(state.systems)[0]
      );
      gpSystem?.update?.(state);

      expect(GamepadInput.leftTrigger[entity]).toBeCloseTo(0.75, 5);
      expect(GamepadInput.rightTrigger[entity]).toBeCloseTo(0.5, 5);
    });

    it('should show 0 for unpressed buttons', () => {
      const mockGamepad = makeMockGamepad();
      global.navigator.getGamepads = () => [mockGamepad];

      const gpSystem = Array.from(state.systems).find(
        (s) => s !== Array.from(state.systems)[0]
      );
      gpSystem?.update?.(state);

      expect(GamepadInput.buttonA[entity]).toBe(0);
      expect(GamepadInput.buttonB[entity]).toBe(0);
      expect(GamepadInput.buttonX[entity]).toBe(0);
      expect(GamepadInput.buttonY[entity]).toBe(0);
      expect(GamepadInput.leftBumper[entity]).toBe(0);
      expect(GamepadInput.rightBumper[entity]).toBe(0);
      expect(GamepadInput.leftTrigger[entity]).toBe(0);
      expect(GamepadInput.rightTrigger[entity]).toBe(0);
    });
  });

  describe('Keyboard Priority', () => {
    it('should not overwrite keyboard moveX with gamepad', () => {
      InputState.moveX[entity] = 1;

      const mockGamepad = makeMockGamepad({
        axes: [0.8, 0, 0, 0],
      });
      global.navigator.getGamepads = () => [mockGamepad];

      const gpSystem = Array.from(state.systems).find(
        (s) => s !== Array.from(state.systems)[0]
      );
      gpSystem?.update?.(state);

      expect(InputState.moveX[entity]).toBe(1);
    });

    it('should write gamepad moveX when keyboard is 0', () => {
      InputState.moveX[entity] = 0;

      const mockGamepad = makeMockGamepad({
        axes: [0.8, 0, 0, 0],
      });
      global.navigator.getGamepads = () => [mockGamepad];

      const gpSystem = Array.from(state.systems).find(
        (s) => s !== Array.from(state.systems)[0]
      );
      gpSystem?.update?.(state);

      const expected = applyDeadzone(0.8, 0.15);
      expect(InputState.moveX[entity]).toBeCloseTo(expected, 5);
    });

    it('should not overwrite keyboard jump with gamepad buttonA', () => {
      InputState.jump[entity] = 1;

      const buttons = Array.from({ length: 17 }, (_, i) => ({
        pressed: i === 0,
        touched: i === 0,
        value: i === 0 ? 1 : 0,
      }));
      const mockGamepad = makeMockGamepad({ buttons });
      global.navigator.getGamepads = () => [mockGamepad];

      const gpSystem = Array.from(state.systems).find(
        (s) => s !== Array.from(state.systems)[0]
      );
      gpSystem?.update?.(state);

      expect(InputState.jump[entity]).toBe(1);
      expect(GamepadInput.buttonA[entity]).toBe(1);
    });

    it('should write gamepad jump when keyboard is 0', () => {
      InputState.jump[entity] = 0;

      const buttons = Array.from({ length: 17 }, (_, i) => ({
        pressed: i === 0,
        touched: i === 0,
        value: i === 0 ? 1 : 0,
      }));
      const mockGamepad = makeMockGamepad({ buttons });
      global.navigator.getGamepads = () => [mockGamepad];

      const gpSystem = Array.from(state.systems).find(
        (s) => s !== Array.from(state.systems)[0]
      );
      gpSystem?.update?.(state);

      expect(InputState.jump[entity]).toBe(1);
    });

    it('should invert Y on left stick for moveZ', () => {
      InputState.moveZ[entity] = 0;

      const mockGamepad = makeMockGamepad({
        axes: [0, 0.8, 0, 0],
      });
      global.navigator.getGamepads = () => [mockGamepad];

      const gpSystem = Array.from(state.systems).find(
        (s) => s !== Array.from(state.systems)[0]
      );
      gpSystem?.update?.(state);

      const expected = -applyDeadzone(0.8, 0.15);
      expect(InputState.moveZ[entity]).toBeCloseTo(expected, 5);
    });

    it('should merge look axes when keyboard look is 0', () => {
      InputState.lookX[entity] = 0;
      InputState.lookY[entity] = 0;

      const mockGamepad = makeMockGamepad({
        axes: [0, 0, 0.5, -0.3],
      });
      global.navigator.getGamepads = () => [mockGamepad];

      const gpSystem = Array.from(state.systems).find(
        (s) => s !== Array.from(state.systems)[0]
      );
      gpSystem?.update?.(state);

      expect(InputState.lookX[entity]).toBeCloseTo(applyDeadzone(0.5, 0.15), 5);
      expect(InputState.lookY[entity]).toBeCloseTo(
        applyDeadzone(-0.3, 0.15),
        5
      );
    });

    it('should not overwrite keyboard look with gamepad', () => {
      InputState.lookX[entity] = 5;
      InputState.lookY[entity] = -3;

      const mockGamepad = makeMockGamepad({
        axes: [0, 0, 0.5, -0.3],
      });
      global.navigator.getGamepads = () => [mockGamepad];

      const gpSystem = Array.from(state.systems).find(
        (s) => s !== Array.from(state.systems)[0]
      );
      gpSystem?.update?.(state);

      expect(InputState.lookX[entity]).toBe(5);
      expect(InputState.lookY[entity]).toBe(-3);
    });
  });

  describe('Plugin Registration', () => {
    it('should register GamepadInput component via plugin', () => {
      const freshState = new State();
      freshState.registerPlugin(InputPlugin);
      const e = freshState.createEntity();
      freshState.addComponent(e, GamepadInput);

      expect(freshState.hasComponent(e, GamepadInput)).toBe(true);
    });
  });
});
