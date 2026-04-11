import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { consoleForwarding, vibegame } from '../../src/vite/index.ts';
import { defineConfig } from 'vite';

const vibegameRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../..'
);

export default defineConfig({
  resolve: {
    dedupe: ['three'],
    alias: [
      { find: 'vibegame/vite', replacement: path.join(vibegameRoot, 'src/vite/index.ts') },
      { find: 'vibegame/plugins', replacement: path.join(vibegameRoot, 'src/plugins') },
      { find: 'vibegame', replacement: path.join(vibegameRoot, 'src/index.ts') },
    ],
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
  },
  optimizeDeps: {
    exclude: ['vibegame'],
  },
});
