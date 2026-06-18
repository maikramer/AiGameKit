import type { MonoBehaviourContext } from 'vibegame';
import {
  registerInteractionTarget,
  unregisterInteractionTarget,
} from 'vibegame';

// Harvestable rock: the engine DestructiblePlugin handles the hits/break; this
// script only shows the "[J] Mine" prompt while the player is in range.
export function start(ctx: MonoBehaviourContext): void {
  registerInteractionTarget(ctx.state, ctx.entity, { label: 'Mine', key: 'J' });
}

export function onDestroy(ctx: MonoBehaviourContext): void {
  unregisterInteractionTarget(ctx.state, ctx.entity);
}

export function update(_ctx: MonoBehaviourContext): void {}
