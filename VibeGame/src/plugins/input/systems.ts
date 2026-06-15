import { defineQuery, type System } from '../../core';
import { InputState, GamepadInput } from './components';
import {
  cleanupEventListeners,
  clearAllInput,
  getFocusedCanvas,
  resetFrameDeltas,
  setupEventListeners,
  updateInputState,
} from './utils';
import { getRenderingContext } from '../rendering/utils';

const inputStateQuery = defineQuery([InputState]);

/**
 * When a modal UI (e.g. a shop) is open, gameplay movement must stop even though
 * the keys are physically down. Suppression is applied at the input source so it
 * can't be overwritten: the player movement runs in the `fixed` group and reads
 * what the input systems leave in `InputState`, and gating here zeroes movement
 * for every consumer regardless of system order.
 */
let movementSuppressed = false;

export function setInputMovementSuppressed(value: boolean): void {
  movementSuppressed = value;
}

export function isInputMovementSuppressed(): boolean {
  return movementSuppressed;
}

function zeroMovement(eid: number): void {
  InputState.moveX[eid] = 0;
  InputState.moveY[eid] = 0;
  InputState.moveZ[eid] = 0;
  InputState.lookX[eid] = 0;
  InputState.lookY[eid] = 0;
  InputState.jump[eid] = 0;
}

export const InputSystem: System = {
  group: 'simulation',

  setup: () => {
    setupEventListeners();
    clearAllInput();
  },

  update: (state) => {
    const focusedCanvas = getFocusedCanvas();
    const context = getRenderingContext(state);

    // Keyboard input is global; mouse-related input requires canvas focus.
    const canvasActive =
      focusedCanvas && context.canvas && context.canvas === focusedCanvas;

    const entities = inputStateQuery(state.world);

    for (const eid of entities) {
      updateInputState(eid, !canvasActive);
      if (movementSuppressed) zeroMovement(eid);
    }

    resetFrameDeltas();
  },

  dispose: () => {
    cleanupEventListeners();
    clearAllInput();
  },
};

export const applyDeadzone = (raw: number, dz: number): number => {
  if (Math.abs(raw) < dz) return 0;
  return (raw - Math.sign(raw) * dz) / (1 - dz);
};

export const GamepadInputSystem: System = {
  group: 'simulation',

  update: (state) => {
    const gamepads: (Gamepad | null)[] =
      typeof navigator !== 'undefined' && navigator.getGamepads
        ? Array.from(navigator.getGamepads())
        : [];

    const entities = inputStateQuery(state.world);

    for (const eid of entities) {
      let gp: Gamepad | null = null;
      for (const g of gamepads) {
        if (g) {
          gp = g;
          break;
        }
      }

      if (!gp) {
        GamepadInput.connected[eid] = 0;
        continue;
      }

      GamepadInput.connected[eid] = 1;
      if (movementSuppressed) {
        zeroMovement(eid);
        continue;
      }
      const dz = GamepadInput.deadzone[eid];

      GamepadInput.leftStickX[eid] = applyDeadzone(gp.axes[0] ?? 0, dz);
      GamepadInput.leftStickY[eid] = applyDeadzone(gp.axes[1] ?? 0, dz);
      GamepadInput.rightStickX[eid] = applyDeadzone(gp.axes[2] ?? 0, dz);
      GamepadInput.rightStickY[eid] = applyDeadzone(gp.axes[3] ?? 0, dz);

      GamepadInput.buttonA[eid] = gp.buttons[0]?.pressed ? 1 : 0;
      GamepadInput.buttonB[eid] = gp.buttons[1]?.pressed ? 1 : 0;
      GamepadInput.buttonX[eid] = gp.buttons[2]?.pressed ? 1 : 0;
      GamepadInput.buttonY[eid] = gp.buttons[3]?.pressed ? 1 : 0;
      GamepadInput.leftBumper[eid] = gp.buttons[4]?.pressed ? 1 : 0;
      GamepadInput.rightBumper[eid] = gp.buttons[5]?.pressed ? 1 : 0;
      GamepadInput.leftTrigger[eid] = gp.buttons[6]?.value ?? 0;
      GamepadInput.rightTrigger[eid] = gp.buttons[7]?.value ?? 0;

      // Merge into InputState (keyboard priority — only write if not already set)
      if (InputState.moveX[eid] === 0)
        InputState.moveX[eid] = GamepadInput.leftStickX[eid];
      if (InputState.moveZ[eid] === 0)
        InputState.moveZ[eid] = -GamepadInput.leftStickY[eid];
      if (InputState.lookX[eid] === 0)
        InputState.lookX[eid] = GamepadInput.rightStickX[eid];
      if (InputState.lookY[eid] === 0)
        InputState.lookY[eid] = GamepadInput.rightStickY[eid];
      if (InputState.jump[eid] === 0)
        InputState.jump[eid] = GamepadInput.buttonA[eid];
    }
  },
};
