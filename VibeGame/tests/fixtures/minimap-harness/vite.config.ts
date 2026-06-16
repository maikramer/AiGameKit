import { defineConfig } from 'vite';

const stub = '/fs-stub.js';

export default defineConfig({
  root: '.',
  resolve: {
    alias: {
      'node:fs': stub,
      'node:path': stub,
      fs: stub,
    },
  },
  server: { host: '127.0.0.1', port: 30988, strictPort: true },
});
