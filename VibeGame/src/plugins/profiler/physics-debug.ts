/**
 * Physics debug snapshot para a aba Physics do profiler.
 *
 * Detalhes finos do mundo Rapier + contagem de componentes de física ECS:
 * corpos por tipo (fixed/dynamic/kinematic), dormindo vs acordados, colisores
 * por forma (inclui cápsulas/cilindros do precompute), CCTs grounded, estados
 * do sync (entityToRigidbody/entityToCollider, falhas, removals) e timings
 * dos sistemas do bucket `fixed`.
 *
 * As contagens ECS (bodies/colliders/CCT por forma) estão sempre disponíveis —
 * mesmo sem mundo Rapier (boot, headless) — para o painel nunca ficar vazio;
 * os campos `rapier`/`sleep`/`sync` só ganham valores quando o contexto de
 * física existe. `available` distingue os dois casos.
 *
 * O texto da aba fica compacto; `__VIBEGAME__.profiler.physicsSnapshot()`
 * devolve o payload rico.
 */
import { defineQuery, type State } from '../../core';
import { getProfilerSnapshot } from '../../core/profiler';
import {
  BodyType,
  CharacterController,
  Collider,
  ColliderShape,
  Rigidbody,
} from '../physics/components';
import { getPhysicsContext } from '../physics/systems';

const colliderQuery = defineQuery([Collider]);
const bodyQuery = defineQuery([Rigidbody]);
const cctQuery = defineQuery([CharacterController]);

export interface PhysicsDebugSnapshot {
  /** `false` quando não há mundo Rapier (headless / sem PhysicsPlugin). */
  available: boolean;
  frame: number;
  bodies: {
    total: number;
    fixed: number;
    dynamic: number;
    kinematic: number;
    sleeping: number;
    awake: number;
  };
  colliders: {
    total: number;
    sensors: number;
    byShape: Record<string, number>;
  };
  cct: { total: number; grounded: number };
  rapier: {
    bodyCount: number;
    colliderCount: number;
    controllerCount: number;
    timestep: number;
  };
  sync: {
    rigidbodies: number;
    colliders: number;
    failedBodies: number;
    failedColliders: number;
    removalsDirty: number;
  };
  /** Timings dos sistemas de física (bucket fixed), ordenados por avg desc. */
  systems: { name: string; avgMs: number; lastMs: number; p95Ms: number }[];
  /** Colisores do precompute: cápsulas/cilindros resolvidos + markers pendentes. */
  precompute: { capsules: number; cylinders: number; unresolved: number };
}

const SHAPE_NAMES: Record<number, string> = {
  [ColliderShape.Box]: 'box',
  [ColliderShape.Sphere]: 'sphere',
  [ColliderShape.Capsule]: 'capsule',
  [ColliderShape.TriMesh]: 'trimesh',
  [ColliderShape.ConvexHull]: 'hull',
  [ColliderShape.Cylinder]: 'cylinder',
  [ColliderShape.Precompute]: 'precompute',
};

/** Prefixos dos sistemas do bucket fixed que interessam à aba. */
const PHYSICS_SYSTEM_PREFIXES = [
  'Physics',
  'Character',
  'CollisionEvent',
  'Kinematic',
  'Teleportation',
  'ApplyInput',
  'SetVelocity',
  'ApplyForce',
  'ApplyImpulse',
  'SetLinearVelocity',
  'SetAngularVelocity',
];

/** Last State seen by the profiler panel (for bridge `physicsSnapshot()`). */
let boundState: State | null = null;

export function bindPhysicsDebugState(state: State): void {
  boundState = state;
}

/** Snapshot from the bound panel State, or null if profiler never refreshed. */
export function getBoundPhysicsDebugSnapshot(): PhysicsDebugSnapshot | null {
  if (!boundState) return null;
  return getPhysicsDebugSnapshot(boundState);
}

