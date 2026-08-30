import { describe, expect, it } from 'bun:test';
import type { Plugin } from 'vite';
import { silenceRapierWasmSourcemapNoise } from '../../../src/vite/silence-rapier-sourcemap';

describe('silenceRapierWasmSourcemapNoise', () => {
  it('strips sourceMappingURL comments from Rapier glue modules', () => {
    const plugin = silenceRapierWasmSourcemapNoise() as Plugin & {
      transform: (
        code: string,
        id: string
      ) => { code: string; map: null } | null;
    };
    const input =
      '//# sourceMappingURL=rapier.mjs.map\n' +
      'export { init } from "./rapier.mjs";\n' +
      '//# sourceMappingURL=rapier.mjs.map\n';
    const out = plugin.transform(
      input,
      '/x/node_modules/@dimforge/rapier3d-compat/dist/rapier.mjs?v=abc'
    );
    expect(out).not.toBeNull();
    expect(out!.code).not.toContain('sourceMappingURL');
    expect(out!.code).toContain('export { init }');
  });

  it('ignores non-Rapier modules even when they declare sourcemaps', () => {
    const plugin = silenceRapierWasmSourcemapNoise() as Plugin & {
      transform: (
        code: string,
        id: string
      ) => { code: string; map: null } | null;
    };
    const input = 'export const x = 1;\n//# sourceMappingURL=x.js.map\n';
    expect(
      plugin.transform(input, '/x/node_modules/foo/dist/foo.mjs')
    ).toBeNull();
  });

  it('ignores Rapier modules without sourcemap comments', () => {
    const plugin = silenceRapierWasmSourcemapNoise() as Plugin & {
      transform: (
        code: string,
        id: string
      ) => { code: string; map: null } | null;
    };
    expect(
      plugin.transform(
        'export const x = 1;',
        '/y/@dimforge/rapier3d-compat/dist/rapier.mjs'
      )
    ).toBeNull();
  });
});
