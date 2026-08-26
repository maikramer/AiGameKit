/**
 * Choppable tree.
 *
 * The script does two small things: it shows the "[J] Chop" prompt in range and
 * it records the entity as a wood node. The swing, the particles and the trunk
 * fall all belong to the `destructible` attribute in the XML; the loot is
 * credited once, globally, in `game/harvest.ts` — a per-entity handler would
 * re-register on every instance for no gain.
 *
 * One file per node type is deliberate: the membership Set below is module
 * state, so sharing a module between trees and rocks would merge the two sets
 * (same trap as simple-rpg's POI scripts).
 */
import {
  registerInteractionTarget,
  t,
  unregisterInteractionTarget,
} from 'vibegame';
import type { MonoBehaviourContext } from 'vibegame';

const woodEntities = new Set<number>();

/** True when `eid` is one of the trees spawned from the world XML. */
export function isWoodEntity(eid: number): boolean {
  return woodEntities.has(eid);
}

export function start(ctx: MonoBehaviourContext): void {
  woodEntities.add(ctx.entity);
  registerInteractionTarget(ctx.state, ctx.entity, {
    label: t(ctx.state, 'interact.chop'),
    key: 'J',
  });
}

export function onDestroy(ctx: MonoBehaviourContext): void {
  woodEntities.delete(ctx.entity);
  unregisterInteractionTarget(ctx.state, ctx.entity);
}
