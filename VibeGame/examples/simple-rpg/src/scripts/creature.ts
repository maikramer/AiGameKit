import * as THREE from 'three';
import { loadGltfToSceneWithAnimator, playSound } from 'vibegame';
import type { GltfAnimator, MonoBehaviourContext, State } from 'vibegame';
import {
  Transform,
  defineQuery,
  PlayerController,
  MonoBehaviour,
} from 'vibegame';
import { getTerrainHeightAt } from '../../../../src/plugins/terrain/systems.ts';
import { getBvhSurfaceHeight } from '../../../../src/plugins/bvh/utils.ts';
import {
  Health,
  damageHealth,
  isDead,
} from '../../../../src/plugins/combat/components.ts';
import { spawnParticleBurst } from '../../../../src/plugins/particles/utils.ts';
import { threeCameras } from '../../../../src/plugins/rendering/utils.ts';
import {
  isNavMeshReady,
  setAgentTarget,
  clearAgentTarget,
  removeAgent,
  getAgentPosition,
  NavMeshAgent,
} from '../../../../src/plugins/navmesh/index';

// Terrain collision layer for ground sampling. Sampling the terrain BVH (not a
// Rapier ray against everything) keeps creatures off the player's head: the BVH
// only contains terrain, so a creature standing over the player can't snap onto
// it. Everything shares membership 0xffff, so a Rapier ray would hit the player.
const TERRAIN_LAYER = 0x0001;
// How close a lunge may bring the creature to the player's center — stops it
// dashing through/over the player so melee stays face-to-face. ~2.2m keeps a
// clear gap between the ~1m-wide meshes instead of letting them overlap.
const LUNGE_STANDOFF = 2.2;
const WATER_LEVEL = 1.25;
const TURN_RATE = 2.0;
const ACCEL = 3.0;
const DETECT_RANGE = 18;
const ATTACK_RANGE = 3.0;
const ATTACK_COOLDOWN = 2.5;
const LEASH_RADIUS = 30;
const LUNGE_WINDUP = 0.25;
const LUNGE_DURATION = 0.3;
const LUNGE_SPEED = 6.0;
const LUNGE_RECOVERY = 0.5;
const LUNGE_HIT_RANGE = 4.0;
const HOVER_MIN = 2.0;
const HOVER_MAX = 5.0;
const WANDER_PICK_DIST_MIN = 2;
const HEALTH_BAR_WIDTH = 1.4;

const aggroEntities = new Set<number>();

export function anyCreatureAggro(): boolean {
  return aggroEntities.size > 0;
}


export interface CreatureClips {
  idle: string;
  walk: string;
  run: string;
  lunge: string;
  death: string;
}

export interface CreatureConfig {
  modelUrl: string;
  clips: CreatureClips;
  hp: number;
  chaseSpeed: number;
  wanderSpeed: number;
  wanderRadius: number;
  attackDamage: number;
  lootGoldMin: number;
  lootGoldMax: number;
  onDeathLoot?: (state: State, gold: number, x: number, y: number, z: number) => void;
}

type CombatState = 'idle' | 'chase' | 'attack' | 'dead';
type LungePhase = 'ready' | 'windup' | 'lunge' | 'recovery';

interface CreatureState {
  heading: number;
  targetHeading: number;
  speed: number;
  hovering: boolean;
  stateTimer: number;
  originX: number;
  originZ: number;
  footOffset: number;
  ready: boolean;
  group: THREE.Group | null;
  animator: GltfAnimator | null;
  playing: string;
  combatState: CombatState;
  attackTimer: number;
  lungePhase: LungePhase;
  lungeTimer: number;
  lungeDirX: number;
  lungeDirZ: number;
  deathTimer: number;
  deathHandled: boolean;
  healthBarBg: THREE.Mesh | null;
  healthBarFill: THREE.Mesh | null;
  lastHp: number;
  flashTimer: number;
  flashMats: { mat: THREE.MeshStandardMaterial; emHex: number; emInt: number }[] | null;
  wanderTargetX: number;
  wanderTargetZ: number;
  prevPosX: number;
  prevPosZ: number;
  navHeading: number;
  navInitialized: boolean;
  agentCreated: boolean;
}

const playerQuery = defineQuery([PlayerController]);
const _box = new THREE.Box3();

