const defaultMappings = {
  moveForward: ['KeyW', 'ArrowUp'],
  moveBackward: ['KeyS', 'ArrowDown'],
  moveLeft: ['KeyA', 'ArrowLeft'],
  moveRight: ['KeyD', 'ArrowRight'],
  moveUp: ['KeyE'],
  moveDown: ['KeyQ'],
  jump: ['Space'],
  primaryAction: ['MouseLeft'],
  secondaryAction: ['MouseRight'],
};

export type InputAction = keyof typeof defaultMappings;

export const INPUT_CONFIG = {
  mappings: defaultMappings as Record<InputAction, string[]>,

  bufferWindow: 100,

  gracePeriods: {
    coyoteTime: 100,
    landingBuffer: 50,
  },

  mouseSensitivity: {
    look: 0.5,
    scroll: 0.01,
  },
};

/**
 * Append extra KeyboardEvent codes (e.g. 'KeyJ') to an action's bindings.
 * Lets a game bind keyboard keys to primaryAction/secondaryAction, which
 * default to mouse buttons only.
 */
export function addInputMapping(action: InputAction, ...codes: string[]): void {
  const list = INPUT_CONFIG.mappings[action];
  for (const code of codes) {
    if (!list.includes(code)) list.push(code);
  }
}
