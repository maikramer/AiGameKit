import { defineConfig } from 'vite';
import { vibegame, consoleForwarding, vibegameWorldHmr } from 'aigamekit-vibegame/vite';

export default defineConfig({
  plugins: [vibegame(), consoleForwarding(), vibegameWorldHmr()],
  server: {
    port: 3000,
    open: process.env.BROWSER !== 'none',
  },
  build: {
    target: 'esnext',
    sourcemap: true,
  },
});
