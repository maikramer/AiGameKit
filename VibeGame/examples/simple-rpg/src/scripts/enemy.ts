import { defineQuery } from 'bitecs';
import type { MonoBehaviourContext } from 'vibegame';
import { Transform, PlayerController, SteeringAgent, SteeringTarget } from 'vibegame';
import { Health, damageHealth, isDead } from '../../../../src/plugins/combat/components.ts';
import { CollisionEvents } from '../../../../src/plugins/physics/components.ts';

interface EnemyConfig {
  health: number;
  speed: number;
  attackDamage: number;
  attackRange: number;
  attackCooldown: number;
  detectRange: number;
}

const DEFAULT_CONFIG: EnemyConfig = {
  health: 30,
  speed: 8,
  attackDamage: 12,
  attackRange: 2.5,
  attackCooldown: 1.0,
  detectRange: 25,
};

const configs = new Map<number, EnemyConfig>();
const attackTimers = new Map<number, number>();
const playerQuery = defineQuery([PlayerController]);

let cachedPlayerEid = 0;

function findPlayer(ctx: MonoBehaviourContext): number {
  if (cachedPlayerEid && Health.current[cachedPlayerEid] > 0) return cachedPlayerEid;
  const players = playerQuery(ctx.state.world);
  cachedPlayerEid = players[0] ?? 0;
  return cachedPlayerEid;
}

export function start(ctx: MonoBehaviourContext): void {
  const eid = ctx.entity;
  configs.set(eid, { ...DEFAULT_CONFIG });
  attackTimers.set(eid, 0);

  ctx.state.addComponent(eid, Health);
  Health.current[eid] = DEFAULT_CONFIG.health;
  Health.max[eid] = DEFAULT_CONFIG.health;

  ctx.state.addComponent(eid, CollisionEvents);
  CollisionEvents.activeEvents[eid] = 1;

  cachedPlayerEid = findPlayer(ctx);
}

export function update(ctx: MonoBehaviourContext): void {
  const eid = ctx.entity;
  const config = configs.get(eid);
  if (!config) return;

  if (isDead(eid)) {
    ctx.state.destroyEntity(eid);
    configs.delete(eid);
    attackTimers.delete(eid);
    return;
  }

  const playerEid = findPlayer(ctx);
  if (!playerEid) return;

  const ex = Transform.posX[eid];
  const ez = Transform.posZ[eid];
  const px = Transform.posX[playerEid];
  const pz = Transform.posZ[playerEid];

  const dx = px - ex;
  const dz = pz - ez;
  const distance = Math.sqrt(dx * dx + dz * dz);

  if (distance <= config.attackRange) {
    SteeringAgent.active[eid] = 0;

    let timer = attackTimers.get(eid) ?? 0;
    timer -= ctx.deltaTime;
    if (timer <= 0) {
      if (!Health.max[playerEid]) {
        ctx.state.addComponent(playerEid, Health);
        Health.current[playerEid] = 100;
        Health.max[playerEid] = 100;
      }
      damageHealth(playerEid, config.attackDamage);
      timer = config.attackCooldown;
    }
    attackTimers.set(eid, timer);
  } else if (distance < config.detectRange) {
    SteeringAgent.behavior[eid] = 0;
    SteeringTarget.targetEntity[eid] = playerEid;
    SteeringAgent.active[eid] = 1;
  } else {
    SteeringAgent.behavior[eid] = 1;
    SteeringAgent.active[eid] = 1;
  }
}
