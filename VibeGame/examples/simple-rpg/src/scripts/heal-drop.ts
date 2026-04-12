import { defineQuery } from 'bitecs';
import type { MonoBehaviourContext } from 'vibegame';
import { Transform, PlayerController } from 'vibegame';
import {
  Health,
  healHealth,
} from '../../../../src/plugins/combat/components.ts';

const HEAL_AMOUNT = 25;
const PICKUP_RANGE = 2.0;
const SPIN_SPEED = 2.0;

const playerQuery = defineQuery([PlayerController]);

let cachedPlayerEid = 0;

function findPlayer(ctx: MonoBehaviourContext): number {
  if (cachedPlayerEid && Health.current[cachedPlayerEid] > 0)
    return cachedPlayerEid;
  const players = playerQuery(ctx.state.world);
  cachedPlayerEid = players[0] ?? 0;
  return cachedPlayerEid;
}

export function start(ctx: MonoBehaviourContext): void {
  findPlayer(ctx);
}

export function update(ctx: MonoBehaviourContext): void {
  const eid = ctx.entity;

  const playerEid = findPlayer(ctx);
  if (!playerEid) return;

  Transform.eulerY[eid] += SPIN_SPEED * ctx.deltaTime;
  const dx = Transform.posX[playerEid] - Transform.posX[eid];
  const dz = Transform.posZ[playerEid] - Transform.posZ[eid];
  const distSq = dx * dx + dz * dz;

  if (distSq < PICKUP_RANGE * PICKUP_RANGE && Health.max[playerEid] > 0) {
    healHealth(playerEid, HEAL_AMOUNT);
    ctx.state.destroyEntity(eid);
  }
}
