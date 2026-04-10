import { EffectComposer, EffectPass, RenderPass } from 'postprocessing';
import type { Effect } from 'postprocessing';
import * as THREE from 'three';
import type { State } from '../../core';
import { defineQuery, type System } from '../../core';
import { WorldTransform } from '../transforms';
import {
  MainCamera,
  getRenderingContext,
  getScene,
  threeCameras,
} from '../rendering';
import { getPostprocessingContext } from './utils';
import { getEffectDefinitions, type EffectDefinition } from './effect-registry';
import { Tonemapping } from './components';

const mainCameraTransformQuery = defineQuery([MainCamera, WorldTransform]);
const mainCameraQuery = defineQuery([MainCamera]);

/** Sentinel value: effect creation failed (incompatible library), skip without retrying. */
const skippedMarker: Effect = null as unknown as Effect;

export const PostprocessingSystem: System = {
  group: 'draw',
  update(state: State) {
    const renderContext = getRenderingContext(state);
    const postContext = getPostprocessingContext(state);
    const scene = getScene(state);

    if (!renderContext.renderer || !scene) return;

    const cameraEntities = mainCameraTransformQuery(state.world);
    const definitions = getEffectDefinitions();

    for (const entity of cameraEntities) {
      const camera = threeCameras.get(entity);
      if (!camera) continue;

      let composer = postContext.composers.get(entity);
      if (!composer) {
        composer = new EffectComposer(renderContext.renderer);
        const composerAny = composer as unknown as {
          createDepthTexture: () => THREE.DepthTexture;
        };
        const origCreate = composerAny.createDepthTexture.bind(composerAny);
        composerAny.createDepthTexture = () => {
          const dt = origCreate();
          dt.minFilter = THREE.NearestFilter;
          dt.magFilter = THREE.NearestFilter;
          return dt;
        };
        composer.addPass(new RenderPass(scene, camera));
        postContext.composers.set(entity, composer);
        postContext.effects.set(entity, new Map());
      }

      const effectsMap = postContext.effects.get(entity)!;
      let needsRebuild = false;

      for (const def of definitions) {
        const hasEffect = state.hasComponent(entity, def.component);
        const current = effectsMap.get(def.key);

        if (hasEffect) {
          if (!current || current === skippedMarker) {
            if (current === skippedMarker) continue;
            const effect = def.create(state, entity);
            if (effect) {
              effectsMap.set(def.key, effect);
              needsRebuild = true;
            } else {
              effectsMap.set(def.key, skippedMarker);
            }
          } else if (def.update) {
            const result = def.update(state, entity, current);
            if (result === true) needsRebuild = true;
          }
        } else if (current) {
          effectsMap.delete(def.key);
          needsRebuild = true;
        }
      }

      if (needsRebuild) {
        rebuildEffectPass(
          composer,
          effectsMap,
          camera,
          postContext.externalEffects,
          definitions
        );
      }

      const size = renderContext.renderer.getSize(new THREE.Vector2());
      composer.setSize(size.width, size.height);
    }

    // Cleanup stale composers
    for (const [cameraEntity, composer] of postContext.composers) {
      if (!state.exists(cameraEntity)) {
        composer.dispose();
        postContext.composers.delete(cameraEntity);
        postContext.effects.delete(cameraEntity);
      }
    }
  },
};

export function triggerRebuild(state: State): void {
  const postContext = getPostprocessingContext(state);
  const definitions = getEffectDefinitions();

  for (const [cameraEntity, composer] of postContext.composers) {
    if (!state.exists(cameraEntity)) continue;
    const effectsMap = postContext.effects.get(cameraEntity);
    if (!effectsMap) continue;
    const camera = threeCameras.get(cameraEntity);
    if (!camera) continue;
    rebuildEffectPass(
      composer,
      effectsMap,
      camera,
      postContext.externalEffects,
      definitions
    );
  }
}

function rebuildEffectPass(
  composer: EffectComposer,
  effectsMap: Map<string, Effect>,
  camera: THREE.Camera,
  externalEffects: Effect[],
  definitions: readonly EffectDefinition[]
): void {
  while (composer.passes.length > 1) {
    composer.removePass(composer.passes[1]);
  }

  if (effectsMap.size === 0 && externalEffects.length === 0) return;

  const firstEffects: Effect[] = [];
  const middleEffects: Effect[] = [];
  const lastEffects: Effect[] = [];
  const convolutionEffects: Effect[] = [];

  for (const def of definitions) {
    const effect = effectsMap.get(def.key);
    if (!effect) continue;
    if (def.convolution) {
      convolutionEffects.push(effect);
    } else if (def.position === 'first') {
      firstEffects.push(effect);
    } else if (def.position === 'last') {
      lastEffects.push(effect);
    } else {
      middleEffects.push(effect);
    }
  }

  const effects = [
    ...firstEffects,
    ...middleEffects,
    ...externalEffects,
    ...lastEffects,
  ];

  if (effects.length > 0) {
    composer.addPass(new EffectPass(camera, ...effects));
  }

  for (const effect of convolutionEffects) {
    composer.addPass(new EffectPass(camera, effect));
  }
}

export const PostprocessingRenderSystem: System = {
  group: 'draw',
  last: true,
  update(state: State) {
    if (state.headless) return;

    const renderContext = getRenderingContext(state);
    const renderer = renderContext.renderer;
    if (!renderer) return;

    const postContext = getPostprocessingContext(state);
    const cameraEntities = mainCameraQuery(state.world);

    if (cameraEntities.length === 0) return;

    const cameraEntity = cameraEntities[0];
    const composer = postContext.composers.get(cameraEntity);

    if (!composer) return;

    /**
     * Sem `Tonemapping` no compositor, o RenderPass usa o tone mapping do renderer.
     * O default do Three.js é `NoToneMapping`, o que deixa HDR/IBL sem mapear para o ecrã
     * (aspeto lavado/pálido). Com `ToneMappingEffect`, o shader do efeito faz o mapeamento:
     * o renderer deve ficar em `NoToneMapping` para não duplicar.
     */
    const prevToneMapping = renderer.toneMapping;
    const prevExposure = renderer.toneMappingExposure;

    if (state.hasComponent(cameraEntity, Tonemapping)) {
      renderer.toneMapping = THREE.NoToneMapping;
    } else {
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.toneMappingExposure = 1;
    }

    composer.render();

    renderer.toneMapping = prevToneMapping;
    renderer.toneMappingExposure = prevExposure;
  },
};
