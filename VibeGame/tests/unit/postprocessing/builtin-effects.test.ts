import { describe, expect, it } from 'bun:test';
import {
  AgXToneMapping,
  type Camera,
  type Scene,
  type WebGLRenderer,
} from 'three';
import { Postprocessing } from 'vibegame';
// Side-effect import: builtin effects self-register at module load.
import '../../../src/plugins/postprocessing/builtin-effects';
import { getEffectDefinitions } from '../../../src/plugins/postprocessing/effect-registry';

const EXPECTED_KEYS = [
  'smaa',
  'fxaa',
  'heightFog',
  'bloom',
  'vignette',
  'ssao',
  'depthOfField',
  'tonemapping',
  'chromaticAberration',
] as const;

type CS = Record<string, Float32Array | Uint8Array>;

const stubState = {} as CS;
const stubRenderer = {
  toneMapping: 0,
  toneMappingExposure: 1,
} as unknown as WebGLRenderer;
const stubScene = {} as unknown as Scene;
const stubCamera = {} as unknown as Camera;

describe('postprocessing builtin effects', () => {
  it('registers the full set of builtin effect keys', () => {
    const keys = getEffectDefinitions().map((d) => d.key);
    for (const key of EXPECTED_KEYS) {
      expect(keys).toContain(key);
    }
    expect(keys).toEqual(expect.arrayContaining([...EXPECTED_KEYS]));
  });

  it('places anti-aliasing first and tonemapping last', () => {
    const byKey = new Map(getEffectDefinitions().map((d) => [d.key, d]));
    expect(byKey.get('smaa')?.position).toBe('first');
    expect(byKey.get('fxaa')?.position).toBe('first');
    expect(byKey.get('tonemapping')?.position).toBe('last');
  });

  it('leaves mid-pipeline effects without an explicit position', () => {
    const byKey = new Map(getEffectDefinitions().map((d) => [d.key, d]));
    expect(byKey.get('bloom')?.position).toBeUndefined();
    expect(byKey.get('vignette')?.position).toBeUndefined();
    expect(byKey.get('heightFog')?.position).toBeUndefined();
    expect(byKey.get('chromaticAberration')?.position).toBeUndefined();
  });

  it('skips disabled effects (create returns null when the enable flag is 0)', () => {
    const byKey = new Map(getEffectDefinitions().map((d) => [d.key, d]));
    const entity = 0;
    for (const key of EXPECTED_KEYS) {
      const def = byKey.get(key);
      expect(def).toBeDefined();
      const pass = def!.create(
        stubState,
        entity,
        stubRenderer,
        stubScene,
        stubCamera
      );
      expect(pass).toBeNull();
    }
  });

  it('tonemapping applies renderer exposure/mapping from the component (no pass emitted)', () => {
    const byKey = new Map(getEffectDefinitions().map((d) => [d.key, d]));
    const entity = 0;
    const savedMode = Postprocessing.toneMapping[entity];
    const savedExposure = Postprocessing.toneMappingExposure[entity];
    Postprocessing.toneMapping[entity] = 1;
    Postprocessing.toneMappingExposure[entity] = 1.25;

    const pass = byKey
      .get('tonemapping')!
      .create(stubState, entity, stubRenderer, stubScene, stubCamera);

    Postprocessing.toneMapping[entity] = savedMode;
    Postprocessing.toneMappingExposure[entity] = savedExposure;

    expect(pass).toBeNull();
    expect(stubRenderer.toneMapping).toBe(AgXToneMapping);
    expect(stubRenderer.toneMappingExposure).toBe(1.25);
  });
});
