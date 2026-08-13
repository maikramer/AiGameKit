import type { MonoBehaviourContext } from 'vibegame';
import {
  registerInteractionTarget,
  unregisterInteractionTarget,
} from 'vibegame';

const woodEntities = new Set<number>();

export function isWoodEntity(eid: number): boolean {
  return woodEntities.has(eid);
}

export function start(ctx: MonoBehaviourContext): void {
  woodEntities.add(ctx.entity);
  // "[J] Chop" prompt when the player is in harvest range.
  registerInteractionTarget(ctx.state, ctx.entity, {
    label: 'Cortar',
    key: 'J',
  });
}

export function onDestroy(ctx: MonoBehaviourContext): void {
  woodEntities.delete(ctx.entity);
  unregisterInteractionTarget(ctx.state, ctx.entity);
}
