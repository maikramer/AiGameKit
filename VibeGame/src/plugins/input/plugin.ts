import type { Plugin } from '../../core';
import { InputState, GamepadInput } from './components';
import { InputSystem, GamepadInputSystem } from './systems';

export const InputPlugin: Plugin = {
  systems: [InputSystem, GamepadInputSystem],
  components: {
    InputState,
    GamepadInput,
  },
};
