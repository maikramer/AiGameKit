import type { Plugin } from 'vite';

/**
 * Firefox DevTools tries to resolve a source map for the Rapier WASM
 * pseudo-source (``wasm:<glue url> line 1 > WebAssembly.instantiate``) using
 * the ``//# sourceMappingURL=rapier.mjs.map`` comment inherited from the
 * wasm-bindgen glue module. The ``wasm:`` URL cannot base-resolve the map, so
 * every dev load logs ``Error: URL constructor: is not a valid URL`` with
 * ``URL do mapa de código: null``. Strip the comment at transform time — the
 * dist map only maps minified output nobody debugs, and the glue is served
 * raw (optimizeDeps.exclude) precisely so bundler noise stays out of it.
 */
export function silenceRapierWasmSourcemapNoise(): Plugin {
  return {
    name: 'vibegame:silence-rapier-wasm-sourcemap',
    transform(code, id) {
      if (!id.includes('@dimforge/rapier')) return null;
      if (!code.includes('//# sourceMappingURL=')) return null;
      return {
        code: code.replace(/^[ \t]*\/\/# sourceMappingURL=.*$/gm, ''),
        map: null,
      };
    },
  };
}
