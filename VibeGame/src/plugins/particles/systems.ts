import { hasComponent } from 'bitecs';
import {
  BatchedParticleRenderer,
} from 'three.quarks';
import { defineQuery, type System } from '../../core';
import { getScene } from '../rendering';
import { Transform, WorldTransform } from '../transforms';
import { ParticlesBurst, ParticlesEmitter } from './components';
import { getParticlesContext } from './context';
import { createParticleSystemForPreset } from './presets';

const emitterQuery = defineQuery([ParticlesEmitter, Transform]);
const burstQuery = defineQuery([ParticlesBurst, Transform]);

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
      ParticlesEmitter.spawned[eid] = 1;
    }

    for (const eid of emitterQuery(state.world)) {
      const root = ctx.roots.get(eid);
      if (!root) continue;
      const wx = hasComponent(state.world, WorldTransform, eid)
        ? WorldTransform.posX[eid]
        : Transform.posX[eid];
      const wy = hasComponent(state.world, WorldTransform, eid)
        ? WorldTransform.posY[eid]
        : Transform.posY[eid];
      const wz = hasComponent(state.world, WorldTransform, eid)
        ? WorldTransform.posZ[eid]
        : Transform.posZ[eid];
      root.position.set(wx, wy, wz);
    }
  },
};

export const ParticleBurstSystem: System = {
  group: 'simulation',
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
      const wx = hasComponent(state.world, WorldTransform, eid)
        ? WorldTransform.posX[eid]
        : Transform.posX[eid];
      const wy = hasComponent(state.world, WorldTransform, eid)
        ? WorldTransform.posY[eid]
        : Transform.posY[eid];
      const wz = hasComponent(state.world, WorldTransform, eid)
        ? WorldTransform.posZ[eid]
        : Transform.posZ[eid];
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
