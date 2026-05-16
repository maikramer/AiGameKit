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
    dedupe: ['three', 'three/webgpu', 'three/tsl'],
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
      allow: ['..'],
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
    exclude: ['vibegame'],
    include: ['@interverse/three-terrain-lod'],
  },
});
