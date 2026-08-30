import { describe, expect, it } from 'bun:test';
import type { Plugin } from 'vite';
import {
  mergeWatchIgnored,
  VIBEGAME_SERVER_WATCH_IGNORED,
  vibegame,
} from '../../../src/vite/index.ts';

function getVibegamePlugin(): Plugin {
  const plugins = vibegame();
  const named = plugins.find((p) => p.name === 'aigamekit-vibegame');
  expect(named).toBeDefined();
  return named!;
}

function applyConfig(
  plugin: Plugin,
  config: Record<string, unknown>,
  env: { command: string; mode: string } = {
    command: 'serve',
    mode: 'development',
  }
): Record<string, unknown> {
  const hook = plugin.config;
  expect(typeof hook).toBe('function');
  (hook as (c: typeof config, e: typeof env) => void)(config, env);
  return config;
}

describe('vibegame() plugin structure', () => {
  it('returns an array of plugins', () => {
    expect(Array.isArray(vibegame())).toBe(true);
  });

  it('includes core vibegame + full-reload helpers', () => {
    const names = vibegame().map((p) => p.name);
    expect(names).toContain('aigamekit-vibegame');
    expect(names).toContain('vibegame-force-full-reload');
  });

  it('names the config plugin vibegame', () => {
    expect(getVibegamePlugin().name).toBe('aigamekit-vibegame');
  });

  it('exposes a config hook', () => {
    expect(typeof getVibegamePlugin().config).toBe('function');
  });

  it('does not set apply (runs in serve and build)', () => {
    expect(getVibegamePlugin().apply).toBeUndefined();
  });

  it('does not set enforce', () => {
    expect(getVibegamePlugin().enforce).toBeUndefined();
  });

  it('returns a fresh plugin instance each call', () => {
    expect(vibegame()[0]).not.toBe(vibegame()[0]);
  });

  it('returns a fresh array each call', () => {
    expect(vibegame()).not.toBe(vibegame());
  });
});

describe('vibegame() rapier alias', () => {
  it('aliases rapier3d to rapier3d-compat on empty config', () => {
    const config = applyConfig(getVibegamePlugin(), {});
    const alias = (config.resolve as { alias: Record<string, string> }).alias;
    expect(alias['@dimforge/rapier3d']).toBe('@dimforge/rapier3d-compat');
  });

  it('preserves existing aliases', () => {
    const config = applyConfig(getVibegamePlugin(), {
      resolve: { alias: { '@game': '/src' } },
    });
    const alias = (config.resolve as { alias: Record<string, string> }).alias;
    expect(alias['@game']).toBe('/src');
    expect(alias['@dimforge/rapier3d']).toBe('@dimforge/rapier3d-compat');
  });

  it('overrides a prior rapier alias', () => {
    const config = applyConfig(getVibegamePlugin(), {
      resolve: { alias: { '@dimforge/rapier3d': 'something-else' } },
    });
    const alias = (config.resolve as { alias: Record<string, string> }).alias;
    expect(alias['@dimforge/rapier3d']).toBe('@dimforge/rapier3d-compat');
  });

  it('creates resolve when missing', () => {
    const config = applyConfig(getVibegamePlugin(), {});
    expect(config.resolve).toBeDefined();
  });

  it('creates resolve.alias when resolve exists without alias', () => {
    const config = applyConfig(getVibegamePlugin(), { resolve: {} });
    expect(
      (config.resolve as { alias: Record<string, string> }).alias
    ).toBeDefined();
  });

  it('keeps unrelated resolve options', () => {
    const config = applyConfig(getVibegamePlugin(), {
      resolve: { dedupe: ['three'], alias: { x: 'y' } },
    });
    expect((config.resolve as { dedupe: string[] }).dedupe).toEqual(['three']);
  });
});

