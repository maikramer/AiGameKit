// Turns a broken tree or rock into inventory. One global subscription handles
// every node in the world — `onDestructibleDestroyed` is state-scoped, not
// per-entity, so subscribing inside the entity scripts would add one listener
// per instance (thousands of them) for identical behaviour.
//
// The amount comes from `harvest()` rather than a hardcoded 1: that respects the
// `yield` declared per species in the world XML and starts the `respawn` timer,
// so a chopped tree comes back instead of thinning the forest permanently.

import {
  Parent,
  ResourceNode,
  addItem,
  defineQuery,
  getResourceNodeKind,
  harvest,
  onDestructibleDestroyed,
  spawnFloatingText,
  t,
} from 'vibegame';
import type { State } from 'vibegame';
import { isWoodEntity } from '../scripts/tree';
import { isStoneEntity } from '../scripts/rock';
import { isFoodEntity } from '../scripts/bush';

/** Item ids in `public/data/items.yaml` that harvested resources land in. */
const ITEM_BY_KIND: Record<string, string> = {
  wood: 'wood',
  stone: 'stone',
  ore: 'ore',
  food: 'berry',
};

const COLOR_BY_KIND: Record<string, string> = {
  wood: '#c8a35a',
  stone: '#d8d8d2',
  ore: '#b98a4a',
  food: '#b04a6a',
};

const nodeQuery = defineQuery([ResourceNode]);

/**
 * The eid carrying the `ResourceNode` for a destroyed prop.
 *
 * `<ResourceNode>` is NOT a merge recipe, so `<GameObject><ResourceNode/></…>`
 * puts the loot data on a CHILD entity while `destructible` (and therefore the
 * destroyed callback) reports the parent. Calling `harvest()` with the parent
 * silently returns 0 — the node is simply not there — and nothing is ever
 * credited. Walk one level down to find it.
 */
function resourceNodeOf(state: State, eid: number): number | null {
  if (state.hasComponent(eid, ResourceNode)) return eid;
  for (const node of nodeQuery(state.world)) {
    if (Parent.entity[node] === eid) return node;
  }
  return null;
}

/** Fallback for nodes whose entity script did not classify them. */
function kindFor(state: State, eid: number, nodeEid: number): string {
  if (isWoodEntity(eid)) return 'wood';
  if (isStoneEntity(eid)) return 'stone';
  if (isFoodEntity(eid)) return 'food';
  return getResourceNodeKind(state, nodeEid);
}

/**
 * Subscribe the loot handler. Call once after `runtime.start()`, like the
 * other post-start wiring in `main.ts` — it resolves the player by name.
 *
 * Returns the unsubscribe so an HMR reload does not stack handlers.
 */
export function registerHarvest(state: State): () => void {
  return onDestructibleDestroyed(state, (eid, x, y, z) => {
    if (eid === null) return;
    const player = state.getEntityByName('player');
    if (player === null) return;

    const nodeEid = resourceNodeOf(state, eid);
    if (nodeEid === null) return;

    // `harvest` returns 0 for a node already depleted and waiting on its
    // respawn timer, which is also what keeps a double-hit from double-paying.
    const amount = harvest(state, nodeEid);
    if (amount <= 0) return;

    const kind = kindFor(state, eid, nodeEid);
    const itemId = ITEM_BY_KIND[kind];
    if (!itemId) return;

    addItem(state, player, itemId, amount);
    spawnFloatingText(state, `+${amount} ${t(state, `item.${itemId}`)}`, {
      x,
      y: y + 1.4,
      z,
      duration: 1.4,
      color: COLOR_BY_KIND[kind] ?? '#ffffff',
    });
  });
}
