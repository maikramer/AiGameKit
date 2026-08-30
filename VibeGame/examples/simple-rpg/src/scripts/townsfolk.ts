// Transeuntes ambientes de Discordia: aldeões que patrulham ciclos fechados
// pelas ruas (só no pad plano da cidade), com walk/idle/gestos. Um script só
// serve N entidades: cada uma adota a rota cujo primeiro ponto está mais
// perto do seu spawn — sem atributos XML, sem estado por ficheiro.
import * as THREE from 'three';
import { loadGltfToSceneWithAnimator } from 'vibegame';
import type { GltfAnimator, MonoBehaviourContext } from 'vibegame';
import { Transform } from 'vibegame';
import { isGamePaused } from '../game/pause.ts';
import { NpcIdleAnimator } from '../game/npc-anims.ts';

const WALK_SPEED = 1.15;
const ARRIVE_DIST_SQ = 0.36;
const IDLE_MIN = 3;
const IDLE_MAX = 7;
const TURN_SPEED = 6;

interface Route {
  model: string;
  gestures: string[];
  /** Ciclo fechado em coords de mundo (x, z) sobre ruas/pad plano. */
  loop: Array<[number, number]>;
}

const SCOUT = '/assets/meshes/characters/npc_scout_lod2.glb';
const ELDER = '/assets/meshes/characters/npc_elder_lod2.glb';

const ROUTES: Route[] = [
  {
    // Volta à praça por dentro (x=±5, z=±10.5): folga ~1.5 m do poço,
    // fogueira, bancos e tochas — o ciclo fica entre o poço e o lume.
    model: SCOUT,
    gestures: ['talk', 'lean', 'call'],
    loop: [
      [5, 10.5],
      [-5, 10.5],
      [-5, -10.5],
      [5, -10.5],
    ],
  },
  {
    // Artéria norte: praça → ampliação → mid_n → portão e volta.
    model: SCOUT,
    gestures: ['talk', 'call', 'yes'],
    loop: [
      [0, -10],
      [0, 14],
      [-4, 22],
      [-9, 27],
      [-4, 28],
      [0, 33],
      [3, 22],
      [0, 8],
    ],
  },
  {
    // Artéria este até mid_e e volta (passa pelo mercado).
    model: ELDER,
    gestures: ['talk', 'yes', 'no'],
    loop: [
      [6, 0],
      [16, 0],
      [26, 0],
      [32, 2],
      [26, 0],
      [16, 0],
      [8, -4],
    ],
  },
];

interface Walker {
  group: THREE.Group;
  animator: GltfAnimator;
  idle: NpcIdleAnimator;
  route: Route;
  leg: number;
  /** Segundos restantes parado no waypoint atual. */
  wait: number;
  yaw: number;
}

const walkers = new Map<number, Walker>();

function pickRoute(x: number, z: number): Route {
  let best = ROUTES[0];
  let bestDist = Infinity;
  for (const route of ROUTES) {
    const [wx, wz] = route.loop[0];
    const d = (wx - x) * (wx - x) + (wz - z) * (wz - z);
    if (d < bestDist) {
      bestDist = d;
      best = route;
    }
  }
  return best;
}

export function start(ctx: MonoBehaviourContext): void {
  const route = pickRoute(ctx.transform.positionX, ctx.transform.positionZ);
  void loadGltfToSceneWithAnimator(ctx.state, route.model, {
    crossfadeDuration: 0.3,
  }).then((result) => {
    const animator = result.animator;
    if (!animator) return;
    const idle = new NpcIdleAnimator({ idle: 'idle', gestures: route.gestures });
    animator.play('idle');
    idle.start(animator);
    result.group.position.set(
      ctx.transform.positionX,
      ctx.transform.positionY,
      ctx.transform.positionZ
    );
    walkers.set(ctx.entity, {
      group: result.group,
      animator,
      idle,
      route,
      leg: 0,
      wait: 1 + Math.random() * 3,
      yaw: 0,
    });
  });
}

export function update(ctx: MonoBehaviourContext): void {
  const walker = walkers.get(ctx.entity);
  if (!walker) return;
  if (isGamePaused()) return;
  walker.animator.update(ctx.deltaTime);
  walker.idle.update(ctx.deltaTime, walker.animator);

  const eid = ctx.entity;
  const x = Transform.posX[eid];
  const z = Transform.posZ[eid];

  if (walker.wait > 0) {
    walker.wait -= ctx.deltaTime;
    return;
  }

  const [tx, tz] = walker.route.loop[walker.leg];
  const dx = tx - x;
  const dz = tz - z;
  const distSq = dx * dx + dz * dz;
  if (distSq < ARRIVE_DIST_SQ) {
    walker.leg = (walker.leg + 1) % walker.route.loop.length;
    walker.wait = IDLE_MIN + Math.random() * (IDLE_MAX - IDLE_MIN);
    walker.animator.play('idle');
    return;
  }

  const step = WALK_SPEED * ctx.deltaTime;
  const inv = 1 / Math.sqrt(distSq);
  Transform.posX[eid] = x + dx * inv * step;
  Transform.posZ[eid] = z + dz * inv * step;
  // Direct Transform writes need the dirty latch or the world-transform
  // (distance cull, minimap, bridge position()) stays at the spawn point.
  Transform.dirty[eid] = 1;

  const targetYaw = Math.atan2(dx, dz);
  const err = Math.atan2(
    Math.sin(targetYaw - walker.yaw),
    Math.cos(targetYaw - walker.yaw)
  );
  const maxTurn = TURN_SPEED * ctx.deltaTime;
  walker.yaw += Math.min(maxTurn, Math.max(-maxTurn, err));
  walker.animator.play('walk');

  walker.group.position.set(Transform.posX[eid], Transform.posY[eid], Transform.posZ[eid]);
  walker.group.rotation.set(0, walker.yaw, 0);
}

export function onDestroy(ctx: MonoBehaviourContext): void {
  walkers.delete(ctx.entity);
}