describe('vibegame() server.watch.ignored merge', () => {
  it('creates server when missing', () => {
    const config = applyConfig(getVibegamePlugin(), {});
    expect(config.server).toBeDefined();
  });

  it('creates server.watch when missing', () => {
    const config = applyConfig(getVibegamePlugin(), { server: {} });
    expect((config.server as { watch: unknown }).watch).toBeDefined();
  });

  it('sets ignored to an array', () => {
    const config = applyConfig(getVibegamePlugin(), {});
    const ignored = (config.server as { watch: { ignored: unknown } }).watch
      .ignored;
    expect(Array.isArray(ignored)).toBe(true);
  });

  it('includes all default ignored patterns', () => {
    const config = applyConfig(getVibegamePlugin(), {});
    const ignored = (config.server as { watch: { ignored: string[] } }).watch
      .ignored;
    for (const pattern of VIBEGAME_SERVER_WATCH_IGNORED) {
      expect(ignored).toContain(pattern);
    }
  });

  it('appends string ignored from user config', () => {
    const config = applyConfig(getVibegamePlugin(), {
      server: { watch: { ignored: '**/tmp/**' } },
    });
    const ignored = (config.server as { watch: { ignored: string[] } }).watch
      .ignored;
    expect(ignored.at(-1)).toBe('**/tmp/**');
  });

  it('appends array ignored from user config', () => {
    const config = applyConfig(getVibegamePlugin(), {
      server: { watch: { ignored: ['**/a/**', '**/b/**'] } },
    });
    const ignored = (config.server as { watch: { ignored: string[] } }).watch
      .ignored;
    expect(ignored).toContain('**/a/**');
    expect(ignored).toContain('**/b/**');
  });

  it('appends RegExp ignored from user config', () => {
    const re = /\.scratch$/;
    const config = applyConfig(getVibegamePlugin(), {
      server: { watch: { ignored: re } },
    });
    const ignored = (
      config.server as { watch: { ignored: (string | RegExp)[] } }
    ).watch.ignored;
    expect(ignored.at(-1)).toBe(re);
  });

  it('appends AnymatchFn ignored from user config', () => {
    const fn = (p: string) => p.endsWith('.scratch');
    const config = applyConfig(getVibegamePlugin(), {
      server: { watch: { ignored: fn } },
    });
    const ignored = (
      config.server as {
        watch: { ignored: (string | ((p: string) => boolean))[] };
      }
    ).watch.ignored;
    expect(ignored.at(-1)).toBe(fn);
  });

  it('works in build command env', () => {
    const config = applyConfig(
      getVibegamePlugin(),
      {},
      { command: 'build', mode: 'production' }
    );
    expect(
      (config.server as { watch: { ignored: unknown[] } }).watch.ignored.length
    ).toBeGreaterThan(0);
  });

  it('works in serve production mode', () => {
    const config = applyConfig(
      getVibegamePlugin(),
      {},
      { command: 'serve', mode: 'production' }
    );
    const alias = (config.resolve as { alias: Record<string, string> }).alias;
    expect(alias['@dimforge/rapier3d']).toBe('@dimforge/rapier3d-compat');
  });

  it('does not drop user server.host', () => {
    const config = applyConfig(getVibegamePlugin(), {
      server: { host: '0.0.0.0' },
    });
    expect((config.server as { host: string }).host).toBe('0.0.0.0');
  });

  it('does not drop user server.port', () => {
    const config = applyConfig(getVibegamePlugin(), {
      server: { port: 5174 },
    });
    expect((config.server as { port: number }).port).toBe(5174);
  });

  it('preserves other watch options', () => {
    const config = applyConfig(getVibegamePlugin(), {
      server: { watch: { usePolling: true, ignored: [] } },
    });
    expect(
      (config.server as { watch: { usePolling: boolean } }).watch.usePolling
    ).toBe(true);
  });
});