function groundHeight(
  ctx: MonoBehaviourContext,
  x: number,
  z: number,
  fromY: number
): number {
  const gy = getBvhSurfaceHeight(ctx.state, x, fromY + 60, z, 2000, TERRAIN_LAYER);
  if (gy != null && Number.isFinite(gy)) return gy;
  const hm = getTerrainHeightAt(ctx.state, x, z);
  if (Number.isFinite(hm)) return hm;
  return 0;
}

const FOOT_RADIUS = 0.3;

function footprintHeight(
  ctx: MonoBehaviourContext,
  x: number,
  z: number,
  fromY: number
): number {
  let best = groundHeight(ctx, x, z, fromY);
  if (!Number.isFinite(best)) return best;
  for (const [ox, oz] of [
    [FOOT_RADIUS, 0],
    [-FOOT_RADIUS, 0],
    [0, FOOT_RADIUS],
    [0, -FOOT_RADIUS],
  ]) {
    const h = groundHeight(ctx, x + ox, z + oz, fromY);
    if (Number.isFinite(h) && h > best) best = h;
  }
  return best;
}

function ensureHealthBar(s: CreatureState): void {
  if (s.healthBarBg || !s.group) return;
  const bgGeo = new THREE.PlaneGeometry(1.5, 0.22);
  const bgMat = new THREE.MeshBasicMaterial({
    color: 0x111111,
    depthTest: false,
    transparent: true,
    opacity: 0.85,
  });
  const bg = new THREE.Mesh(bgGeo, bgMat);
  bg.position.set(0, 2.0, 0);
  bg.renderOrder = 998;
  bg.visible = false;
  s.group.add(bg);

  const fillGeo = new THREE.PlaneGeometry(HEALTH_BAR_WIDTH, 0.14);
  const fillMat = new THREE.MeshBasicMaterial({
    color: 0x44ddff,
    depthTest: false,
    transparent: true,
  });
  const fill = new THREE.Mesh(fillGeo, fillMat);
  fill.position.set(0, 2.0, 0.01);
  fill.renderOrder = 999;
  fill.visible = false;
  s.group.add(fill);

  s.healthBarBg = bg;
  s.healthBarFill = fill;
}

function disposeHealthBar(s: CreatureState): void {
  for (const mesh of [s.healthBarBg, s.healthBarFill]) {
    if (!mesh) continue;
    mesh.removeFromParent();
    mesh.geometry.dispose();
    const mat = mesh.material;
    if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
    else mat?.dispose();
  }
  s.healthBarBg = null;
  s.healthBarFill = null;
}

/** Cache the creature's emissive materials so a hit can tint them white. */
function collectFlashMats(s: CreatureState): void {
  if (s.flashMats || !s.group) return;
  const mats: { mat: THREE.MeshStandardMaterial; emHex: number; emInt: number }[] = [];
  s.group.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    const m = mesh.material;
    const arr = Array.isArray(m) ? m : [m];
    for (const mat of arr) {
      const sm = mat as THREE.MeshStandardMaterial;
      if (sm && sm.emissive) {
        mats.push({
          mat: sm,
          emHex: sm.emissive.getHex(),
          emInt: sm.emissiveIntensity ?? 1,
        });
      }
    }
  });
  s.flashMats = mats;
}

function applyFlash(s: CreatureState, on: boolean): void {
  if (!s.flashMats) return;
  for (const f of s.flashMats) {
    if (on) {
      f.mat.emissive.setRGB(1, 1, 1);
      f.mat.emissiveIntensity = 1.4;
    } else {
      f.mat.emissive.setHex(f.emHex);
      f.mat.emissiveIntensity = f.emInt;
    }
  }
}

export interface CreatureBehaviours {
  start: (ctx: MonoBehaviourContext) => void;
  update: (ctx: MonoBehaviourContext) => void;
  onDestroy: (ctx: MonoBehaviourContext) => void;
}

