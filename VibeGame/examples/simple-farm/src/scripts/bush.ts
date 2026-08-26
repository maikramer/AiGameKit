/**
 * Berry bush — the only FOOD node on the map.
 *
 * Same shape as `tree.ts` / `rock.ts`, and again its own module so the
 * membership Set is not shared. The bush is the one node whose kind is not in
 * the engine's default enum: `food` is registered by
 * `game/resource-kinds.ts`, without which it would harvest as wood.
 */
import {
  registerInteractionTarget,
  t,
  unregisterInteractionTarget,
} from 'vibegame';
import type { MonoBehaviourContext } from 'vibegame';

const foodEntities = new Set<number>();

/** True when `eid` is one of the berry bushes spawned from the world XML. */
export function isFoodEntity(eid: number): boolean {
  return foodEntities.has(eid);
}

export function start(ctx: MonoBehaviourContext): void {
  foodEntities.add(ctx.entity);
  registerInteractionTarget(ctx.state, ctx.entity, {
    label: t(ctx.state, 'interact.pick'),
    key: 'J',
  });
}

export function onDestroy(ctx: MonoBehaviourContext): void {
  foodEntities.delete(ctx.entity);
  unregisterInteractionTarget(ctx.state, ctx.entity);
}
