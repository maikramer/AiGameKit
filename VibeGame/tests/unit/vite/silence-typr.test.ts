import { describe, expect, it } from 'bun:test';
import type { Plugin } from 'vite';
import { silenceTyprOpentypeNoise } from '../../../src/vite/silence-typr';

describe('silenceTyprOpentypeNoise', () => {
  it('neutralizes Typr unsupported GPOS/GSUB console.debug calls', () => {
    const plugin = silenceTyprOpentypeNoise() as Plugin & {
      transform: (
        code: string,
        id: string
      ) => { code: string; map: null } | null;
    };
    const input =
      'console.debug("unsupported GPOS table LookupType",8,2);' +
      'console.debug("unsupported GSUB table LookupType",6,1);';
    const out = plugin.transform(input, '/x/typr.factory.js');
    expect(out).not.toBeNull();
    expect(out!.code).toContain('0&&console.debug("unsupported GPOS');
    expect(out!.code).toContain('0&&console.debug("unsupported GSUB');
  });

  it('ignores unrelated modules', () => {
    const plugin = silenceTyprOpentypeNoise() as Plugin & {
      transform: (
        code: string,
        id: string
      ) => { code: string; map: null } | null;
    };
    expect(plugin.transform('console.debug("hi")', '/foo/bar.js')).toBeNull();
  });
});
