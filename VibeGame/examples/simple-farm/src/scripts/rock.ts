/**
 * Breakable rock. Mirror of `tree.ts`, deliberately a separate module so the
 * membership Set stays per node type.
 *
 * `break-style: shatter` in the XML gives the burst instead of the tree's fall;
 * the loot path is the same global handler in `game/harvest.ts`.
 */
import {
  registerInteractionTarget,
  t,
  unregisterInteractionTarget,
} from 'vibegame';
import type { MonoBehaviourContext } from 'vibegame';

const stoneEntities = new Set<number>();

/** True when `eid` is one of the rocks spawned from the world XML. */
export function isStoneEntity(eid: number): boolean {
  return stoneEntities.has(eid);
}

export function start(ctx: MonoBehaviourContext): void {
  stoneEntities.add(ctx.entity);
  registerInteractionTarget(ctx.state, ctx.entity, {
    label: t(ctx.state, 'interact.mine'),
    key: 'J',
  });
}

export function onDestroy(ctx: MonoBehaviourContext): void {
  stoneEntities.delete(ctx.entity);
  unregisterInteractionTarget(ctx.state, ctx.entity);
}
