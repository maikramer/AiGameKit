import path from 'node:path';
import { consoleForwarding, vibegame } from '../../src/vite/index.ts';
import { defineConfig } from 'vite';

const vibegameRoot = path.resolve(import.meta.dirname, '../..');

const sharedAssets = path.join(
  vibegameRoot,
  'examples/shared-assets/public/assets'
);

export default defineConfig({
  resolve: {
    dedupe: ['three', 'vibegame'],
    alias: {
      vibegame: path.join(vibegameRoot, 'src/index.ts'),
      'vibegame/vite': path.join(vibegameRoot, 'src/vite/index.ts'),
    },
  },
  plugins: [vibegame({ sharedAssets }), consoleForwarding()],
  server: {
    port: 3020,
    open: process.env.BROWSER !== 'none',
    fs: { allow: ['..', vibegameRoot] },
  },
  build: { target: 'esnext', sourcemap: true },
  optimizeDeps: {
    exclude: ['vibegame', '@dimforge/rapier3d-compat'],
  },
});
