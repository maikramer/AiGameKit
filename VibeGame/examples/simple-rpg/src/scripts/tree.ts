import type { MonoBehaviourContext } from 'vibegame';

const woodEntities = new Set<number>();

export function isWoodEntity(eid: number): boolean {
  return woodEntities.has(eid);
}

export function start(ctx: MonoBehaviourContext): void {
  woodEntities.add(ctx.entity);
}

export function onDestroy(ctx: MonoBehaviourContext): void {
  woodEntities.delete(ctx.entity);
}

export function update(_ctx: MonoBehaviourContext): void {}
