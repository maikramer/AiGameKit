import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { consoleForwarding, vibegame } from 'vibegame/vite';
import { defineConfig } from 'vite';

const vibegameRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../..'
);

export default defineConfig({
  resolve: {
    dedupe: ['three'],
    alias: {
      vibegame: path.join(vibegameRoot, 'src/index.ts'),
      'vibegame/vite': path.join(vibegameRoot, 'src/vite/index.ts'),
    },
  },
  plugins: [vibegame(), consoleForwarding()],
  server: {
    port: 3011,
    open: true,
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
  },
  optimizeDeps: {
    exclude: ['vibegame'],
  },
});
