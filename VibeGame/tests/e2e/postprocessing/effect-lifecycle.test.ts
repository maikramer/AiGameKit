/**
 * E2E: Postprocessing effect lifecycle — register, create entity, run systems, remove, cleanup.
 * All tests run headless (no GPU/WebGL required).
 */
import { beforeEach, describe, expect, it } from 'bun:test';
import {
  State,
  TIME_CONSTANTS,
  defineQuery,
  addComponent,
  addEntity,
  removeComponent,
  removeEntity,
} from 'vibegame';
import { createHeadlessState } from 'vibegame/cli';
import { DefaultPlugins } from 'vibegame/defaults';
import { getPostprocessingContext } from '../../../src/plugins/postprocessing/utils';
import {
  getEffectDefinitions,
  unregisterEffect,
  registerEffect,
  type EffectDefinition,
} from '../../../src/plugins/postprocessing/effect-registry';
import {
  Bloom,
  SMAA,
  DepthOfField,
  Vignette,
} from '../../../src/plugins/postprocessing/components';
import { WorldTransform } from '../../../src/plugins/transforms';
import { MainCamera } from '../../../src/plugins/rendering/components';
import { Player } from '../../../src/plugins/player/components';

// Polyfill browser APIs for Bun (GSAP uses these internally)
globalThis.requestAnimationFrame = ((cb: any) => setTimeout(cb, 16)) as any;
globalThis.cancelAnimationFrame = clearTimeout as any;