export function createCreatureBehaviours(
  cfg: CreatureConfig
): CreatureBehaviours {
  const stateMap = new Map<number, CreatureState>();
  let cachedPlayerEid = 0;

  function findPlayer(ctx: MonoBehaviourContext): number {
    if (cachedPlayerEid && Health.current[cachedPlayerEid] > 0)
      return cachedPlayerEid;
    const players = playerQuery(ctx.state.world);
    cachedPlayerEid = players[0] ?? 0;
    return cachedPlayerEid;
  }

  function handleDeath(
    ctx: MonoBehaviourContext,
    s: CreatureState,
    eid: number
  ): void {
    if (s.deathHandled) return;
    s.deathHandled = true;
    s.combatState = 'dead';
    aggroEntities.delete(eid);
    s.deathTimer = 2.0;
    s.speed = 0;
    clearAgentTarget(ctx.state, eid);

    if (s.healthBarBg) s.healthBarBg.visible = false;
    if (s.healthBarFill) s.healthBarFill.visible = false;

    playSound('enemy-death');

    const x = Transform.posX[eid];
    const y = Transform.posY[eid];
    const z = Transform.posZ[eid];

    const gold = Math.floor(
      cfg.lootGoldMin +
        Math.random() * (cfg.lootGoldMax - cfg.lootGoldMin + 1)
    );
    cfg.onDeathLoot?.(ctx.state, gold, x, y, z);
    playSound('item-drop');

    // The "+gold" pop is emitted centrally (gold-delta watcher in main.ts).
    spawnParticleBurst(ctx.state, {
      x,
      y: y + 0.5,
      z,
      preset: 'explosion',
      count: 16,
      duration: 0.8,
    });

    if (s.animator && s.playing !== cfg.clips.death) {
      s.animator.play(cfg.clips.death, { loop: false });
      s.playing = cfg.clips.death;
    }
  }

  function updateNavMeshMovement(
    ctx: MonoBehaviourContext,
    s: CreatureState,
    eid: number,
    playerEid: number,
    dt: number
  ): void {
    let moving = false;
    let tx = 0;
    let tz = 0;

    if (s.lungePhase === 'windup' || s.lungePhase === 'recovery') {
      moving = false;
    } else if (s.combatState === 'chase' && playerEid > 0) {
      // Hold a 1.1–1.6m band around the player. Crowd agents ignore RigidBodies
      // and carry momentum, so a target pinned to the player's centroid lets the
      // capsule glide through the agent; anchoring on a ring around the player
      // makes the enemy approach face-to-face and retreat when the player walks
      // into it, instead of overlapping.
      const pdx = Transform.posX[playerEid] - Transform.posX[eid];
      const pdz = Transform.posZ[playerEid] - Transform.posZ[eid];
      const pdist = Math.sqrt(pdx * pdx + pdz * pdz) || 1e-3;
      const DESIRED_DIST = 1.6;
      const MIN_GAP = 1.1;
      if (pdist > DESIRED_DIST) {
        const k = (pdist - DESIRED_DIST) / pdist;
        tx = Transform.posX[eid] + pdx * k;
        tz = Transform.posZ[eid] + pdz * k;
        moving = true;
      } else if (pdist < MIN_GAP) {
        const k = (MIN_GAP - pdist) / pdist;
        tx = Transform.posX[eid] - pdx * k;
        tz = Transform.posZ[eid] - pdz * k;
        moving = true;
      } else {
        moving = false;
      }
    } else if (s.combatState === 'attack') {
      // Player can still push in: keep the same gap during attack so lunges
      // start from face-to-face distance instead of inside the capsule.
      const pdx = Transform.posX[playerEid] - Transform.posX[eid];
      const pdz = Transform.posZ[playerEid] - Transform.posZ[eid];
      const pdist = Math.sqrt(pdx * pdx + pdz * pdz) || 1e-3;
      const MIN_GAP = 1.1;
      if (pdist < MIN_GAP) {
        const k = (MIN_GAP - pdist) / pdist;
        tx = Transform.posX[eid] - pdx * k;
        tz = Transform.posZ[eid] - pdz * k;
        moving = true;
      } else {
        moving = false;
      }
    } else {
      const homeDx = s.originX - Transform.posX[eid];
      const homeDz = s.originZ - Transform.posZ[eid];
      const homeDistSq = homeDx * homeDx + homeDz * homeDz;
      if (homeDistSq > cfg.wanderRadius * cfg.wanderRadius) {
        tx = s.originX;
        tz = s.originZ;
        moving = true;
      } else if (!s.hovering) {
        tx = s.wanderTargetX;
        tz = s.wanderTargetZ;
        moving = true;
      } else {
        moving = false;
      }
    }

    if (moving) {
      const ty = getTerrainHeightAt(ctx.state, tx, tz);
      setAgentTarget(ctx.state, eid, tx, ty, tz);
    } else {
      clearAgentTarget(ctx.state, eid);
    }

    const pos = getAgentPosition(eid);
    if (!pos) return;

    if (!s.navInitialized) {
      s.prevPosX = pos.x;
      s.prevPosZ = pos.z;
      s.navInitialized = true;
    }

    const vx = pos.x - s.prevPosX;
    const vz = pos.z - s.prevPosZ;
    const moveSpeed = dt > 0 ? Math.hypot(vx, vz) / dt : 0;

    let heading: number;
    if (moveSpeed > 0.3) {
      heading = Math.atan2(vx, vz);
      s.navHeading = heading;
    } else if (
      playerEid > 0 &&
      Health.max[playerEid] > 0 &&
      (s.combatState === 'attack' ||
        s.lungePhase === 'windup' ||
        s.lungePhase === 'recovery')
    ) {
      const pdx = Transform.posX[playerEid] - pos.x;
      const pdz = Transform.posZ[playerEid] - pos.z;
      heading = Math.atan2(pdx, pdz);
      s.navHeading = heading;
    } else {
      heading = s.navHeading;
    }

    const terrainY = groundHeight(ctx, pos.x, pos.z, 500);
    const visualY =
      (Number.isFinite(terrainY) ? terrainY : pos.y) + s.footOffset;
    s.group!.position.set(pos.x, visualY, pos.z);
    Transform.posX[eid] = pos.x;
    Transform.posY[eid] = visualY;
    Transform.posZ[eid] = pos.z;
    Transform.dirty[eid] = 1;
    s.group!.rotation.set(0, heading, 0);
    s.prevPosX = pos.x;
    s.prevPosZ = pos.z;

    let clip: string;
    if (s.lungePhase !== 'ready') {
      clip = cfg.clips.lunge;
    } else if (s.combatState === 'chase') {
      clip = cfg.clips.run;
    } else {
      clip = moveSpeed > 0.3 ? cfg.clips.walk : cfg.clips.idle;
    }
    if (s.animator && s.playing !== clip) {
      if (clip === cfg.clips.lunge) {
        s.animator.play(cfg.clips.lunge, { loop: false });
      } else {
        s.animator.play(clip);
      }
      s.playing = clip;
    }
  }

  const behaviours: CreatureBehaviours = {
    start(ctx: MonoBehaviourContext): void {
      const eid = ctx.entity;
      const s: CreatureState = {
        heading: 0,
        targetHeading: 0,
        speed: 0,
        hovering: true,
        stateTimer: HOVER_MIN + Math.random() * (HOVER_MAX - HOVER_MIN),
        originX: 0,
        originZ: 0,
        footOffset: 0,
        ready: false,
        group: null,
        animator: null,
        playing: '',
        combatState: 'idle',
        attackTimer: 0,
        lungePhase: 'ready',
        lungeTimer: 0,
        lungeDirX: 0,
        lungeDirZ: 1,
        deathTimer: 0,
        deathHandled: false,
        healthBarBg: null,
        healthBarFill: null,
        lastHp: cfg.hp,
        flashTimer: 0,
        flashMats: null,
        wanderTargetX: 0,
        wanderTargetZ: 0,
        prevPosX: 0,
        prevPosZ: 0,
        navHeading: 0,
        navInitialized: false,
        agentCreated: false,
      };
      s.heading = Math.random() * Math.PI * 2;
      s.navHeading = s.heading;
      stateMap.set(eid, s);

      if (!ctx.state.hasComponent(eid, Health)) {
        ctx.state.addComponent(eid, Health);
      }
      Health.current[eid] = cfg.hp;
      Health.max[eid] = cfg.hp;

      if (!ctx.state.hasComponent(eid, NavMeshAgent)) {
        ctx.state.addComponent(eid, NavMeshAgent);
      }
      NavMeshAgent.speed[eid] = cfg.chaseSpeed;
      NavMeshAgent.radius[eid] = 0.4;
      NavMeshAgent.height[eid] = 1.0;
      NavMeshAgent.enabled[eid] = 1;

      void loadGltfToSceneWithAnimator(ctx.state, cfg.modelUrl, {
        crossfadeDuration: 0.25,
      }).then((result) => {
        if (stateMap.get(eid) !== s) {
          result.group.removeFromParent();
          return;
        }
        s.group = result.group;
        s.animator = result.animator;
        s.group.updateWorldMatrix(true, true);
        _box.setFromObject(s.group);
        s.footOffset = Number.isFinite(_box.min.y) ? -_box.min.y : 0;
      });
    },

    onDestroy(ctx: MonoBehaviourContext): void {
      const s = stateMap.get(ctx.entity);
      if (s) disposeHealthBar(s);
      s?.group?.removeFromParent();
      removeAgent(ctx.state, ctx.entity);
      stateMap.delete(ctx.entity);
      aggroEntities.delete(ctx.entity);
    },

    update(ctx: MonoBehaviourContext): void {
      const eid = ctx.entity;
      const s = stateMap.get(eid);
      if (!s || !s.group) return;

      s.animator?.update(ctx.deltaTime);

      const x = Transform.posX[eid];
      const z = Transform.posZ[eid];

      if (!s.ready) {
        const gy = groundHeight(ctx, x, z, 500);
        if (!Number.isFinite(gy) || gy === 0) return;
        s.originX = x;
        s.originZ = z;
        s.ready = true;
      }

      const dt = ctx.deltaTime;

      if (s.combatState === 'dead') {
        s.deathTimer -= dt;
        if (s.deathTimer <= 0) {
          disposeHealthBar(s);
          s.group?.removeFromParent();
          s.group = null;
          MonoBehaviour.enabled[eid] = 0;
        }
        return;
      }

      // Tick down a hit flash and restore the original emissive when it ends.
      if (s.flashTimer > 0) {
        s.flashTimer -= ctx.deltaTime;
        if (s.flashTimer <= 0) applyFlash(s, false);
      }

      const currentHp = Health.current[eid];
      if (s.lastHp > currentHp) {
        // Damage number + hurt SFX come from the central watchCombatFx in
        // main.ts; here we add the 3D spark burst + a white flash for hit juice.
        collectFlashMats(s);
        s.flashTimer = 0.11;
        applyFlash(s, true);
        spawnParticleBurst(ctx.state, {
          x: Transform.posX[eid],
          y: Transform.posY[eid] + 1.0,
          z: Transform.posZ[eid],
          preset: 'sparks',
          count: 6,
          duration: 0.4,
        });
      }
      s.lastHp = currentHp;

      if (isDead(eid)) {
        handleDeath(ctx, s, eid);
        return;
      }

      const playerEid = findPlayer(ctx);
      let inCombat = false;
      let lunging = false;

      const homeDx = s.originX - x;
      const homeDz = s.originZ - z;
      const homeDistSq = homeDx * homeDx + homeDz * homeDz;
      const leashed = homeDistSq > LEASH_RADIUS * LEASH_RADIUS;

      if (playerEid > 0 && Health.max[playerEid] > 0 && !leashed) {
        const px = Transform.posX[playerEid];
        const pz = Transform.posZ[playerEid];
        const dx = px - x;
        const dz = pz - z;
        const distSq = dx * dx + dz * dz;
        const homeToPlayerSq =
          (px - s.originX) * (px - s.originX) +
          (pz - s.originZ) * (pz - s.originZ);

        if (
          distSq <= ATTACK_RANGE * ATTACK_RANGE &&
          homeToPlayerSq <= LEASH_RADIUS * LEASH_RADIUS
        ) {
          s.combatState = 'attack';
          inCombat = true;
          s.targetHeading = Math.atan2(dx, dz);

          if (s.lungePhase === 'ready') {
            s.attackTimer -= dt;
            if (s.attackTimer <= 0) {
              s.lungePhase = 'windup';
              s.lungeTimer = LUNGE_WINDUP;
              const len = Math.sqrt(distSq) || 1;
              s.lungeDirX = dx / len;
              s.lungeDirZ = dz / len;
              s.playing = '';
            }
          } else if (s.lungePhase === 'windup') {
            s.lungeTimer -= dt;
            if (s.lungeTimer <= 0) {
              s.lungePhase = 'lunge';
              s.lungeTimer = LUNGE_DURATION;
            }
          } else if (s.lungePhase === 'lunge') {
            lunging = true;
            s.lungeTimer -= dt;
            if (s.lungeTimer <= 0) {
              const px2 = Transform.posX[playerEid];
              const pz2 = Transform.posZ[playerEid];
              const ddx = px2 - Transform.posX[eid];
              const ddz = pz2 - Transform.posZ[eid];
              if (
                ddx * ddx + ddz * ddz <=
                LUNGE_HIT_RANGE * LUNGE_HIT_RANGE
              ) {
                damageHealth(playerEid, cfg.attackDamage);
                spawnParticleBurst(ctx.state, {
                  x: px2,
                  y: Transform.posY[playerEid] + 1.0,
                  z: pz2,
                  preset: 'sparks',
                  count: 8,
                  duration: 0.4,
                });
                playSound('hit');
              }
              s.lungePhase = 'recovery';
              s.lungeTimer = LUNGE_RECOVERY;
              s.attackTimer = ATTACK_COOLDOWN;
            }
          } else if (s.lungePhase === 'recovery') {
            s.lungeTimer -= dt;
            if (s.lungeTimer <= 0) s.lungePhase = 'ready';
          }
        } else if (
          distSq <= DETECT_RANGE * DETECT_RANGE &&
          homeToPlayerSq <= LEASH_RADIUS * LEASH_RADIUS
        ) {
          s.combatState = 'chase';
          inCombat = true;
          s.targetHeading = Math.atan2(dx, dz);
          s.lungePhase = 'ready';
          s.lungeTimer = 0;
        } else {
          s.combatState = 'idle';
          s.lungePhase = 'ready';
        }
      } else {
        s.combatState = 'idle';
        s.lungePhase = 'ready';
      }

      if (s.combatState === 'chase' || s.combatState === 'attack') {
        aggroEntities.add(eid);
      } else {
        aggroEntities.delete(eid);
      }

      if (!inCombat) {
        s.stateTimer -= dt;
        if (s.stateTimer <= 0) {
          s.hovering = !s.hovering;
          if (s.hovering) {
            s.stateTimer = HOVER_MIN + Math.random() * (HOVER_MAX - HOVER_MIN);
          } else {
            s.stateTimer =
              HOVER_MIN * 0.6 + Math.random() * (HOVER_MAX * 0.6 - HOVER_MIN * 0.6);
            s.targetHeading =
              s.heading + (Math.random() - 0.5) * Math.PI * 1.4;
            const angle = Math.random() * Math.PI * 2;
            const dist =
              WANDER_PICK_DIST_MIN +
              Math.random() * (cfg.wanderRadius * 0.6 - WANDER_PICK_DIST_MIN);
            s.wanderTargetX = s.originX + Math.sin(angle) * dist;
            s.wanderTargetZ = s.originZ + Math.cos(angle) * dist;
          }
        }

        const homeDx2 = s.originX - x;
        const homeDz2 = s.originZ - z;
        if (
          homeDx2 * homeDx2 + homeDz2 * homeDz2 >
          cfg.wanderRadius * cfg.wanderRadius
        ) {
          s.targetHeading = Math.atan2(homeDx2, homeDz2);
          s.hovering = false;
          s.wanderTargetX = s.originX;
          s.wanderTargetZ = s.originZ;
        }
      }

      const navReady = isNavMeshReady();

      if (lunging && NavMeshAgent.agentIndex[eid] !== -1) {
        removeAgent(ctx.state, eid);
        NavMeshAgent.enabled[eid] = 0;
      } else if (!lunging && NavMeshAgent.enabled[eid] === 0 && navReady) {
        NavMeshAgent.enabled[eid] = 1;
        s.navInitialized = false;
        s.agentCreated = false;
      }

      const hasAgent = navReady && NavMeshAgent.agentIndex[eid] !== -1;

      if (lunging) {
        let nx = x + s.lungeDirX * LUNGE_SPEED * dt;
        let nz = z + s.lungeDirZ * LUNGE_SPEED * dt;
        // Clamp the dash so the creature halts at a frontal standoff instead of
        // sliding through / onto the player.
        if (playerEid > 0) {
          const pdx = nx - Transform.posX[playerEid];
          const pdz = nz - Transform.posZ[playerEid];
          const pd = Math.hypot(pdx, pdz);
          if (pd < LUNGE_STANDOFF) {
            const ux = pd > 1e-3 ? pdx / pd : -s.lungeDirX;
            const uz = pd > 1e-3 ? pdz / pd : -s.lungeDirZ;
            nx = Transform.posX[playerEid] + ux * LUNGE_STANDOFF;
            nz = Transform.posZ[playerEid] + uz * LUNGE_STANDOFF;
          }
        }
        const aheadY = groundHeight(ctx, nx, nz, Transform.posY[eid]);
        if (Number.isFinite(aheadY) && aheadY >= WATER_LEVEL) {
          const groundY = footprintHeight(ctx, nx, nz, Transform.posY[eid]);
          if (Number.isFinite(groundY)) {
            Transform.posX[eid] = nx;
            Transform.posY[eid] = groundY + s.footOffset;
            Transform.posZ[eid] = nz;
            Transform.dirty[eid] = 1;
            s.group.position.set(nx, groundY + s.footOffset, nz);
          }
        }
        s.heading = Math.atan2(s.lungeDirX, s.lungeDirZ);
        s.group.rotation.set(0, s.heading, 0);
        if (s.animator && s.playing !== cfg.clips.lunge) {
          s.animator.play(cfg.clips.lunge, { loop: false });
          s.playing = cfg.clips.lunge;
        }
      } else if (hasAgent) {
        if (!s.agentCreated) s.agentCreated = true;
        updateNavMeshMovement(ctx, s, eid, playerEid, dt);
      } else {
        const turnRate = inCombat ? TURN_RATE : TURN_RATE * 0.6;
        const err = Math.atan2(
          Math.sin(s.targetHeading - s.heading),
          Math.cos(s.targetHeading - s.heading)
        );
        const maxTurn = turnRate * dt;
        s.heading += Math.min(maxTurn, Math.max(-maxTurn, err));

        let targetSpeed: number;
        if (s.lungePhase === 'windup' || s.lungePhase === 'recovery') {
          targetSpeed = 0;
        } else if (s.combatState === 'chase') {
          targetSpeed = cfg.chaseSpeed;
        } else if (s.combatState === 'attack') {
          targetSpeed = 0;
        } else {
          targetSpeed = s.hovering ? 0 : cfg.wanderSpeed;
        }
        if (s.speed < targetSpeed)
          s.speed = Math.min(targetSpeed, s.speed + ACCEL * dt);
        else if (s.speed > targetSpeed)
          s.speed = Math.max(targetSpeed, s.speed - ACCEL * dt);

        let nx = x;
        let nz = z;
        if (s.speed > 0.001) {
          nx = x + Math.sin(s.heading) * s.speed * dt;
          nz = z + Math.cos(s.heading) * s.speed * dt;
        }
        if (nx !== x || nz !== z) {
          const aheadY = groundHeight(ctx, nx, nz, Transform.posY[eid]);
          if (!Number.isFinite(aheadY) || aheadY < WATER_LEVEL) {
            s.targetHeading = s.heading + Math.PI;
            nx = x;
            nz = z;
          }
        }

        const groundY = footprintHeight(ctx, nx, nz, Transform.posY[eid]);
        if (!Number.isFinite(groundY)) return;

        let clip: string;
        if (s.lungePhase !== 'ready') {
          clip = cfg.clips.lunge;
        } else if (s.combatState === 'chase') {
          clip = cfg.clips.run;
        } else {
          clip = s.speed > 0.15 ? cfg.clips.walk : cfg.clips.idle;
        }
        if (s.animator && s.playing !== clip) {
          if (clip === cfg.clips.lunge) {
            s.animator.play(cfg.clips.lunge, { loop: false });
          } else {
            s.animator.play(clip);
          }
          s.playing = clip;
        }

        Transform.posX[eid] = nx;
        Transform.posY[eid] = groundY + s.footOffset;
        Transform.posZ[eid] = nz;
        s.group.position.set(nx, groundY + s.footOffset, nz);
        s.group.rotation.set(0, s.heading, 0);
      }

      ensureHealthBar(s);
      const showBar = inCombat;
      if (s.healthBarBg) s.healthBarBg.visible = showBar;
      if (s.healthBarFill) {
        s.healthBarFill.visible = showBar;
        if (showBar) {
          const hpMax = Health.max[eid] || cfg.hp;
          const ratio = Math.max(0, Math.min(1, currentHp / hpMax));
          s.healthBarFill.scale.x = ratio;
          s.healthBarFill.position.x = -(HEALTH_BAR_WIDTH / 2) * (1 - ratio);
          const mat = s.healthBarFill.material as THREE.MeshBasicMaterial;
          mat.color.setHex(
            ratio > 0.5 ? 0x22cc22 : ratio > 0.25 ? 0xeecc22 : 0xee2222
          );
        }
      }
      if (showBar && s.healthBarBg && s.healthBarFill) {
        const cam = threeCameras.values().next().value;
        if (cam) {
          s.group.updateWorldMatrix(true, false);
          s.healthBarBg.lookAt(cam.position);
          s.healthBarFill.lookAt(cam.position);
        }
      }
    },
  };

  return behaviours;
}
