export { FloatingText } from './components';
export { FloatingTextPlugin } from './plugin';
export {
  ScreenFloatPool,
  disposeScreenFloatPool,
  getFloatingScreenPoolSize,
  getScreenFloatPool,
} from './screen-pool';
export {
  FloatingTextScreenUpdateSystem,
  FloatingTextUpdateSystem,
} from './systems';
export { claimStackSlot, clearFloatingTextStacks } from './stacking';
export type { StackEntry, StackSlot } from './stacking';
export { spawnFloatingText, spawnFloatingTextScreen } from './utils';
export type {
  FloatingTextOptions,
  FloatingTextSpace,
  ScreenFloatingTextOptions,
} from './utils';
