import { defineQuery } from "bitecs";
import type { MonoBehaviourContext } from "vibegame";
import { Transform, PlayerController } from "vibegame";
import { Health, healHealth } from "../../../../src/plugins/combat/components.ts";

const PICKUP_RANGE = 2.5;
const GLOW_RANGE = 4.0;
const SPIN_SPEED = 1.5;

const playerQuery = defineQuery([PlayerController]);

let cachedPlayerEid = 0;

function findPlayer(ctx: MonoBehaviourContext): number {
  if (cachedPlayerEid && Health.current[cachedPlayerEid] > 0) return cachedPlayerEid;
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
  const dist = Math.sqrt(distSq);

  if (dist < GLOW_RANGE) {
    const pulse = 1.1 + 0.05 * Math.sin(performance.now() * 0.003);
    Transform.scaleX[eid] = pulse;
    Transform.scaleY[eid] = pulse;
    Transform.scaleZ[eid] = pulse;
  } else {
    Transform.scaleX[eid] = 1;
    Transform.scaleY[eid] = 1;
    Transform.scaleZ[eid] = 1;
  }

  if (distSq < PICKUP_RANGE * PICKUP_RANGE && Health.max[playerEid] > 0) {
    const roll = Math.random();
    if (roll < 0.33) {
      healHealth(playerEid, 50);
    } else if (roll < 0.66) {
      PlayerController.maxSpeed[playerEid] *= 1.3;
      setTimeout(() => {
        if (Health.current[playerEid] !== undefined && Health.current[playerEid] > 0) {
          PlayerController.maxSpeed[playerEid] /= 1.3;
        }
      }, 10000);
    } else {
      healHealth(playerEid, 30);
    }
    ctx.state.destroyEntity(eid);
  }
}