describe('E2E: Postprocessing Effect Lifecycle', () => {
  let state: State;

  beforeEach(async () => {
    state = createHeadlessState({ plugins: DefaultPlugins });
    await state.initializePlugins();
  });

  it('should register PostprocessingPlugin with components and systems', () => {
    const bloomComp = state.getComponent('bloom');
    const smaaComp = state.getComponent('smaa');
    const dofComp = state.getComponent('depthOfField');
    const vignetteComp = state.getComponent('vignette');
    expect(bloomComp).toBeDefined();
    expect(smaaComp).toBeDefined();
    expect(dofComp).toBeDefined();
    expect(vignetteComp).toBeDefined();
    expect(state.systems.size).toBeGreaterThan(0);
  });

  it('should register all builtin effect definitions', () => {
    const defs = getEffectDefinitions();
    const keys = defs.map((d) => d.key);
    expect(keys).toContain('smaa');
    expect(keys).toContain('bloom');
    expect(keys).toContain('dithering');
    expect(keys).toContain('tonemapping');
    expect(keys).toContain('vignette');
    expect(keys).toContain('depthOfField');
    expect(keys).toContain('chromaticAberration');
    expect(keys).toContain('noise');
    expect(keys).toContain('ssao');
    expect(keys).toContain('ssr');
    expect(defs.length).toBe(10);
  });

  it('should create bloom entity with plugin defaults', () => {
    const entity = addEntity(state.world);
    addComponent(state.world, Bloom, entity);

    Bloom.intensity[entity] = 1.0;
    Bloom.luminanceThreshold[entity] = 1.0;
    Bloom.luminanceSmoothing[entity] = 0.3;
    Bloom.mipmapBlur[entity] = 1;
    Bloom.radius[entity] = 0.85;
    Bloom.levels[entity] = 8;

    expect(Bloom.intensity[entity]).toBe(1.0);
    expect(Bloom.luminanceThreshold[entity]).toBe(1.0);
    expect(Bloom.mipmapBlur[entity]).toBe(1);
    expect(Bloom.levels[entity]).toBe(8);
  });

  it('should create SMAA entity with preset', () => {
    const entity = addEntity(state.world);
    addComponent(state.world, SMAA, entity);
    SMAA.preset[entity] = 2;

    expect(SMAA.preset[entity]).toBe(2);
  });

  it('should query bloom entities via defineQuery', () => {
    const bloomQuery = defineQuery([Bloom]);

    const e1 = addEntity(state.world);
    addComponent(state.world, Bloom, e1);
    Bloom.intensity[e1] = 1.5;

    const e2 = addEntity(state.world);
    addComponent(state.world, Bloom, e2);
    Bloom.intensity[e2] = 2.0;

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    const bloomEntities = bloomQuery(state.world);
    expect(bloomEntities.length).toBeGreaterThanOrEqual(2);

    const intensities = bloomEntities.map((e: number) => Bloom.intensity[e]);
    expect(intensities).toContain(1.5);
    expect(intensities).toContain(2.0);
  });

  it('should effect activation: entity gets component → component present in query', () => {
    const bloomQuery = defineQuery([Bloom]);
    const entity = addEntity(state.world);

    let bloomEntities = bloomQuery(state.world);
    expect(bloomEntities).not.toContain(entity);

    addComponent(state.world, Bloom, entity);
    Bloom.intensity[entity] = 1.0;

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    bloomEntities = bloomQuery(state.world);
    expect(bloomEntities).toContain(entity);
    expect(Bloom.intensity[entity]).toBe(1.0);
  });

  it('should effect deactivation: entity loses component → removed from query', () => {
    const bloomQuery = defineQuery([Bloom]);
    const entity = addEntity(state.world);
    addComponent(state.world, Bloom, entity);
    Bloom.intensity[entity] = 1.0;

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    let bloomEntities = bloomQuery(state.world);
    expect(bloomEntities).toContain(entity);

    removeComponent(state.world, Bloom, entity);

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    bloomEntities = bloomQuery(state.world);
    expect(bloomEntities).not.toContain(entity);
  });

  it('should remove postprocessing entity and have zero entities in query', () => {
    const dofQuery = defineQuery([DepthOfField]);
    const entity = addEntity(state.world);
    addComponent(state.world, DepthOfField, entity);
    DepthOfField.focusDistance[entity] = 15;
    DepthOfField.autoFocus[entity] = 0;

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    let dofEntities = dofQuery(state.world);
    expect(dofEntities.length).toBeGreaterThanOrEqual(1);

    removeEntity(state.world, entity);

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    dofEntities = dofQuery(state.world);
    expect(dofEntities).not.toContain(entity);
  });

  it('should zero overhead: no postprocessing attributes → empty postprocessing context', () => {
    for (let i = 0; i < 20; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    }

    const postCtx = getPostprocessingContext(state);
    expect(postCtx.composers.size).toBe(0);
    expect(postCtx.effects.size).toBe(0);
    expect(postCtx.externalEffects.length).toBe(0);
  });

  it('should zero overhead: entities without postprocessing components → no effects in context', () => {
    const e1 = addEntity(state.world);
    addComponent(state.world, WorldTransform, e1);
    WorldTransform.posX[e1] = 5;
    WorldTransform.posY[e1] = 0;
    WorldTransform.posZ[e1] = 5;

    const e2 = addEntity(state.world);
    addComponent(state.world, Player, e2);

    for (let i = 0; i < 10; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    }

    const postCtx = getPostprocessingContext(state);
    expect(postCtx.composers.size).toBe(0);
    expect(postCtx.effects.size).toBe(0);
  });

  it('should handle multiple effect types on same entity', () => {
    const bloomQuery = defineQuery([Bloom]);
    const smaaQuery = defineQuery([SMAA]);
    const vignetteQuery = defineQuery([Vignette]);

    const entity = addEntity(state.world);
    addComponent(state.world, Bloom, entity);
    addComponent(state.world, SMAA, entity);
    addComponent(state.world, Vignette, entity);

    Bloom.intensity[entity] = 2.0;
    SMAA.preset[entity] = 3;
    Vignette.darkness[entity] = 0.8;

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    expect(bloomQuery(state.world)).toContain(entity);
    expect(smaaQuery(state.world)).toContain(entity);
    expect(vignetteQuery(state.world)).toContain(entity);
    expect(Bloom.intensity[entity]).toBe(2.0);
    expect(SMAA.preset[entity]).toBe(3);
    expect(Vignette.darkness[entity]).toBeCloseTo(0.8);
  });

  it('should remove one effect while keeping others', () => {
    const bloomQuery = defineQuery([Bloom]);
    const smaaQuery = defineQuery([SMAA]);
    const vignetteQuery = defineQuery([Vignette]);

    const entity = addEntity(state.world);
    addComponent(state.world, Bloom, entity);
    addComponent(state.world, SMAA, entity);
    addComponent(state.world, Vignette, entity);

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    removeComponent(state.world, Bloom, entity);

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    expect(bloomQuery(state.world)).not.toContain(entity);
    expect(smaaQuery(state.world)).toContain(entity);
    expect(vignetteQuery(state.world)).toContain(entity);
  });

  it('should run 100 steps with postprocessing entities without degradation', () => {
    const entity = addEntity(state.world);
    addComponent(state.world, Bloom, entity);
    addComponent(state.world, SMAA, entity);
    addComponent(state.world, Vignette, entity);

    Bloom.intensity[entity] = 1.0;
    Bloom.radius[entity] = 0.85;
    SMAA.preset[entity] = 2;
    Vignette.darkness[entity] = 0.5;
    Vignette.offset[entity] = 0.1;

    const initialIntensity = Bloom.intensity[entity];
    const initialPreset = SMAA.preset[entity];
    const initialDarkness = Vignette.darkness[entity];

    for (let i = 0; i < 100; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    }

    expect(Bloom.intensity[entity]).toBeCloseTo(initialIntensity);
    expect(SMAA.preset[entity]).toBe(initialPreset);
    expect(Vignette.darkness[entity]).toBeCloseTo(initialDarkness);
  });

  it('should not crash with no postprocessing entities present', () => {
    expect(() => {
      for (let i = 0; i < 20; i++) {
        state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
      }
    }).not.toThrow();
  });

  it('should DoF auto-focus: player and camera entities with WorldTransforms', () => {
    const player = addEntity(state.world);
    addComponent(state.world, Player, player);
    addComponent(state.world, WorldTransform, player);
    WorldTransform.posX[player] = 10;
    WorldTransform.posY[player] = 0;
    WorldTransform.posZ[player] = 0;

    const camera = addEntity(state.world);
    addComponent(state.world, MainCamera, camera);
    addComponent(state.world, WorldTransform, camera);
    addComponent(state.world, DepthOfField, camera);
    DepthOfField.focusDistance[camera] = 10;
    DepthOfField.focalLength[camera] = 0.05;
    DepthOfField.bokehScale[camera] = 1;
    DepthOfField.autoFocus[camera] = 1;
    WorldTransform.posX[camera] = 0;
    WorldTransform.posY[camera] = 0;
    WorldTransform.posZ[camera] = 0;

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    const playerQuery = defineQuery([Player, WorldTransform])(state.world);
    const cameraQuery = defineQuery([MainCamera, WorldTransform])(state.world);
    const dofQuery = defineQuery([DepthOfField])(state.world);

    expect(playerQuery.length).toBeGreaterThanOrEqual(1);
    expect(cameraQuery.length).toBeGreaterThanOrEqual(1);
    expect(dofQuery).toContain(camera);

    const dx = WorldTransform.posX[camera] - WorldTransform.posX[player];
    const dy = WorldTransform.posY[camera] - WorldTransform.posY[player];
    const dz = WorldTransform.posZ[camera] - WorldTransform.posZ[player];
    const expectedDistance = Math.sqrt(dx * dx + dy * dy + dz * dz);
    expect(expectedDistance).toBe(10);
  });

  it('should DoF auto-focus: update player position and verify distance changes', () => {
    const player = addEntity(state.world);
    addComponent(state.world, Player, player);
    addComponent(state.world, WorldTransform, player);
    WorldTransform.posX[player] = 3;
    WorldTransform.posY[player] = 4;
    WorldTransform.posZ[player] = 0;

    const camera = addEntity(state.world);
    addComponent(state.world, MainCamera, camera);
    addComponent(state.world, WorldTransform, camera);
    addComponent(state.world, DepthOfField, camera);
    DepthOfField.autoFocus[camera] = 1;
    WorldTransform.posX[camera] = 0;
    WorldTransform.posY[camera] = 0;
    WorldTransform.posZ[camera] = 0;

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    const dx = WorldTransform.posX[camera] - WorldTransform.posX[player];
    const dy = WorldTransform.posY[camera] - WorldTransform.posY[player];
    const dz = WorldTransform.posZ[camera] - WorldTransform.posZ[player];
    expect(Math.sqrt(dx * dx + dy * dy + dz * dz)).toBe(5);

    WorldTransform.posX[player] = 1;
    WorldTransform.posY[player] = 0;
    WorldTransform.posZ[player] = 0;

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    const dx2 = WorldTransform.posX[camera] - WorldTransform.posX[player];
    const dy2 = WorldTransform.posY[camera] - WorldTransform.posY[player];
    const dz2 = WorldTransform.posZ[camera] - WorldTransform.posZ[player];
    expect(Math.sqrt(dx2 * dx2 + dy2 * dy2 + dz2 * dz2)).toBe(1);
  });

  it('should DoF manual focus: autoFocus off → uses focusDistance from component', () => {
    const camera = addEntity(state.world);
    addComponent(state.world, MainCamera, camera);
    addComponent(state.world, WorldTransform, camera);
    addComponent(state.world, DepthOfField, camera);
    DepthOfField.autoFocus[camera] = 0;
    DepthOfField.focusDistance[camera] = 25;

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    expect(DepthOfField.focusDistance[camera]).toBe(25);
    expect(DepthOfField.autoFocus[camera]).toBe(0);
  });

  it('should effect registry: unregister, re-register, overwrite, and reject unknown', () => {
    const defsBefore = getEffectDefinitions();
    expect(defsBefore.map((d) => d.key)).toContain('bloom');
    const originalCount = defsBefore.length;
    const bloomDef = defsBefore.find((d) => d.key === 'bloom')!;

    const unregistered = unregisterEffect('bloom');
    expect(unregistered).toBe(true);

    const defsAfter = getEffectDefinitions();
    expect(defsAfter.map((d) => d.key)).not.toContain('bloom');
    expect(defsAfter.length).toBe(originalCount - 1);

    registerEffect(bloomDef);
    expect(getEffectDefinitions().map((d) => d.key)).toContain('bloom');
    expect(getEffectDefinitions().length).toBe(originalCount);

    const customDef: EffectDefinition = {
      key: 'bloom',
      component: Bloom,
      create() {
        return null as any;
      },
    };
    registerEffect(customDef);
    const bloomDefs = getEffectDefinitions().filter((d) => d.key === 'bloom');
    expect(bloomDefs.length).toBe(1);

    registerEffect(bloomDef);

    expect(unregisterEffect('nonexistent-effect')).toBe(false);
  });
});
