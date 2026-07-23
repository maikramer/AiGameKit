import { afterEach, beforeEach, describe, expect, it, mock } from 'bun:test';
import {
  mkdtempSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import type { HmrContext, Plugin, ViteDevServer, WebSocketServer } from 'vite';
import { vibegameAssetHotReload } from '../../../src/vite/hot-reload.ts';
import { initAssetHotReload } from '../../../src/vite/hot-reload-client.ts';

function asPlugin(p: Plugin | Plugin[]): Plugin {
  return Array.isArray(p) ? p[0]! : p;
}

function makeServer(root: string): {
  server: ViteDevServer;
  send: ReturnType<typeof mock>;
} {
  const send = mock(() => {});
  const ws: Partial<WebSocketServer> = {
    send: send as unknown as WebSocketServer['send'],
    on: mock(() => {}) as unknown as WebSocketServer['on'],
    clients: new Set(),
  };
  const server = {
    config: { root },
    ws: ws as WebSocketServer,
  } as unknown as ViteDevServer;
  return { server, send };
}

function runHotUpdate(
  plugin: Plugin,
  file: string,
  server: ViteDevServer
): unknown {
  return (plugin.handleHotUpdate as (ctx: HmrContext) => unknown).call({}, {
    file,
    server,
    modules: [],
    timestamp: Date.now(),
    read: async () => '',
  } as HmrContext);
}

describe('vibegameAssetHotReload() structure', () => {
  it('returns a plugin object', () => {
    expect(typeof vibegameAssetHotReload()).toBe('object');
  });

  it('names the plugin vibegame-asset-hot-reload', () => {
    expect(asPlugin(vibegameAssetHotReload()).name).toBe(
      'vibegame-asset-hot-reload'
    );
  });

  it('enforces pre', () => {
    expect(asPlugin(vibegameAssetHotReload()).enforce).toBe('pre');
  });

  it('exposes configureServer', () => {
    expect(typeof asPlugin(vibegameAssetHotReload()).configureServer).toBe(
      'function'
    );
  });

  it('exposes handleHotUpdate', () => {
    expect(typeof asPlugin(vibegameAssetHotReload()).handleHotUpdate).toBe(
      'function'
    );
  });

  it('accepts empty options object', () => {
    expect(() => vibegameAssetHotReload({})).not.toThrow();
  });

  it('accepts enabled false', () => {
    expect(asPlugin(vibegameAssetHotReload({ enabled: false })).name).toBe(
      'vibegame-asset-hot-reload'
    );
  });

  it('accepts custom watchDirs', () => {
    expect(() =>
      vibegameAssetHotReload({ watchDirs: ['public/textures'] })
    ).not.toThrow();
  });

  it('accepts custom extensions', () => {
    expect(() =>
      vibegameAssetHotReload({ extensions: ['.png', '.ktx2'] })
    ).not.toThrow();
  });

  it('returns a fresh plugin each call', () => {
    expect(vibegameAssetHotReload()).not.toBe(vibegameAssetHotReload());
  });
});

describe('vibegameAssetHotReload() handleHotUpdate', () => {
  const root = '/tmp/vibegame-fake-root';
  let plugin: Plugin;
  let send: ReturnType<typeof mock>;
  let server: ViteDevServer;

  beforeEach(() => {
    plugin = asPlugin(vibegameAssetHotReload());
    ({ server, send } = makeServer(root));
  });

  const defaultExts = ['.png', '.jpg', '.jpeg', '.webp', '.glb', '.gltf'];
  for (const ext of defaultExts) {
    it(`sends asset-update for ${ext}`, () => {
      const file = path.join(root, `public/assets/tex${ext}`);
      const result = runHotUpdate(plugin, file, server);
      expect(result).toEqual([]);
      expect(send).toHaveBeenCalled();
      const payload = (send.mock.calls as unknown as unknown[][])[0]?.[0] as {
        type: string;
        event: string;
        data: { path: string; ext: string };
      };
      expect(payload.type).toBe('custom');
      expect(payload.event).toBe('vibegame:asset-update');
      expect(payload.data.ext).toBe(ext);
      expect(payload.data.path.replace(/\\/g, '/')).toContain(`tex${ext}`);
    });
  }

  const ignoredExts = [
    '.ts',
    '.tsx',
    '.js',
    '.css',
    '.html',
    '.json',
    '.md',
    '.txt',
    '.svg',
    '.mp3',
  ];
  for (const ext of ignoredExts) {
    it(`ignores non-asset extension ${ext}`, () => {
      const result = runHotUpdate(
        plugin,
        path.join(root, `src/file${ext}`),
        server
      );
      expect(result).toBeUndefined();
      expect(send).not.toHaveBeenCalled();
    });
  }

  it('is case-insensitive on extension', () => {
    runHotUpdate(plugin, path.join(root, 'public/assets/HERO.PNG'), server);
    const payload = (send.mock.calls as unknown as unknown[][])[0]?.[0] as {
      data: { ext: string };
    };
    expect(payload.data.ext).toBe('.png');
  });

  it('normalizes path separators', () => {
    runHotUpdate(plugin, `${root}/public/assets/nested/foo.glb`, server);
    const payload = (send.mock.calls as unknown as unknown[][])[0]?.[0] as {
      data: { path: string };
    };
    expect(payload.data.path.includes('\\')).toBe(false);
  });

  it('returns empty array to suppress default HMR', () => {
    const result = runHotUpdate(plugin, path.join(root, 'a.png'), server);
    expect(Array.isArray(result)).toBe(true);
    expect((result as unknown[]).length).toBe(0);
  });

  it('does nothing when enabled is false', () => {
    const disabled = asPlugin(vibegameAssetHotReload({ enabled: false }));
    const result = runHotUpdate(disabled, path.join(root, 'a.png'), server);
    expect(result).toBeUndefined();
    expect(send).not.toHaveBeenCalled();
  });

  it('respects custom extensions allow-list', () => {
    const custom = asPlugin(vibegameAssetHotReload({ extensions: ['.ktx2'] }));
    runHotUpdate(custom, path.join(root, 'tex.ktx2'), server);
    expect(send).toHaveBeenCalled();
  });

  it('rejects default ext when custom list omits it', () => {
    const custom = asPlugin(vibegameAssetHotReload({ extensions: ['.ktx2'] }));
    const result = runHotUpdate(custom, path.join(root, 'tex.png'), server);
    expect(result).toBeUndefined();
    expect(send).not.toHaveBeenCalled();
  });

  const nestedPaths = [
    'public/assets/a.png',
    'public/assets/chars/hero.glb',
    'public/assets/env/sky.webp',
    'static/tex.jpg',
    'content/model.gltf',
  ];
  for (const rel of nestedPaths) {
    it(`emits relative path for ${rel}`, () => {
      runHotUpdate(plugin, path.join(root, rel), server);
      const payload = (send.mock.calls as unknown as unknown[][])[0]?.[0] as {
        data: { path: string };
      };
      expect(payload.data.path.replace(/\\/g, '/')).toBe(rel);
    });
  }
});

describe('vibegameAssetHotReload() configureServer', () => {
  let tmp: string;

  beforeEach(() => {
    tmp = mkdtempSync(path.join(tmpdir(), 'vg-hmr-'));
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  it('does not throw when watch dir exists', () => {
    mkdirSync(path.join(tmp, 'public', 'assets'), { recursive: true });
    const plugin = asPlugin(
      vibegameAssetHotReload({ watchDirs: ['public/assets'] })
    );
    const { server } = makeServer(tmp);
    expect(() =>
      (plugin.configureServer as (s: ViteDevServer) => void)(server)
    ).not.toThrow();
  });

  it('does not throw when watch dir is missing', () => {
    const plugin = asPlugin(
      vibegameAssetHotReload({ watchDirs: ['public/assets'] })
    );
    const { server } = makeServer(tmp);
    expect(() =>
      (plugin.configureServer as (s: ViteDevServer) => void)(server)
    ).not.toThrow();
  });

  it('skips watching when enabled is false', () => {
    const assets = path.join(tmp, 'public', 'assets');
    mkdirSync(assets, { recursive: true });
    const plugin = asPlugin(
      vibegameAssetHotReload({ enabled: false, watchDirs: ['public/assets'] })
    );
    const { server, send } = makeServer(tmp);
    (plugin.configureServer as (s: ViteDevServer) => void)(server);
    writeFileSync(path.join(assets, 'x.png'), 'x');
    expect(send).not.toHaveBeenCalled();
  });

  it('accepts multiple watchDirs', () => {
    mkdirSync(path.join(tmp, 'a'), { recursive: true });
    mkdirSync(path.join(tmp, 'b'), { recursive: true });
    const plugin = asPlugin(vibegameAssetHotReload({ watchDirs: ['a', 'b'] }));
    const { server } = makeServer(tmp);
    expect(() =>
      (plugin.configureServer as (s: ViteDevServer) => void)(server)
    ).not.toThrow();
  });

  it('accepts empty watchDirs array', () => {
    const plugin = asPlugin(vibegameAssetHotReload({ watchDirs: [] }));
    const { server } = makeServer(tmp);
    expect(() =>
      (plugin.configureServer as (s: ViteDevServer) => void)(server)
    ).not.toThrow();
  });

  const dirs = [
    'public/assets',
    'public/textures',
    'assets',
    'static/models',
    'content/glb',
  ];
  for (const dir of dirs) {
    it(`configures watch for ${dir} without throw`, () => {
      mkdirSync(path.join(tmp, dir), { recursive: true });
      const plugin = asPlugin(vibegameAssetHotReload({ watchDirs: [dir] }));
      const { server } = makeServer(tmp);
      expect(() =>
        (plugin.configureServer as (s: ViteDevServer) => void)(server)
      ).not.toThrow();
    });
  }
});

describe('vibegameAssetHotReload() options matrix', () => {
  const optionSets: Array<Parameters<typeof vibegameAssetHotReload>[0]> = [
    undefined,
    {},
    { enabled: true },
    { enabled: false },
    { watchDirs: ['public/assets'] },
    { watchDirs: ['a', 'b', 'c'] },
    { extensions: ['.png'] },
    { extensions: ['.glb', '.gltf'] },
    { extensions: ['.png', '.jpg', '.jpeg', '.webp'] },
    { enabled: true, watchDirs: ['x'], extensions: ['.png'] },
    { enabled: false, watchDirs: ['x'], extensions: ['.png'] },
    { watchDirs: [], extensions: [] },
    { extensions: ['.KTX2', '.PNG'] },
    { watchDirs: ['public/assets', 'public/models'] },
    { enabled: true, extensions: ['.wasm'] },
  ];

  for (const [i, opts] of optionSets.entries()) {
    it(`builds plugin for options set #${i}`, () => {
      const plugin = asPlugin(vibegameAssetHotReload(opts));
      expect(plugin.name).toBe('vibegame-asset-hot-reload');
      expect(plugin.enforce).toBe('pre');
    });
  }
});

describe('initAssetHotReload client', () => {
  it('is a function export', () => {
    expect(typeof initAssetHotReload).toBe('function');
  });

  it('no-ops safely in bun test (no import.meta.hot)', () => {
    expect(() => initAssetHotReload()).not.toThrow();
  });

  it('can be called repeatedly', () => {
    expect(() => {
      initAssetHotReload();
      initAssetHotReload();
    }).not.toThrow();
  });
});

describe('initAssetHotReload client source contracts', () => {
  const src = readFileSync(
    path.join(import.meta.dir, '../../../src/vite/hot-reload-client.ts'),
    'utf8'
  );

  const contracts = [
    'vibegame:asset-update',
    'import.meta.hot',
    '.png',
    '.jpg',
    '.jpeg',
    '.webp',
    '.glb',
    '.gltf',
    'invalidateTexture',
    'reload recommended',
    'Asset updated',
  ];

  for (const snippet of contracts) {
    it(`client source contains ${snippet}`, () => {
      expect(src).toContain(snippet);
    });
  }
});

describe('vibegameAssetHotReload() payload shape', () => {
  const root = '/project';
  const cases = [
    { file: 'hero.png', ext: '.png' },
    { file: 'hero.PNG', ext: '.png' },
    { file: 'env/sky.jpg', ext: '.jpg' },
    { file: 'm.JPEG', ext: '.jpeg' },
    { file: 'a.webp', ext: '.webp' },
    { file: 'boss.glb', ext: '.glb' },
    { file: 'boss.GLTF', ext: '.gltf' },
    { file: 'nested/deep/x.png', ext: '.png' },
  ];

  for (const c of cases) {
    it(`payload for ${c.file} has ext ${c.ext}`, () => {
      const plugin = asPlugin(vibegameAssetHotReload());
      const { server, send } = makeServer(root);
      runHotUpdate(plugin, path.join(root, c.file), server);
      const payload = (send.mock.calls as unknown as unknown[][])[0]?.[0] as {
        data: { ext: string; path: string };
      };
      expect(payload.data.ext).toBe(c.ext);
      expect(typeof payload.data.path).toBe('string');
    });
  }
});

describe('vibegameAssetHotReload() extra coverage', () => {
  const root = '/extra-root';

  it('handleHotUpdate sends custom event type', () => {
    const plugin = asPlugin(vibegameAssetHotReload());
    const { server, send } = makeServer(root);
    runHotUpdate(plugin, path.join(root, 'x.png'), server);
    const payload = (send.mock.calls as unknown as unknown[][])[0]?.[0] as {
      type: string;
    };
    expect(payload.type).toBe('custom');
  });

  const manyFiles = Array.from({ length: 20 }, (_, i) => `asset-${i}.png`);
  for (const file of manyFiles) {
    it(`emits update for ${file}`, () => {
      const plugin = asPlugin(vibegameAssetHotReload());
      const { server, send } = makeServer(root);
      runHotUpdate(plugin, path.join(root, file), server);
      expect(send).toHaveBeenCalledTimes(1);
    });
  }

  const rejectFiles = [
    'a.ts',
    'b.js',
    'c.css',
    'd.html',
    'e.json',
    'f.svg',
    'g.mp3',
    'h.wav',
    'i.md',
    'j.txt',
  ];
  for (const file of rejectFiles) {
    it(`rejects ${file}`, () => {
      const plugin = asPlugin(vibegameAssetHotReload());
      const { server, send } = makeServer(root);
      const result = runHotUpdate(plugin, path.join(root, file), server);
      expect(result).toBeUndefined();
      expect(send).not.toHaveBeenCalled();
    });
  }
});
