import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { consoleForwarding, vibegame } from '../../src/vite/index.ts';
import { defineConfig, type Plugin } from 'vite';

const vibegameRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../..'
);

const terrainLodPath = path.join(
  vibegameRoot,
  'node_modules/@interverse/three-terrain-lod'
);

// rpg-core's <RpgData>/<PriceTable> recipes import node:fs at module scope; the
// stub keeps them inert in the browser instead of failing the bundle. Farm data
// is fetched + fed to getDataRegistry().loadYaml() at runtime instead.
const nodeStubsPlugin = (): Plugin => ({
  name: 'node-stubs',
  enforce: 'pre',
  resolveId(id) {
    if (
      id === 'node:fs' ||
      id === 'node:path' ||
      id === 'fs' ||
      id === 'path'
    ) {
      return {
        id: path.resolve(vibegameRoot, 'scripts/node-stub.js'),
        external: false,
      };
    }
    return null;
  },
});

const sharedAssets = path.join(
  vibegameRoot,
  'examples/shared-assets/public/assets'
);

export default defineConfig({
  resolve: {
    // Keep engine SoA (Transform, etc.) as a single module instance so entity
    // scripts and main.ts share the same component arrays.
    dedupe: ['three', 'vibegame'],
    alias: {
      vibegame: path.join(vibegameRoot, 'src/index.ts'),
      'vibegame/vite': path.join(vibegameRoot, 'src/vite/index.ts'),
      '@interverse/three-terrain-lod': terrainLodPath,
      'node:fs': path.resolve(vibegameRoot, 'scripts/node-stub.js'),
      'node:path': path.resolve(vibegameRoot, 'scripts/node-stub.js'),
      fs: path.resolve(vibegameRoot, 'scripts/node-stub.js'),
      path: path.resolve(vibegameRoot, 'scripts/node-stub.js'),
    },
  },
  plugins: [nodeStubsPlugin(), vibegame({ sharedAssets }), consoleForwarding()],
  server: {
    port: 3021,
    open: process.env.BROWSER !== 'none',
    fs: {
      // The whole VibeGame root: the engine source AND its node_modules are
      // served from outside examples/.
      allow: ['..', vibegameRoot],
    },
  },
  optimizeDeps: {
    exclude: [
      'vibegame',
      'recast-navigation',
      '@recast-navigation/three',
      '@pmndrs/uikit',
      'yoga-layout',
      '@dimforge/rapier3d-compat',
      '@dimforge/rapier3d',
      '@gltf-transform/core',
      '@gltf-transform/functions',
    ],
    include: ['@interverse/three-terrain-lod', 'troika-three-text'],
  },
  build: {
    target: 'esnext',
    sourcemap: true,
    rolldownOptions: {
      external: ['@interverse/three-terrain-lod', 'node:fs', 'node:path'],
    },
  },
});
