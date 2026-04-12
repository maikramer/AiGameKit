import { defineQuery } from 'bitecs';
import type { MonoBehaviourContext } from 'vibegame';
import {
  Transform,
  PlayerController,
  SteeringAgent,
  SteeringTarget,
  MonoBehaviour,
} from 'vibegame';
import { Health, isDead } from '../../../../src/plugins/combat/components.ts';
import { CollisionEvents } from '../../../../src/plugins/physics/components.ts';
import { getTerrainHeightAt } from '../../../../src/plugins/terrain/systems.ts';
import { setScriptFile } from '../../../../src/plugins/entity-script/context.ts';

const WATER_LEVEL = 1.25;
const HEAL_DROP_CHANCE = 0.4;
const HEAL_DROP_PICKUP_RANGE = 2.0;
const SPAWN_RADIUS_MIN = 30;
const SPAWN_RADIUS_MAX = 50;
const INITIAL_DELAY = 2.0;
const BETWEEN_WAVES_DELAY = 3.0;
const WAVE_COMPLETE_DISPLAY_TIME = 3.0;

interface WaveState {
  waveNumber: number;
  waveActive: boolean;
  betweenWaves: boolean;
  spawnDelay: number;
  waveCompleteDelay: number;
}

interface EnemyRecord {
  x: number;
  y: number;
  z: number;
  alive: boolean;
}

interface HealDrop {
  eid: number;
  x: number;
  y: number;
  z: number;
}

const states = new Map<number, WaveState>();
const enemyMap = new Map<number, EnemyRecord>();
const healDrops: HealDrop[] = [];
const playerQuery = defineQuery([PlayerController]);
const healthQuery = defineQuery([Health]);

let cachedPlayerEid = 0;
let currentWaveNumber = 1;
let currentEnemiesAlive = 0;

function findPlayer(ctx: MonoBehaviourContext): number {
  if (cachedPlayerEid && Health.current[cachedPlayerEid] > 0)
    return cachedPlayerEid;
  const players = playerQuery(ctx.state.world);
  cachedPlayerEid = players[0] ?? 0;
  return cachedPlayerEid;
}

function spawnWave(ctx: MonoBehaviourContext, waveNum: number): void {
  const count = 3 + (waveNum - 1) * 2;
  const playerEid = findPlayer(ctx);
  if (!playerEid) return;

  const px = Transform.posX[playerEid];
  const pz = Transform.posZ[playerEid];

  let spawned = 0;
  let attempts = 0;
  const maxAttempts = count * 5;

  while (spawned < count && attempts < maxAttempts) {
    attempts++;
    const angle = Math.random() * Math.PI * 2;
    const radius =
      SPAWN_RADIUS_MIN + Math.random() * (SPAWN_RADIUS_MAX - SPAWN_RADIUS_MIN);
    const x = px + Math.cos(angle) * radius;
    const z = pz + Math.sin(angle) * radius;
    const height = getTerrainHeightAt(ctx.state, x, z);

    if (height < WATER_LEVEL) continue;

    const eid = ctx.state.createFromRecipe('dynamic-part', {
      pos: `${x} ${height + 0.5} ${z}`,
      scale: '0.44 0.88 0.44',
    });

    ctx.state.addComponent(eid, SteeringAgent);
    SteeringAgent.maxSpeed[eid] = 8;
    SteeringAgent.active[eid] = 1;
    SteeringAgent.behavior[eid] = 1;

    ctx.state.addComponent(eid, SteeringTarget);

    ctx.state.addComponent(eid, CollisionEvents);
    CollisionEvents.activeEvents[eid] = 1;

    ctx.state.addComponent(eid, MonoBehaviour);
    MonoBehaviour.enabled[eid] = 1;
    MonoBehaviour.ready[eid] = 0;
    setScriptFile(ctx.state, eid, 'enemy.ts');

    enemyMap.set(eid, { x, y: height + 0.5, z, alive: true });
    spawned++;
  }
}

function spawnHealDrop(
  ctx: MonoBehaviourContext,
  x: number,
  y: number,
  z: number
): void {
  const eid = ctx.state.createFromRecipe('dynamic-part', {
    pos: `${x} ${y + 0.5} ${z}`,
    scale: '0.3 0.3 0.3',
  });

  ctx.state.addComponent(eid, MonoBehaviour);
  MonoBehaviour.enabled[eid] = 1;
  MonoBehaviour.ready[eid] = 0;
  setScriptFile(ctx.state, eid, 'heal-drop.ts');

  healDrops.push({ eid, x, y: y + 0.5, z });
}

export function start(ctx: MonoBehaviourContext): void {
  states.set(ctx.entity, {
    waveNumber: 1,
    waveActive: false,
    betweenWaves: true,
    spawnDelay: INITIAL_DELAY,
    waveCompleteDelay: 0,
  });
}

export function update(ctx: MonoBehaviourContext): void {
  const state = states.get(ctx.entity);
  if (!state) return;

  const dt = ctx.deltaTime;
  const playerEid = findPlayer(ctx);

  if (state.betweenWaves) {
    state.spawnDelay -= dt;
    if (state.spawnDelay <= 0) {
      spawnWave(ctx, state.waveNumber);
      state.waveActive = true;
      state.betweenWaves = false;
    }
    return;
  }

  if (state.waveActive) {
    let aliveCount = 0;
    const allHealth = healthQuery(ctx.state.world);

    for (const eid of allHealth) {
      if (eid === playerEid) continue;
      if (!enemyMap.has(eid)) continue;
      if (!isDead(eid)) {
        aliveCount++;
        const record = enemyMap.get(eid)!;
        record.x = Transform.posX[eid];
        record.y = Transform.posY[eid];
        record.z = Transform.posZ[eid];
      }
    }

    currentEnemiesAlive = aliveCount;

    if (aliveCount === 0) {
      for (const [eid, record] of enemyMap) {
        if (record.alive && isDead(eid)) {
          record.alive = false;
          if (Math.random() < HEAL_DROP_CHANCE) {
            spawnHealDrop(ctx, record.x, record.y, record.z);
          }
        }
      }
      enemyMap.clear();

      state.waveActive = false;
      state.waveCompleteDelay = WAVE_COMPLETE_DISPLAY_TIME;
      state.waveNumber++;
      currentWaveNumber = state.waveNumber;
    }
    return;
  }

  if (state.waveCompleteDelay > 0) {
    state.waveCompleteDelay -= dt;
    if (state.waveCompleteDelay <= 0) {
      state.betweenWaves = true;
      state.spawnDelay = BETWEEN_WAVES_DELAY;
    }
  }
}

export function getWaveNumber(): number {
  return currentWaveNumber;
}

export function getEnemiesAlive(): number {
  return currentEnemiesAlive;
}
