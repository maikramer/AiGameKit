import type { MonoBehaviourContext } from 'vibegame';
import { ProjectileData } from 'vibegame/plugins/combat/components';
import { Collider, CollisionEvents, Rigidbody } from 'vibegame/plugins/physics/components';

// Minimal setup — damage handled by DamageResolutionSystem, cleanup by ProjectileCleanupSystem
export function start(ctx: MonoBehaviourContext): void {
  const eid = ctx.entity;

  ctx.state.addComponent(eid, CollisionEvents);
  CollisionEvents.activeEvents[eid] = 1;

  Collider.isSensor[eid] = 1;
  Rigidbody.gravityScale[eid] = 0;
}
