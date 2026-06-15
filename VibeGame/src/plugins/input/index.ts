export { InputState, GamepadInput } from './components';
export { INPUT_CONFIG, addInputMapping } from './config';
export type { InputAction } from './config';
export { InputPlugin } from './plugin';
export {
  applyDeadzone,
  setInputMovementSuppressed,
  isInputMovementSuppressed,
} from './systems';
export {
  consumeJump,
  consumePrimary,
  consumeSecondary,
  handleWheel,
  handleMouseMove,
  handleMouseDown,
  handleMouseUp,
  isKeyDown,
  setTargetCanvas,
  setFocusedCanvas,
  getFocusedCanvas,
} from './utils';
