import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';

const root = path.dirname(fileURLToPath(import.meta.url));
const stub = path.resolve(root, 'fs-stub.js');

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