export function getPhysicsDebugSnapshot(state: State): PhysicsDebugSnapshot {
  const frame = state.time.frameCount;
  const context = getPhysicsContext(state);
  const world = context.physicsWorld;

  // --- Contagens ECS (sempre disponíveis, sem depender do mundo Rapier) -----
  const bodies = {
    total: 0,
    fixed: 0,
    dynamic: 0,
    kinematic: 0,
    sleeping: 0,
    awake: 0,
  };
  for (const entity of bodyQuery(state.world)) {
    bodies.total++;
    const type = Rigidbody.type[entity];
    if (type === BodyType.Fixed) bodies.fixed++;
    else if (
      type === BodyType.KinematicPositionBased ||
      type === BodyType.KinematicVelocityBased
    ) {
      bodies.kinematic++;
    } else {
      bodies.dynamic++;
    }
  }

  const byShape: Record<string, number> = {};
  let sensors = 0;
  let colliderTotal = 0;
  for (const eid of colliderQuery(state.world)) {
    colliderTotal++;
    if (Collider.isSensor[eid] === 1) sensors++;
    const name =
      SHAPE_NAMES[Collider.shape[eid]] ?? `shape-${Collider.shape[eid]}`;
    byShape[name] = (byShape[name] ?? 0) + 1;
  }

  let cctTotal = 0;
  let grounded = 0;
  for (const eid of cctQuery(state.world)) {
    cctTotal++;
    if (CharacterController.grounded[eid] === 1) grounded++;
  }

  // --- Campos condicionais ao mundo Rapier ----------------------------------
  let rapier = {
    bodyCount: 0,
    colliderCount: 0,
    controllerCount: 0,
    timestep: 0,
  };
  const sync = {
    rigidbodies: context.entityToRigidbody.size,
    colliders: context.entityToCollider.size,
    failedBodies: context.failedRigidbodies.size,
    failedColliders: context.failedColliders.size,
    removalsDirty: context.removalsDirty ? 1 : 0,
  };
  if (world) {
    // isSleeping() é WASM — aceitável ao ritmo de refresh do painel.
    for (const body of context.entityToRigidbody.values()) {
      if (body.isSleeping()) bodies.sleeping++;
      else bodies.awake++;
    }
    rapier = {
      bodyCount: world.bodies.len(),
      colliderCount: world.colliders.len(),
      controllerCount: world.characterControllers.size,
      timestep: world.timestep,
    };
  }

  const systems = getProfilerSnapshot()
    .systems.filter(
      (s) =>
        s.group === 'fixed' &&
        PHYSICS_SYSTEM_PREFIXES.some((p) => s.name.startsWith(p))
    )
    .map((s) => ({
      name: s.name,
      avgMs: s.avgMs,
      lastMs: s.lastMs,
      p95Ms: s.p95Ms,
    }))
    .sort((a, b) => b.avgMs - a.avgMs);

  return {
    available: world !== null,
    frame,
    bodies,
    colliders: { total: colliderTotal, sensors, byShape },
    cct: { total: cctTotal, grounded },
    rapier,
    sync,
    systems,
    precompute: {
      capsules: byShape['capsule'] ?? 0,
      cylinders: byShape['cylinder'] ?? 0,
      unresolved: byShape['precompute'] ?? 0,
    },
  };
}

function fmt(v: number, digits = 2): string {
  return v.toFixed(digits);
}

export function renderPhysicsTab(snap: PhysicsDebugSnapshot): string {
  if (!snap.available) {
    return 'Physics: (sem mundo Rapier — PhysicsPlugin não registado ou headless)';
  }
  const lines: string[] = [];
  lines.push(
    `frame=${snap.frame}  bodies=${snap.bodies.total} (fixed ${snap.bodies.fixed} · dyn ${snap.bodies.dynamic} · kin ${snap.bodies.kinematic})`
  );
  lines.push(
    `  sleep    ${snap.bodies.sleeping} dormindo · ${snap.bodies.awake} acordados`
  );
  const shapes = Object.entries(snap.colliders.byShape)
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `${k} ${v}`)
    .join('  ');
  lines.push(
    `colliders ${snap.colliders.total}  sensors=${snap.colliders.sensors}`
  );
  lines.push(`  ${shapes}`);
  lines.push(
    `precompute capsules=${snap.precompute.capsules} · cylinders=${snap.precompute.cylinders} · unresolved=${snap.precompute.unresolved}`
  );
  lines.push(`CCT       ${snap.cct.total}  grounded=${snap.cct.grounded}`);
  lines.push(
    `rapier    bodies=${snap.rapier.bodyCount}  colliders=${snap.rapier.colliderCount}  controllers=${snap.rapier.controllerCount}  timestep=${fmt(snap.rapier.timestep, 4)}`
  );
  lines.push(
    `sync      rb=${snap.sync.rigidbodies}  col=${snap.sync.colliders}  failedRb=${snap.sync.failedBodies}  failedCol=${snap.sync.failedColliders}  removals=${snap.sync.removalsDirty}`
  );
  lines.push('');
  lines.push('Fixed systems (avg / last / p95 ms):');
  for (const s of snap.systems.slice(0, 12)) {
    lines.push(
      `  ${s.name.padEnd(34)} ${fmt(s.avgMs).padStart(6)}  ${fmt(s.lastMs).padStart(6)}  ${fmt(s.p95Ms).padStart(6)}`
    );
  }
  return lines.join('\n');
}
