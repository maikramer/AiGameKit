import {
  defineComponent,
  filled,
  F32,
  I32,
  U8,
} from '../../core/ecs/component-storage';

/** Flag component placed on an entity to request navmesh generation.
 * The presence of an enabled NavMeshSurface entity triggers the init system. */
export const NavMeshSurface = defineComponent({
  enabled: filled(U8, 1),
  generated: U8,
});

export const NavMeshWalkable = defineComponent({
  enabled: filled(U8, 1),
});

/** Agent component. `agentIndex === -1` means no Crowd agent has been created yet. */
export const NavMeshAgent = defineComponent({
  agentIndex: filled(I32, -1),
  speed: F32,
  radius: filled(F32, 0.4),
  height: filled(F32, 1.0),
  targetX: F32,
  targetY: F32,
  targetZ: F32,
  hasTarget: U8,
  enabled: filled(U8, 1),
  /**
   * When 1, ``NavMeshAgentSystem`` writes planar yaw from crowd velocity.
   * Set to 0 when presentation/AI owns facing (melee creatures: chase=velocity,
   * attack=face target) so two writers do not fight over ``Transform.eulerY``.
   */
  faceVelocity: filled(U8, 1),
  /**
   * When 1, the crowd agent is kept alive but **frozen**: the readback loop
   * skips writing its position/velocity back into Transform, and no move target
   * is issued. Used by the melee lunge so the dash (direct Transform writes)
   * is not overwritten by the crowd, without destroying + re-creating the agent
   * (which snapped position on re-add). Distinct from `enabled` (0 = the system
   * tears the agent down entirely).
   */
  suspended: U8,
});
