import { describe, expect, it } from 'bun:test';
import {
  mergeWatchIgnored,
  VIBEGAME_SERVER_WATCH_IGNORED,
  vibegame,
} from '../../../src/vite/index.ts';

describe('VIBEGAME_SERVER_WATCH_IGNORED', () => {
  it('covers graphify cache, pipeline dirs, and huge public assets', () => {
    const joined = VIBEGAME_SERVER_WATCH_IGNORED.join('\n');
    expect(joined).toContain('**/graphify-out/**');
    expect(joined).toContain('**/sample-gameassets/**');
    expect(joined).toContain('**/.gameassets_work/**');
    expect(joined).toContain('**/_intermediate/**');
    expect(joined).toContain('**/public/assets/**');
    expect(joined).toContain('**/node_modules/**');
    expect(joined).toContain('!**/node_modules/vibegame/**');
    expect(joined).toContain('**/.git/**');
    expect(joined).toContain('**/dist/**');
  });

  it('mergeWatchIgnored appends user patterns after defaults', () => {
    const merged = mergeWatchIgnored(['**/custom-scratch/**']);
    expect(Array.isArray(merged)).toBe(true);
    const list = merged as (string | RegExp | ((s: string) => boolean))[];
    expect(list[0]).toBe(VIBEGAME_SERVER_WATCH_IGNORED[0]);
    expect(list.at(-1)).toBe('**/custom-scratch/**');
  });

  it('mergeWatchIgnored keeps AnymatchFn alongside defaults', () => {
    const fn = (path: string) => path.includes('scratch');
    const merged = mergeWatchIgnored(fn);
    expect(Array.isArray(merged)).toBe(true);
    const list = merged as (string | RegExp | ((s: string) => boolean))[];
    expect(list[0]).toBe(VIBEGAME_SERVER_WATCH_IGNORED[0]);
    expect(list.at(-1)).toBe(fn);
  });

  it('vibegame() plugin installs ignored list on config', () => {
    const plugin = vibegame().find((p) => p.name === 'aigamekit-vibegame')!;
    expect(plugin.config).toBeTypeOf('function');
    const config: {
      resolve?: { alias?: Record<string, string> };
      server?: { watch?: { ignored?: string | string[] } };
    } = {
      server: { watch: { ignored: ['**/extra/**'] } },
    };
    type ConfigHook = (
      conf: typeof config,
      env: { command: string; mode: string }
    ) => void;
    const hook = plugin.config as ConfigHook | undefined;
    if (typeof hook === 'function') {
      hook(config, { command: 'serve', mode: 'development' });
    }
    const ignored = config.server?.watch?.ignored;
    expect(Array.isArray(ignored)).toBe(true);
    const list = ignored as string[];
    expect(list.some((p) => p.includes('graphify-out'))).toBe(true);
    expect(list).toContain('**/extra/**');
  });
});
