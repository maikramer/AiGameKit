import { entityExists, hasComponent } from 'bitecs';
import * as THREE from 'three';
import type { Object3D } from 'three';
import { BatchedParticleRenderer } from 'three.quarks';
import type { ParticleSystem } from 'three.quarks';
import { Parent, defineQuery, type State, type System } from '../../core';
import { getScene } from '../rendering';
import { getTextureAsset } from '../rendering/texture-recipe-system';
import { TextureRecipe } from '../rendering/texture-recipe';
import {
  Transform,
  TransformHierarchySystem,
  WorldTransform,
} from '../transforms';
import {
  ColorOverLife,
  ParticleTexture,
  ParticlesBurst,
  ParticlesEmitter,
  SizeOverLife,
} from './components';
import { getParticlesContext } from './context';
import { createParticleSystemForPreset, psMaterialMap } from './presets';

/**
 * World position for the emitter Object3D (`scene.add(emitter)` — must be world space).
 * Child entities: use {@link WorldTransform}. Roots: {@link Transform}.
 * Runs after `TransformHierarchySystem`. Terrain placement for `<entity place="…">` runs **first**
 * in the simulation group so parent `Transform` is updated before hierarchy propagates to children.
 */
function getEmitterWorldPosition(
  state: State,
  eid: number
): [number, number, number] {
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

interface SpriteAnimData {
  frames: number;
  animationSpeed: number;
  elapsed: number;
}
const entitySpriteAnim = new Map<number, SpriteAnimData>();

const _tmpOffset = new THREE.Vector2();

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
  entitySpriteAnim.delete(eid);
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

      const colOL = hasComponent(state.world, ColorOverLife, eid)
        ? {
            startR: ColorOverLife.startR[eid],
            startG: ColorOverLife.startG[eid],
            startB: ColorOverLife.startB[eid],
            startA: ColorOverLife.startA[eid],
            endR: ColorOverLife.endR[eid],
            endG: ColorOverLife.endG[eid],
            endB: ColorOverLife.endB[eid],
            endA: ColorOverLife.endA[eid],
          }
        : undefined;

      const szOL = hasComponent(state.world, SizeOverLife, eid)
        ? {
            startSize: SizeOverLife.startSize[eid],
            endSize: SizeOverLife.endSize[eid],
          }
        : undefined;

      let texture: THREE.Texture | undefined;
      let spriteFrames = 1;
      let spriteSpeed = 1;

      if (hasComponent(state.world, TextureRecipe, eid)) {
        const loaded = getTextureAsset(eid);
        if (!loaded) continue;
        texture = loaded.clone();
        texture.needsUpdate = true;

        if (hasComponent(state.world, ParticleTexture, eid)) {
          spriteFrames = ParticleTexture.frames[eid] || 1;
          spriteSpeed = ParticleTexture.animationSpeed[eid] || 1;
          if (spriteFrames > 1) {
            texture.wrapS = THREE.RepeatWrapping;
            texture.wrapT = THREE.RepeatWrapping;
            texture.repeat.set(1 / spriteFrames, 1);
          }
        }
      }

      const ps = createParticleSystemForPreset(
        ParticlesEmitter.preset[eid],
        ParticlesEmitter.rate[eid],
        ParticlesEmitter.lifetime[eid],
        ParticlesEmitter.size[eid],
        batch,
        colOL,
        szOL,
        texture
      );
      const root = ps.emitter;
      const scene = getScene(state);
      if (scene) scene.add(root);
      ctx.roots.set(eid, root);
      entityToPS.set(eid, ps);

      if (spriteFrames > 1) {
        entitySpriteAnim.set(eid, {
          frames: spriteFrames,
          animationSpeed: spriteSpeed,
          elapsed: 0,
        });
      }

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

    for (const [eid, anim] of entitySpriteAnim) {
      if (!entityExists(state.world, eid)) continue;
      const ps = entityToPS.get(eid);
      if (!ps) continue;
      const mat = psMaterialMap.get(ps);
      if (!mat?.map) continue;
      anim.elapsed += state.time.deltaTime;
      const frame =
        Math.floor(anim.elapsed * anim.animationSpeed) % anim.frames;
      _tmpOffset.set(frame / anim.frames, 0);
      mat.map.offset.copy(_tmpOffset);
    }
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