describe('VIBEGAME_SERVER_WATCH_IGNORED patterns', () => {
  const expected = [
    '**/graphify-out/**',
    '**/.git/**',
    '**/node_modules/**',
    '!**/node_modules/aigamekit-vibegame/**',
    '!**/node_modules/vibegame/**',
    '**/dist/**',
    '**/build/**',
    '**/coverage/**',
    '**/.cache/**',
    '**/.turbo/**',
    '**/.vite/**',
    '**/.rollup.cache/**',
    '**/sample-gameassets/**',
    '**/.gameassets_work/**',
    '**/_intermediate/**',
    '**/_part3d_*/**',
    '**/_unused_*/**',
    '**/_rig_backup*/**',
    '**/_retarget_*/**',
    '**/_city_gen/**',
    '**/manifest*.yaml.bak*',
    '**/*.bak',
    '**/*.bak_*',
    '**/*.orig',
    '**/.playwright-mcp/**',
    '**/.sisyphus/**',
    '**/logs/**',
    '**/playwright-report/**',
    '**/test-results/**',
    '**/public/assets/**',
    '**/*.glb',
    '**/*.gltf',
    '**/*.ktx2',
    '**/*.wasm',
  ];

  it('exports a readonly array with expected length', () => {
    expect(VIBEGAME_SERVER_WATCH_IGNORED.length).toBe(expected.length);
  });

  for (const pattern of expected) {
    it(`includes pattern ${pattern}`, () => {
      expect(VIBEGAME_SERVER_WATCH_IGNORED).toContain(pattern);
    });
  }

  it('re-includes linked vibegame after ignoring node_modules', () => {
    const nm = VIBEGAME_SERVER_WATCH_IGNORED.indexOf('**/node_modules/**');
    const re = VIBEGAME_SERVER_WATCH_IGNORED.indexOf(
      '!**/node_modules/vibegame/**'
    );
    expect(nm).toBeGreaterThanOrEqual(0);
    expect(re).toBeGreaterThan(nm);
  });

  it('ignores graphify before node_modules', () => {
    expect(
      VIBEGAME_SERVER_WATCH_IGNORED.indexOf('**/graphify-out/**')
    ).toBeLessThan(VIBEGAME_SERVER_WATCH_IGNORED.indexOf('**/node_modules/**'));
  });

  it('has no duplicate patterns', () => {
    expect(new Set(VIBEGAME_SERVER_WATCH_IGNORED).size).toBe(
      VIBEGAME_SERVER_WATCH_IGNORED.length
    );
  });

  it('every entry is a non-empty string', () => {
    for (const p of VIBEGAME_SERVER_WATCH_IGNORED) {
      expect(typeof p).toBe('string');
      expect(p.length).toBeGreaterThan(0);
    }
  });

  it('every glob uses ** or starts with negation', () => {
    for (const p of VIBEGAME_SERVER_WATCH_IGNORED) {
      expect(p.includes('**') || p.startsWith('!')).toBe(true);
    }
  });
});

