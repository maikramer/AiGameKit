import type { MonoBehaviourContext } from 'vibegame';
import { ProjectileData } from '../../../../src/plugins/combat/components.ts';
import {
  Collider,
  CollisionEvents,
  Rigidbody,
} from '../../../../src/plugins/physics/components.ts';

// Minimal setup — damage handled by DamageResolutionSystem, cleanup by ProjectileCleanupSystem
export function start(ctx: MonoBehaviourContext): void {
  const eid = ctx.entity;

  ctx.state.addComponent(eid, CollisionEvents);
  CollisionEvents.activeEvents[eid] = 1;

  Collider.isSensor[eid] = 1;
  Rigidbody.gravityScale[eid] = 0;
}
