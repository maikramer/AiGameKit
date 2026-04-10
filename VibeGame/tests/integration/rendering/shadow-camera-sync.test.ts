import { describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import {
  DirectionalLight,
  RenderingPlugin,
  getRenderingContext,
} from 'vibegame/rendering';
import { TransformsPlugin } from 'vibegame/transforms';
import { LightSyncSystem } from '../../../src/plugins/rendering/systems';

describe('Shadow camera sync', () => {
  it('aligns the directional light shadow camera to the light position', () => {
    const state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(RenderingPlugin);
    const context = getRenderingContext(state);

    const lightEntity = state.createEntity();
    state.addComponent(lightEntity, DirectionalLight);
    DirectionalLight.color[lightEntity] = 0xffffff;
    DirectionalLight.intensity[lightEntity] = 1.5;
    DirectionalLight.castShadow[lightEntity] = 1;
    DirectionalLight.shadowMapSize[lightEntity] = 2048;
    DirectionalLight.directionX[lightEntity] = -0.9;
    DirectionalLight.directionY[lightEntity] = 1.45;
    DirectionalLight.directionZ[lightEntity] = -0.55;
    DirectionalLight.distance[lightEntity] = 55;

    LightSyncSystem.update?.(state);

    const light = context.lights.directional;
    expect(light).toBeDefined();

    const shadowCamera = light!.shadow.camera;
    expect(shadowCamera.position.x).toBeCloseTo(light!.position.x);
    expect(shadowCamera.position.y).toBeCloseTo(light!.position.y);
    expect(shadowCamera.position.z).toBeCloseTo(light!.position.z);
  });
});
