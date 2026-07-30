import { BatchedRenderer, ParticleSystem, RenderMode } from 'three.quarks';
import type { ParticleSystemParameters } from 'three.quarks';
import { ConstantValue, IntervalValue } from 'quarks.core';
import { ColorRange } from 'quarks.core';
import { Vector4 } from 'quarks.core';
import { defineQuery } from '../../core';
import type { State, System } from '../../core';
import { logger } from '../../core/utils/logger';
import { getScene } from '../rendering';
import { WorldTransform } from '../transforms';
import { ParticleEmitter } from './components';
import {
  createPresetParams,
  presetName,
  presetNeedsUprightCone,
} from './presets';
import { preloadParticleTextures } from './textures';
import { defineSystem } from '../../core';

const emitterQuery = defineQuery([ParticleEmitter]);

// Soft budget: caps total emitter entities to keep three.quarks batch + GPU
// draw cost bounded under heavy spawn scenarios (particle combat FX, weather).
const MAX_PARTICLE_EMITTERS = 64;

const stateRendererMap = new WeakMap<State, BatchedRenderer>();
const stateParticleSystems = new WeakMap<State, Map<number, ParticleSystem>>();

function getRenderer(state: State): BatchedRenderer | undefined {
  return stateRendererMap.get(state);
}

function getParticleSystems(state: State): Map<number, ParticleSystem> {
  let map = stateParticleSystems.get(state);
  if (!map) {
    map = new Map();
    stateParticleSystems.set(state, map);
  }
  return map;
}

/**
 * Apply the emitter's `shape-radius` / `shape-angle` overrides onto the preset's
 * emitter shape. Both fields default to 0, meaning "keep the preset's value" —
 * before this, they were parsed, defaulted and documented but never read, so a
 * campfire and a torch were stuck with the same 0.12 m fire cone.
 *
 * The preset factory builds a fresh emitter per call, so mutating in place is
 * safe — no shape instance is shared between particle systems.
 */
function applyShapeOverrides(
  shape: ParticleSystemParameters['shape'],
  entity: number
): ParticleSystemParameters['shape'] {
  if (!shape) return shape;
  const radius = ParticleEmitter.shapeRadius[entity];
  const angle = ParticleEmitter.shapeAngle[entity];
  const target = shape as unknown as { radius?: number; angle?: number };
  if (radius > 0 && typeof target.radius === 'number') target.radius = radius;
  if (angle > 0 && typeof target.angle === 'number') target.angle = angle;
  return shape;
}

function createParticleSystem(
  state: State,
  entity: number
): ParticleSystem | null {
  const scene = getScene(state);
  if (!scene) return null;

  let renderer = getRenderer(state);
  if (!renderer) {
    renderer = new BatchedRenderer();
    scene.add(renderer);
    stateRendererMap.set(state, renderer);
  }

  const name = presetName(ParticleEmitter.preset[entity]);
  const presetParams = createPresetParams(name);
  // Continuous scene emitters (XML) may tune rate/size/speed; bursts keep preset look.
  const preferEntityTuning = ParticleEmitter.burst[entity] !== 1;

  const params: ParticleSystemParameters = {
    autoDestroy: false,
    looping: ParticleEmitter.looping[entity] === 1,
    duration: ParticleEmitter.duration[entity] || presetParams.duration || 5,
    startLife: preferEntityTuning
      ? new IntervalValue(
          ParticleEmitter.startLifeMin[entity],
          ParticleEmitter.startLifeMax[entity]
        )
      : (presetParams.startLife ??
        new IntervalValue(
          ParticleEmitter.startLifeMin[entity],
          ParticleEmitter.startLifeMax[entity]
        )),
    startSpeed: preferEntityTuning
      ? new IntervalValue(
          ParticleEmitter.startSpeedMin[entity],
          ParticleEmitter.startSpeedMax[entity]
        )
      : (presetParams.startSpeed ??
        new IntervalValue(
          ParticleEmitter.startSpeedMin[entity],
          ParticleEmitter.startSpeedMax[entity]
        )),
    startSize: preferEntityTuning
      ? new IntervalValue(
          ParticleEmitter.startSizeMin[entity],
          ParticleEmitter.startSizeMax[entity]
        )
      : (presetParams.startSize ??
        new IntervalValue(
          ParticleEmitter.startSizeMin[entity],
          ParticleEmitter.startSizeMax[entity]
        )),
    startColor:
      presetParams.startColor ??
      new ColorRange(
        new Vector4(
          ParticleEmitter.startColorR[entity],
          ParticleEmitter.startColorG[entity],
          ParticleEmitter.startColorB[entity],
          ParticleEmitter.startColorA[entity]
        ),
        new Vector4(
          ParticleEmitter.startColorR[entity],
          ParticleEmitter.startColorG[entity],
          ParticleEmitter.startColorB[entity],
          ParticleEmitter.startColorA[entity]
        )
      ),
    emissionOverTime: preferEntityTuning
      ? new ConstantValue(ParticleEmitter.emissionRate[entity])
      : (presetParams.emissionOverTime ??
        new ConstantValue(ParticleEmitter.emissionRate[entity])),
    shape: applyShapeOverrides(presetParams.shape, entity),
    material: presetParams.material!,
    worldSpace:
      typeof presetParams.worldSpace === 'boolean'
        ? presetParams.worldSpace
        : ParticleEmitter.worldSpace[entity] === 1,
    renderMode:
      (ParticleEmitter.renderMode[entity] as RenderMode) ||
      RenderMode.BillBoard,
    behaviors: presetParams.behaviors ? [...presetParams.behaviors] : [],
    ...(presetParams.emissionBursts
      ? { emissionBursts: presetParams.emissionBursts }
      : {}),
  };

  const ps = new ParticleSystem(params);

  if (ParticleEmitter.burst[entity] === 1) {
    ps.looping = false;
    ps.autoDestroy = true;
  }

  // ConeEmitter emits along local +Z — tip upright to world +Y.
  if (presetNeedsUprightCone(name)) {
    ps.emitter.rotation.x = -Math.PI / 2;
  }

  scene.add(ps.emitter);
  renderer.addSystem(ps);

  if (state.hasComponent(entity, WorldTransform)) {
    ps.emitter.position.set(
      WorldTransform.posX[entity],
      WorldTransform.posY[entity],
      WorldTransform.posZ[entity]
    );
  }

  return ps;
}

