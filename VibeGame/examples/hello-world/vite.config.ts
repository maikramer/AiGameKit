import { consoleForwarding, vibegame } from '../../src/vite/index.ts';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [vibegame(), consoleForwarding()],
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
