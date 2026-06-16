import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { consoleForwarding, vibegame } from '../../src/vite/index.ts';
import { defineConfig } from 'vite';

const vibegameRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../..'
);

const terrainLodPath = path.join(
  vibegameRoot,
  'node_modules/@interverse/three-terrain-lod'
);

export default defineConfig({
  resolve: {
    dedupe: ['three'],
    alias: {
      vibegame: path.join(vibegameRoot, 'src/index.ts'),
      'vibegame/vite': path.join(vibegameRoot, 'src/vite/index.ts'),
      '@interverse/three-terrain-lod': terrainLodPath,
    },
  },
  plugins: [vibegame(), consoleForwarding()],
  server: {
    port: 3011,
    open: process.env.BROWSER !== 'none',
    fs: {
      // The whole VibeGame root: the engine source AND its node_modules
      // (e.g. troika-three-text) are served from outside examples/.
      allow: ['..', vibegameRoot],
    },
    watch: {
      ignored: ['!**/node_modules/vibegame/**'],
    },
  },
  build: {
    target: 'esnext',
    sourcemap: true,
    rolldownOptions: {
      external: ['@interverse/three-terrain-lod'],
    },
  },
  optimizeDeps: {
    // recast-navigation ships WASM that esbuild's prebundler mangles — exclude it
    // (and its three helper) so the runtime loads the real module.
    exclude: ['vibegame', 'recast-navigation', '@recast-navigation/three'],
    include: ['@interverse/three-terrain-lod', 'troika-three-text'],
  },
});
