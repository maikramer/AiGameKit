import { describe, expect, it } from 'bun:test';
import { createSceneManifestConfig } from '../../../src/plugins/scene-manifest/plugin';

describe('SceneManifestPlugin config', () => {
  it('returns default URL as /assets/gameassets_handoff.json', () => {
    const config = createSceneManifestConfig();
    expect(config.manifestUrl).toBe('/assets/gameassets_handoff.json');
  });

  it('returns default basePath as /', () => {
    const config = createSceneManifestConfig();
    expect(config.basePath).toBe('/');
  });

  it('allows overriding manifestUrl', () => {
    const config = createSceneManifestConfig({
      manifestUrl: '/custom/manifest.json',
    });
    expect(config.manifestUrl).toBe('/custom/manifest.json');
  });

  it('allows overriding basePath', () => {
    const config = createSceneManifestConfig({ basePath: '/assets/' });
    expect(config.basePath).toBe('/assets/');
  });
});
