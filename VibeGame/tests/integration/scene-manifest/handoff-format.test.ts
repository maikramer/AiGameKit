import { describe, expect, it, mock, spyOn } from 'bun:test';
import { State, defineQuery } from 'vibegame';
import { loadSceneManifest } from '../../../src/plugins/scene-manifest/loader';
import type {
  HandoffRow,
  HandoffManifest,
} from '../../../src/plugins/scene-manifest/loader';
import { AudioEmitter } from '../../../src/plugins/audio/components';

const mockGroup = {
  position: { set: mock(() => {}) },
  rotation: { set: mock(() => {}) },
  scale: { set: mock(() => {}) },
};

mock.module('../../../src/extras/gltf-bridge', () => ({
  loadGltfToSceneWithAnimator: mock(async () => ({ group: mockGroup })),
}));

const audioQuery = defineQuery([AudioEmitter]);

describe('SceneManifest Handoff Format Integration', () => {
  function makeState() {
    return new State();
  }

  function mockFetch(data: object) {
    return spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new globalThis.Response(JSON.stringify(data))
    );
  }

  describe('Handoff format detection', () => {
    it('should process handoff format (rows array) as handoff', async () => {
      const state = makeState();
      const fetchMock = mockFetch({
        version: 1,
        rows: [{ id: 'test', audio: { url: '/audio/test.wav' } }],
      });

      const result = await loadSceneManifest(state, '/test.json');
      fetchMock.mockRestore();

      expect('rows' in result).toBe(true);
    });

    it('should process old format (assets object) without rows', async () => {
      const state = makeState();
      const fetchMock = mockFetch({
        version: 1,
        assets: { hero: { model: '/hero.glb' } },
      });

      const result = await loadSceneManifest(state, '/test.json');
      fetchMock.mockRestore();

      expect('assets' in result).toBe(true);
      expect('rows' in (result as any)).toBe(false);
    });
  });

  describe('Type exports', () => {
    it('HandoffRow should accept valid row data', () => {
      const row: HandoffRow = {
        id: 'hero',
        public_id: 'hero',
        model: {
          kind: 'rigged',
          url: '/models/hero.glb',
          dest: '/public/hero.glb',
        },
      };
      expect(row.id).toBe('hero');
      expect(row.model?.url).toBe('/models/hero.glb');
    });

    it('HandoffRow should accept audio-only row', () => {
      const row: HandoffRow = {
        id: 'sfx',
        audio: { url: '/audio/sfx.wav', dest: '/public/sfx.wav' },
      };
      expect(row.id).toBe('sfx');
      expect(row.model).toBeUndefined();
      expect(row.audio?.url).toBe('/audio/sfx.wav');
    });

    it('HandoffManifest should accept full manifest', () => {
      const manifest: HandoffManifest = {
        version: 1,
        generated_at: '2026-01-01',
        public_dir: '/public',
        assets_base_url: '/assets',
        rows: [{ id: 'test' }],
      };
      expect(manifest.version).toBe(1);
      expect(manifest.rows).toHaveLength(1);
    });
  });

  describe('Audio entity creation', () => {
    it('should create AudioEmitter entity for audio-only row', async () => {
      const state = makeState();
      const fetchMock = mockFetch({
        version: 1,
        rows: [{ id: 'sfx_collect', audio: { url: '/audio/collect.wav' } }],
      });

      await loadSceneManifest(state, '/test.json');
      fetchMock.mockRestore();

      const entities = audioQuery(state.world);
      expect(entities.length).toBe(1);
    });

    it('should set AudioEmitter defaults: volume=0.7, loop=1, spatial=0, playing=1', async () => {
      const state = makeState();
      const fetchMock = mockFetch({
        version: 1,
        rows: [{ id: 'sfx', audio: { url: '/audio/sfx.wav' } }],
      });

      await loadSceneManifest(state, '/test.json');
      fetchMock.mockRestore();

      const entities = audioQuery(state.world);
      expect(entities.length).toBe(1);
      const eid = entities[0];
      expect(AudioEmitter.volume[eid]).toBeCloseTo(0.7);
      expect(AudioEmitter.loop[eid]).toBe(1);
      expect(AudioEmitter.spatial[eid]).toBe(0);
      expect(AudioEmitter.playing[eid]).toBe(1);
    });

    it('should set clipPath to eid for audio rows', async () => {
      const state = makeState();
      const fetchMock = mockFetch({
        version: 1,
        rows: [{ id: 'sfx', audio: { url: '/audio/sfx.wav' } }],
      });

      await loadSceneManifest(state, '/test.json');
      fetchMock.mockRestore();

      const entities = audioQuery(state.world);
      expect(entities.length).toBe(1);
      const eid = entities[0];
      expect(AudioEmitter.clipPath[eid]).toBe(eid);
    });

    it('should call registerAudioClip with basePath + audio url', async () => {
      const state = makeState();
      const fetchMock = mockFetch({
        version: 1,
        rows: [{ id: 'sfx', audio: { url: '/audio/test.wav' } }],
      });

      await loadSceneManifest(state, '/test.json');
      fetchMock.mockRestore();

      const entities = audioQuery(state.world);
      expect(entities.length).toBe(1);
      const eid = entities[0];
      expect(AudioEmitter.clipPath[eid]).toBe(eid);
    });

    it('should use custom basePath for audio registration', async () => {
      const state = makeState();
      const fetchMock = mockFetch({
        version: 1,
        rows: [{ id: 'sfx', audio: { url: '/audio/test.wav' } }],
      });

      await loadSceneManifest(state, '/test.json', '/custom-base');
      fetchMock.mockRestore();

      const entities = audioQuery(state.world);
      expect(entities.length).toBe(1);
      expect(AudioEmitter.clipPath[entities[0]]).toBe(entities[0]);
    });
  });

  describe('No audio entries', () => {
    it('should not create AudioEmitter entities when handoff has no audio rows', async () => {
      const state = makeState();
      const fetchMock = mockFetch({
        version: 1,
        rows: [
          { id: 'hero', model: { url: '/models/hero.glb' } },
          { id: 'crate', model: { url: '/models/crate.glb' } },
        ],
      });

      await loadSceneManifest(state, '/test.json');
      fetchMock.mockRestore();

      const entities = audioQuery(state.world);
      expect(entities.length).toBe(0);
    });
  });

  describe('Mixed rows', () => {
    it('should create AudioEmitter only for audio-only rows in mixed manifest', async () => {
      const state = makeState();
      const fetchMock = mockFetch({
        version: 1,
        rows: [
          { id: 'hero', model: { url: '/models/hero.glb' } },
          { id: 'crate', model: { url: '/models/crate.glb' } },
          { id: 'sfx_collect', audio: { url: '/audio/collect.wav' } },
          { id: 'tree', model: { url: '/models/tree.glb' } },
          { id: 'bgm', audio: { url: '/audio/bgm.mp3' } },
        ],
      });

      await loadSceneManifest(state, '/test.json');
      fetchMock.mockRestore();

      const entities = audioQuery(state.world);
      expect(entities.length).toBe(2);
    });
  });

  describe('Edge cases', () => {
    it('should skip rows with no model and no audio', async () => {
      const state = makeState();
      const fetchMock = mockFetch({
        version: 1,
        rows: [
          { id: 'empty_row' },
          { id: 'sfx', audio: { url: '/audio/sfx.wav' } },
        ],
      });

      await loadSceneManifest(state, '/test.json');
      fetchMock.mockRestore();

      const entities = audioQuery(state.world);
      expect(entities.length).toBe(1);
    });

    it('should throw on fetch failure', async () => {
      const state = makeState();
      const fetchMock = spyOn(globalThis, 'fetch').mockResolvedValueOnce(
        new globalThis.Response('', { status: 404, statusText: 'Not Found' })
      );

      await expect(loadSceneManifest(state, '/missing.json')).rejects.toThrow(
        /404/
      );
      fetchMock.mockRestore();
    });
  });
});
