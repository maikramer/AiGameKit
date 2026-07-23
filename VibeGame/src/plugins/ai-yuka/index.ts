export { YukaAiPlugin } from './plugin';
export { YukaAgentSystem } from './systems';
export { npcRecipe } from './recipes';
export {
  YukaAgentComponent,
  YUKA_BEHAVIOR_NONE,
  YUKA_BEHAVIOR_SEEK,
  YUKA_BEHAVIOR_ARRIVE,
  YUKA_BEHAVIOR_PURSUIT,
  YUKA_BEHAVIOR_EVADE,
  YUKA_BEHAVIOR_FLEE,
  YUKA_BEHAVIOR_WANDER,
  YUKA_BEHAVIOR_SEPARATION,
  YUKA_BEHAVIOR_FLOCK,
  YUKA_BEHAVIOR_HOLD_RING,
} from './components';
export type { YukaBehaviorMask } from './components';
export { hasLineOfSight, DEFAULT_VISION_BLOCK_LAYERS } from './perception';
export { decide, applyDecision } from './decision';
export type {
  CreatureDecisionProfile,
  DecisionInput,
  DecisionResult,
} from './decision';
export { createYukaRuntime, TargetProxy } from './vehicle-bridge';
export {
  getYukaRuntime,
  getYukaRuntimeMap,
  deleteYukaRuntime,
} from './context';
export type { YukaRuntime, SteeringBehaviorId } from './context';
