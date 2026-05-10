import { defineConfig } from 'vite';

export default defineConfig({
  resolve: {
    alias: {
      '@dimforge/rapier3d': '@dimforge/rapier3d-simd-compat',
    },
  },
  build: {
    lib: {
      entry: 'src/index.ts',
      name: 'GAME',
      fileName: 'vibegame.standalone',
      formats: ['iife'],
    },
    rolldownOptions: {
      output: {
        extend: true,
      },
    },
    outDir: 'dist/cdn',
    sourcemap: false,
    target: 'esnext',
  },
});
