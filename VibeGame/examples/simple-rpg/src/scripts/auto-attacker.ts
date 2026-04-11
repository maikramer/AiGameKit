import { defineQuery } from 'bitecs';
import type { MonoBehaviourContext } from 'vibegame';
import { Transform } from 'vibegame';
import { Health, ProjectileData } from 'vibegame/plugins/combat/components';
import { Collider, CollisionEvents, SetLinearVelocity } from 'vibegame/plugins/physics/components';

interface AttackConfig {
  cooldown: number;
  maxRange: number;
  maxProjectiles: number;
  speed: number;
  damage: number;
}

const DEFAULT_CONFIG: AttackConfig = {
  cooldown: 0.5,
  maxRange: 50,
  maxProjectiles: 5,
  speed: 40,
  damage: 10,
};

const configs = new Map<number, AttackConfig>();
const cooldownTimers = new Map<number, number>();
const healthQuery = defineQuery([Health]);
const projectileQuery = defineQuery([ProjectileData]);

export function start(ctx: MonoBehaviourContext): void {
  configs.set(ctx.entity, { ...DEFAULT_CONFIG });
  cooldownTimers.set(ctx.entity, 0);
}

export function update(ctx: MonoBehaviourContext): void {
  const eid = ctx.entity;
  const config = configs.get(eid);
  if (!config) return;

  let timer = cooldownTimers.get(eid) ?? 0;
  timer -= ctx.deltaTime;
  if (timer > 0) {
    cooldownTimers.set(eid, timer);
    return;
  }

  const projectiles = projectileQuery(ctx.state.world);
  const ownCount = projectiles.filter((p) => ProjectileData.ownerEid[p] === eid).length;
  if (ownCount >= config.maxProjectiles) {
    cooldownTimers.set(eid, timer);
    return;
  }

  const px = Transform.posX[eid];
  const py = Transform.posY[eid];
  const pz = Transform.posZ[eid];

  const targets = healthQuery(ctx.state.world);
  let nearestEid = 0;
  let nearestDist = config.maxRange;

  for (let i = 0; i < targets.length; i++) {
    const t = targets[i];
    if (t === eid) continue;
    if (Health.current[t] <= 0) continue;

    const dx = Transform.posX[t] - px;
    const dy = Transform.posY[t] - py;
    const dz = Transform.posZ[t] - pz;
    const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);

    if (dist < nearestDist) {
      nearestDist = dist;
      nearestEid = t;
    }
  }

  if (!nearestEid) {
    cooldownTimers.set(eid, timer);
    return;
  }

  const tx = Transform.posX[nearestEid];
  const ty = Transform.posY[nearestEid];
  const tz = Transform.posZ[nearestEid];

  const dx = tx - px;
  const dy = ty - py;
  const dz = tz - pz;
  const len = Math.sqrt(dx * dx + dy * dy + dz * dz);
  if (len === 0) {
    cooldownTimers.set(eid, timer);
    return;
  }

  const ndx = dx / len;
  const ndy = dy / len;
  const ndz = dz / len;

  const spawnOffset = 2;
  const spawnPos = `${px + ndx * spawnOffset} ${py + ndy * spawnOffset} ${pz + ndz * spawnOffset}`;

  const projectileEid = ctx.state.createFromRecipe('dynamic-part', {
    pos: spawnPos,
    scale: '0.2 0.2 0.2',
  });

  ctx.state.addComponent(projectileEid, ProjectileData);
  ProjectileData.damage[projectileEid] = config.damage;
  ProjectileData.ownerEid[projectileEid] = eid;
  ProjectileData.lifetime[projectileEid] = 3.0;
  ProjectileData.age[projectileEid] = 0;

  ctx.state.addComponent(projectileEid, Collider);
  Collider.isSensor[projectileEid] = 1;

  ctx.state.addComponent(projectileEid, CollisionEvents);
  CollisionEvents.activeEvents[projectileEid] = 1;

  ctx.state.addComponent(projectileEid, SetLinearVelocity);
  SetLinearVelocity.x[projectileEid] = ndx * config.speed;
  SetLinearVelocity.y[projectileEid] = ndy * config.speed;
  SetLinearVelocity.z[projectileEid] = ndz * config.speed;

  cooldownTimers.set(eid, config.cooldown);
}
