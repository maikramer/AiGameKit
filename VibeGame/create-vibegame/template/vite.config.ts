import { defineConfig } from 'vite';
import { vibegame, consoleForwarding } from 'vibegame/vite';

export default defineConfig({
  plugins: [vibegame(), consoleForwarding()],
  server: {
    port: 3000,
    open: process.env.BROWSER !== 'none',
  },
  build: {
    target: 'esnext',
    sourcemap: true,
  },
});