describe('mergeWatchIgnored', () => {
  it('returns defaults when existing is undefined', () => {
    const merged = mergeWatchIgnored(undefined);
    expect(merged).toEqual([...VIBEGAME_SERVER_WATCH_IGNORED]);
  });

  it('returns defaults when existing is empty array', () => {
    const merged = mergeWatchIgnored([]);
    expect(merged).toEqual([...VIBEGAME_SERVER_WATCH_IGNORED]);
  });

  it('appends a single string', () => {
    const merged = mergeWatchIgnored('**/custom/**') as string[];
    expect(merged.at(-1)).toBe('**/custom/**');
    expect(merged.length).toBe(VIBEGAME_SERVER_WATCH_IGNORED.length + 1);
  });

  it('appends a RegExp', () => {
    const re = /foo/;
    const merged = mergeWatchIgnored(re) as (string | RegExp)[];
    expect(merged.at(-1)).toBe(re);
  });

  it('appends a function matcher', () => {
    const fn = () => false;
    const merged = mergeWatchIgnored(fn) as (string | (() => boolean))[];
    expect(merged.at(-1)).toBe(fn);
  });

  it('appends multiple array entries in order', () => {
    const merged = mergeWatchIgnored(['a', 'b', 'c']) as string[];
    expect(merged.slice(-3)).toEqual(['a', 'b', 'c']);
  });

  it('does not mutate VIBEGAME_SERVER_WATCH_IGNORED', () => {
    const before = [...VIBEGAME_SERVER_WATCH_IGNORED];
    mergeWatchIgnored(['**/x/**']);
    expect([...VIBEGAME_SERVER_WATCH_IGNORED]).toEqual(before);
  });

  it('does not mutate the user array argument', () => {
    const user = ['**/x/**'];
    mergeWatchIgnored(user);
    expect(user).toEqual(['**/x/**']);
  });

  it('returns a new array instance', () => {
    const a = mergeWatchIgnored(undefined);
    const b = mergeWatchIgnored(undefined);
    expect(a).not.toBe(b);
    expect(a).toEqual(b);
  });

  it('places defaults before user patterns', () => {
    const merged = mergeWatchIgnored(['**/user/**']) as string[];
    expect(merged[0]).toBe(VIBEGAME_SERVER_WATCH_IGNORED[0]);
    expect(merged.at(-1)).toBe('**/user/**');
  });

  it('supports mixed array of string RegExp and fn', () => {
    const re = /\.tmp$/;
    const fn = (p: string) => p.includes('scratch');
    const merged = mergeWatchIgnored(['**/z/**', re, fn]) as unknown[];
    expect(merged.slice(-3)).toEqual(['**/z/**', re, fn]);
  });

  const userPatterns = [
    '**/tmp/**',
    '**/scratch/**',
    '**/.agent/**',
    '**/out/**',
    '**/vendor/**',
    '**/*.log',
    '**/*.tmp',
    '**/__pycache__/**',
    '**/.venv/**',
    '**/datasets/**',
    '**/exports/**',
    '**/captures/**',
    '**/screenshots/**',
    '**/benchmarks/**',
    '**/fixtures/large/**',
  ];

  for (const pattern of userPatterns) {
    it(`mergeWatchIgnored keeps user pattern ${pattern}`, () => {
      const merged = mergeWatchIgnored([pattern]) as string[];
      expect(merged).toContain(pattern);
      expect(merged.indexOf(pattern)).toBe(
        VIBEGAME_SERVER_WATCH_IGNORED.length
      );
    });
  }
});

describe('vibegame() integration invariants', () => {
  it('ignored length equals defaults when no user ignored', () => {
    const config = applyConfig(getVibegamePlugin(), {});
    const ignored = (config.server as { watch: { ignored: string[] } }).watch
      .ignored;
    expect(ignored.length).toBe(VIBEGAME_SERVER_WATCH_IGNORED.length);
  });

  it('ignored length grows with user patterns', () => {
    const config = applyConfig(getVibegamePlugin(), {
      server: { watch: { ignored: ['a', 'b'] } },
    });
    const ignored = (config.server as { watch: { ignored: string[] } }).watch
      .ignored;
    expect(ignored.length).toBe(VIBEGAME_SERVER_WATCH_IGNORED.length + 2);
  });

  it('can be applied twice without throwing', () => {
    const plugin = getVibegamePlugin();
    const config: Record<string, unknown> = {};
    applyConfig(plugin, config);
    expect(() => applyConfig(plugin, config)).not.toThrow();
  });

  it('second apply still has rapier alias', () => {
    const plugin = getVibegamePlugin();
    const config: Record<string, unknown> = {};
    applyConfig(plugin, config);
    applyConfig(plugin, config);
    const alias = (config.resolve as { alias: Record<string, string> }).alias;
    expect(alias['@dimforge/rapier3d']).toBe('@dimforge/rapier3d-compat');
  });

  it('leaves top-level unrelated keys intact', () => {
    const config = applyConfig(getVibegamePlugin(), {
      base: '/game/',
      publicDir: 'static',
    });
    expect(config.base).toBe('/game/');
    expect(config.publicDir).toBe('static');
  });

  it('excludes Rapier WASM from optimizeDeps prebundle', () => {
    const config = applyConfig(getVibegamePlugin(), {});
    const exclude = (config.optimizeDeps as { exclude: string[] }).exclude;
    expect(exclude).toContain('@dimforge/rapier3d-compat');
    expect(exclude).toContain('@dimforge/rapier3d');
    expect(exclude).toContain('yoga-layout');
    expect(exclude).toContain('recast-navigation');
  });
});
