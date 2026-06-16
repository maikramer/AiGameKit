import type { State } from '../../core';
import {
  ColliderShape,
  Collider,
  CollisionEvents,
  Rigidbody,
} from '../physics/components';
import { Transform } from '../transforms/components';
import { getDataRegistry } from '../rpg-core/registry';
import {
  FactionComponent,
  ProjectileConfig,
  ProjectileData,
} from './components';

export const PROJECTILE_TEMPLATE_KIND = 'projectile';

export interface ProjectileSpawnConfig {
  speed: number;
  maxLife: number;
  damage: number;
  /** Faction tag id (matches `FactionComponent.tag`). Defaults to 0 (player). */
  faction?: number;
  /** Sensor collider radius. Defaults to 0.3. */
  sensorRadius?: number;
}

export type ProjectileTarget =
  | number
  | { readonly eid: number }
  | { readonly point: readonly [number, number, number] };

export interface ProjectileTemplate {
  readonly id: string;
  readonly speed: number;
  readonly damage: number;
  readonly maxLife: number;
  readonly faction?: string;
  readonly sensorRadius?: number;
}

function resolveTargetPoint(
  target: ProjectileTarget
): readonly [number, number, number] {
  if (typeof target === 'number') {
    const t = target;
    return [Transform.posX[t], Transform.posY[t], Transform.posZ[t]];
  }
  if ('eid' in target) {
    const t = target.eid;
    return [Transform.posX[t], Transform.posY[t], Transform.posZ[t]];
  }
  return target.point;
}

export function spawnProjectile(
  state: State,
  originEid: number,
  target: ProjectileTarget,
  config: ProjectileSpawnConfig
): number {
  const ox = Transform.posX[originEid];
  const oy = Transform.posY[originEid];
  const oz = Transform.posZ[originEid];
  const [tx, ty, tz] = resolveTargetPoint(target);

  let dx = tx - ox;
  let dy = ty - oy;
  let dz = tz - oz;
  const len = Math.sqrt(dx * dx + dy * dy + dz * dz);
  const inv = len > 0 ? 1 / len : 1;
  dx *= inv;
  dy *= inv;
  dz *= inv;

  const speed = config.speed;
  const faction = config.faction ?? 0;
  const radius = config.sensorRadius ?? 0.3;

  const eid = state.createEntity();

  state.addComponent(eid, Transform, {
    posX: ox,
    posY: oy,
    posZ: oz,
    scaleX: 1,
    scaleY: 1,
    scaleZ: 1,
    rotW: 1,
  });

  state.addComponent(eid, Rigidbody);
  Rigidbody.gravityScale[eid] = 0;
  Rigidbody.velX[eid] = dx * speed;
  Rigidbody.velY[eid] = dy * speed;
  Rigidbody.velZ[eid] = dz * speed;

  state.addComponent(eid, Collider);
  Collider.shape[eid] = ColliderShape.Sphere;
  Collider.radius[eid] = radius;
  Collider.isSensor[eid] = 1;

  state.addComponent(eid, CollisionEvents);
  CollisionEvents.activeEvents[eid] = 1;

  state.addComponent(eid, ProjectileData, {
    damage: config.damage,
    ownerEid: originEid,
    lifetime: config.maxLife,
    age: 0,
  });

  state.addComponent(eid, ProjectileConfig, {
    speed,
    maxLife: config.maxLife,
    damage: config.damage,
    faction,
  });

  state.addComponent(eid, FactionComponent);
  FactionComponent.tag[eid] = faction;

  return eid;
}

export function spawnProjectileFromTemplate(
  state: State,
  originEid: number,
  templateName: string,
  target?: ProjectileTarget
): number {
  const template = getDataRegistry(state).get<ProjectileTemplate>(
    PROJECTILE_TEMPLATE_KIND,
    templateName
  );
  if (!template) {
    throw new Error(
      `spawnProjectileFromTemplate: unknown projectile template "${templateName}"`
    );
  }
  const resolvedTarget: ProjectileTarget = target ?? {
    point: [
      Transform.posX[originEid],
      Transform.posY[originEid],
      Transform.posZ[originEid],
    ],
  };
  return spawnProjectile(state, originEid, resolvedTarget, {
    speed: template.speed,
    maxLife: template.maxLife,
    damage: template.damage,
    faction: resolveFactionTag(state, template.faction),
    sensorRadius: template.sensorRadius,
  });
}

function resolveFactionTag(
  state: State,
  name: string | undefined
): number | undefined {
  if (name === undefined) return undefined;
  const tagMap = state.config.getEnums('faction').tag;
  return tagMap && name in tagMap ? tagMap[name] : 0;
}
