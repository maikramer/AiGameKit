import { defineConfig } from 'vite';
import { vibegame, consoleForwarding } from '../../src/vite/index.ts';

export default defineConfig({
  plugins: [vibegame(), consoleForwarding()],
  server: {
    port: 3000,
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