function destroyParticleSystem(state: State, entity: number): void {
  const systems = getParticleSystems(state);
  const ps = systems.get(entity);
  if (!ps) return;

  const scene = getScene(state);
  if (scene) {
    scene.remove(ps.emitter);
  }

  const renderer = getRenderer(state);
  if (renderer) {
    renderer.deleteSystem(ps);
  }

  ps.dispose();
  systems.delete(entity);
}

export const ParticleUpdateSystem: System = defineSystem({
  name: 'ParticleUpdateSystem',
  group: 'draw',

  setup(state: State) {
    const scene = getScene(state);
    if (!scene) return;

    preloadParticleTextures();

    let renderer = getRenderer(state);
    if (!renderer) {
      renderer = new BatchedRenderer();
      scene.add(renderer);
      stateRendererMap.set(state, renderer);
    }

    getParticleSystems(state);
  },

  update(state: State) {
    if (state.headless) return;

    const scene = getScene(state);
    if (!scene) return;

    const renderer = getRenderer(state);
    if (!renderer) return;

    const systems = getParticleSystems(state);
    const delta = state.time.deltaTime;
    const emitters = emitterQuery(state.world);
    const emitterCount = emitters.length;

    // No emitters and no live systems → skip BatchedRenderer.update.
    if (emitterCount === 0 && systems.size === 0) return;

    let anyActive = systems.size > 0;
    for (const entity of emitters) {
      if (ParticleEmitter.active[entity] !== 1) continue;

      let ps = systems.get(entity);
      if (!ps) {
        if (emitterCount >= MAX_PARTICLE_EMITTERS) {
          logger.warn(
            `Particle emitter cap reached (MAX_PARTICLE_EMITTERS=${MAX_PARTICLE_EMITTERS}); skipping new emitter`
          );
          continue;
        }
        const created = createParticleSystem(state, entity);
        if (created) {
          systems.set(entity, created);
          ps = created;
          // Tear down on destroy (not via an exists() sweep): a recycled eid
          // would pass exists() and silently reuse this dead system. See
          // eid-recycling-sidecars.
          state.onDestroy(entity, () => destroyParticleSystem(state, entity));
        }
        if (!ps) continue;
      }

      anyActive = true;

      if (state.hasComponent(entity, WorldTransform)) {
        const x = WorldTransform.posX[entity];
        const y = WorldTransform.posY[entity];
        const z = WorldTransform.posZ[entity];
        const em = ps.emitter.position;
        if (em.x !== x || em.y !== y || em.z !== z) {
          em.set(x, y, z);
        }
      }

      if (ParticleEmitter.burst[entity] === 1 && ps.time >= ps.duration) {
        ParticleEmitter.active[entity] = 0;
        state.destroyEntity(entity);
      }
    }

    if (anyActive || systems.size > 0) {
      renderer.update(delta);
    }
  },

  dispose(state: State) {
    const systems = getParticleSystems(state);
    const scene = getScene(state);
    const renderer = getRenderer(state);

    for (const [entity] of systems) {
      const ps = systems.get(entity);
      if (ps) {
        if (scene) scene.remove(ps.emitter);
        ps.dispose();
      }
    }
    systems.clear();

    if (renderer && scene) {
      scene.remove(renderer);
    }

    stateRendererMap.delete(state);
    stateParticleSystems.delete(state);
  },
});
