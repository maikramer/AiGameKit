import type { Plugin } from 'vite';

/**
 * Typr (via troika-three-text) logs ``console.debug("unsupported GPOS/GSUB…")``
 * for OpenType lookups it skips. Harmless, but Firefox surfaces them as noise.
 * Neutralize those calls at transform time without patching node_modules.
 */
export function silenceTyprOpentypeNoise(): Plugin {
  return {
    name: 'vibegame:silence-typr-opentype',
    transform(code, id) {
      if (!id.includes('typr')) return null;
      if (
        !code.includes('unsupported GPOS table LookupType') &&
        !code.includes('unsupported GSUB table LookupType')
      ) {
        return null;
      }
      return {
        code: code
          .replaceAll(
            'console.debug("unsupported GPOS table LookupType"',
            '0&&console.debug("unsupported GPOS table LookupType"'
          )
          .replaceAll(
            'console.debug("unsupported GSUB table LookupType"',
            '0&&console.debug("unsupported GSUB table LookupType"'
          ),
        map: null,
      };
    },
  };
}
