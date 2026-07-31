import fs from 'node:fs';
import path from 'node:path';
import type { Connect, Plugin, ViteDevServer } from 'vite';

const MIME: Record<string, string> = {
  '.html': 'text/html',
  '.htm': 'text/html',
  '.css': 'text/css',
  '.js': 'text/javascript',
  '.svg': 'image/svg+xml',
  '.glb': 'model/gltf-binary',
  '.gltf': 'model/gltf+json',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.ktx2': 'image/ktx2',
  '.wasm': 'application/wasm',
  '.json': 'application/json',
  '.wav': 'audio/wav',
  '.ogg': 'audio/ogg',
  '.mp3': 'audio/mpeg',
};

/**
 * Resolve a URL to a file under `publicDir`, or null if missing / unsafe.
 *
 * Vite 8 snapshots `public/` into a Set at startup. New files added while the
 * server runs are absent from that Set, so the stock public middleware skips
 * them and SPA HTML is returned (`<!doctype` → GLB "bad magic").
 */
export function resolvePublicFile(
  publicDir: string,
  rawUrl: string
): string | null {
  if (
    !rawUrl.startsWith('/') ||
    rawUrl.startsWith('/@') ||
    rawUrl.startsWith('/node_modules')
  ) {
    return null;
  }
  let pathname: string;
  try {
    pathname = decodeURIComponent(rawUrl.split('?')[0] ?? '');
  } catch {
    return null;
  }
  if (
    !pathname.startsWith('/') ||
    pathname.includes('\0') ||
    pathname.endsWith('/')
  ) {
    return null;
  }
  const rel = pathname.slice(1);
  if (!rel) return null;

  const root = path.resolve(publicDir);
  const rootPrefix = root.endsWith(path.sep) ? root : root + path.sep;
  const filePath = path.resolve(root, rel);
  if (filePath !== root && !filePath.startsWith(rootPrefix)) return null;

  try {
    if (!fs.statSync(filePath).isFile()) return null;
  } catch {
    return null;
  }
  return filePath;
}

/**
 * Serve existing `public/` files from disk before Vite's cached public check.
 */
export function vibegamePublicLiveServe(): Plugin {
  return {
    name: 'vibegame:public-live-serve',
    apply: 'serve',
    enforce: 'pre',
    configureServer(server: ViteDevServer) {
      server.middlewares.use(publicLiveServeMiddleware(server));
    },
  };
}

export function publicLiveServeMiddleware(
  server: ViteDevServer
): Connect.NextHandleFunction {
  const publicDir = server.config.publicDir;

  return function vibegamePublicLiveServeMiddleware(req, res, next) {
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      next();
      return;
    }
    const filePath = resolvePublicFile(publicDir, req.url ?? '');
    if (!filePath) {
      next();
      return;
    }
    let st: fs.Stats;
    try {
      st = fs.statSync(filePath);
    } catch {
      next();
      return;
    }
    const ext = path.extname(filePath).toLowerCase();
    res.statusCode = 200;
    res.setHeader('Content-Type', MIME[ext] ?? 'application/octet-stream');
    res.setHeader('Content-Length', String(st.size));
    res.setHeader('Cache-Control', 'no-cache');
    if (req.method === 'HEAD') {
      res.end();
      return;
    }
    fs.createReadStream(filePath).pipe(res);
  };
}
