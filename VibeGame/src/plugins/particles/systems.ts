import { entityExists, hasComponent } from 'bitecs';
import type { Object3D } from 'three';
import { BatchedParticleRenderer } from 'three.quarks';
import type { ParticleSystem } from 'three.quarks';
import { Parent, defineQuery, type State, type System } from '../../core';
import { getScene } from '../rendering';
import { Transform, TransformHierarchySystem, WorldTransform } from '../transforms';
import { ParticlesBurst, ParticlesEmitter } from './components';
import { getParticlesContext } from './context';
import { createParticleSystemForPreset } from './presets';

/**
 * World position for the emitter Object3D (`scene.add(emitter)` — must be world space).
 * Child entities: use {@link WorldTransform}. Roots: {@link Transform}.
 * Runs after `TransformHierarchySystem`. Terrain placement for `<entity place="…">` runs **first**
 * in the simulation group so parent `Transform` is updated before hierarchy propagates to children.
 */
function getEmitterWorldPosition(state: State, eid: number): [number, number, number] {
  if (hasComponent(state.world, Parent, eid)) {
    if (hasComponent(state.world, WorldTransform, eid)) {
      return [
        WorldTransform.posX[eid],
        WorldTransform.posY[eid],
        WorldTransform.posZ[eid],
      ];
    }
  }
  return [Transform.posX[eid], Transform.posY[eid], Transform.posZ[eid]];
}

const emitterQuery = defineQuery([ParticlesEmitter, Transform]);
const burstQuery = defineQuery([ParticlesBurst, Transform]);

const entityToPS = new Map<number, ParticleSystem>();

function disposeParticle(
  eid: number,
  ctx: {
    batch: InstanceType<typeof BatchedParticleRenderer> | null;
    roots: Map<number, Object3D>;
  }
) {
  const ps = entityToPS.get(eid);
  const root = ctx.roots.get(eid);
  if (ps && ctx.batch) {
    ctx.batch.deleteSystem(ps);
  }
  if (root) {
    root.removeFromParent();
    ctx.roots.delete(eid);
  }
  if (ps) {
    ps.dispose();
    entityToPS.delete(eid);
  }
}

export const ParticleBootstrapSystem: System = {
  group: 'setup',
  update: (state) => {
    if (state.headless) return;
    const scene = getScene(state);
    if (!scene) return;
    const ctx = getParticlesContext(state);
    if (ctx.batch) return;
    const batch = new BatchedParticleRenderer();
    ctx.batch = batch;
    scene.add(batch);
  },
};

export const ParticleEmitSystem: System = {
  group: 'simulation',
  last: true,
  after: [TransformHierarchySystem],
  update: (state) => {
    if (state.headless) return;
    const ctx = getParticlesContext(state);
    let batch = ctx.batch;
    if (!batch) {
      const scene = getScene(state);
      if (!scene) return;
      batch = new BatchedParticleRenderer();
      ctx.batch = batch;
      scene.add(batch);
    }

    for (const eid of emitterQuery(state.world)) {
      if (ParticlesEmitter.spawned[eid]) continue;
      if (!ParticlesEmitter.playing[eid]) continue;

      const ps = createParticleSystemForPreset(
        ParticlesEmitter.preset[eid],
        ParticlesEmitter.rate[eid],
        ParticlesEmitter.lifetime[eid],
        ParticlesEmitter.size[eid],
        batch
      );
      const root = ps.emitter;
      const scene = getScene(state);
      if (scene) scene.add(root);
      ctx.roots.set(eid, root);
      entityToPS.set(eid, ps);
      ParticlesEmitter.spawned[eid] = 1;
    }

    for (const eid of emitterQuery(state.world)) {
      const root = ctx.roots.get(eid);
      if (!root) continue;
      const [wx, wy, wz] = getEmitterWorldPosition(state, eid);
      root.position.set(wx, wy, wz);
    }
  },
};

export const ParticleBurstSystem: System = {
  group: 'simulation',
  last: true,
  after: [ParticleEmitSystem],
  update: (state) => {
    if (state.headless) return;
    const ctx = getParticlesContext(state);
    let batch = ctx.batch;
    if (!batch) {
      const scene = getScene(state);
      if (!scene) return;
      batch = new BatchedParticleRenderer();
      ctx.batch = batch;
      scene.add(batch);
    }

    for (const eid of burstQuery(state.world)) {
      if (!ParticlesBurst.triggered[eid]) continue;

      if (entityToPS.has(eid)) {
        disposeParticle(eid, ctx);
      }

      const ps = createParticleSystemForPreset(
        ParticlesBurst.preset[eid],
        ParticlesBurst.count[eid],
        0.4,
        0.15,
        batch
      );
      const root = ps.emitter;
      const scene = getScene(state);
      if (scene) scene.add(root);
      ctx.roots.set(eid, root);
      entityToPS.set(eid, ps);
      const [wx, wy, wz] = getEmitterWorldPosition(state, eid);
      root.position.set(wx, wy, wz);
      ParticlesBurst.triggered[eid] = 0;
    }
  },
};

export const ParticleRenderSystem: System = {
  group: 'draw',
  update: (state) => {
    if (state.headless) return;
    const ctx = getParticlesContext(state);
    const batch = ctx.batch;
    if (!batch) return;
    batch.update(state.time.deltaTime);
  },
};

export const ParticleCleanupSystem: System = {
  group: 'draw',
  after: [ParticleRenderSystem],
  update: (state) => {
    if (state.headless) return;
    const ctx = getParticlesContext(state);
    if (!ctx.batch) return;
    for (const eid of entityToPS.keys()) {
      if (!entityExists(state.world, eid)) {
        disposeParticle(eid, ctx);
      }
    }
  },
};
