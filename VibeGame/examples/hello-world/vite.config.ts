import path from 'node:path';
import { consoleForwarding, vibegame } from '../../src/vite/index.ts';
import { defineConfig } from 'vite';

const vibegameRoot = path.resolve(import.meta.dirname, '../..');

const sharedAssets = path.join(
  vibegameRoot,
  'examples/shared-assets/public/assets'
);

export default defineConfig({
  plugins: [vibegame({ sharedAssets }), consoleForwarding()],
  server: {
    port: 3000,
    open: process.env.BROWSER !== 'none',
    fs: {
      allow: ['..'],
    },
    // server.watch.ignored: set by vibegame() (graphify-out, caches, blobs, …)
  },
  build: {
    target: 'esnext',
    sourcemap: true,
  },
  optimizeDeps: {
    exclude: ['vibegame'],
  },
});
