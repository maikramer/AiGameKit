import fs from 'node:fs';
import path from 'node:path';
import type { Connect, Plugin, ViteDevServer } from 'vite';
import { resolvePublicFile } from './public-live-serve.ts';

/** Directory names inside the pool that never reach a build. */
export const DEFAULT_SHARED_EXCLUDE = ['_intermediate', '.gameassets_work'];

/** URL prefixes answered from the pool when the example has no local file. */
export const DEFAULT_SHARED_PREFIXES = ['/assets/'];

const MIME: Record<string, string> = {
  '.glb': 'model/gltf-binary',
  '.gltf': 'model/gltf+json',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.ktx2': 'image/ktx2',
  '.json': 'application/json',
  '.wav': 'audio/wav',
  '.ogg': 'audio/ogg',
  '.mp3': 'audio/mpeg',
  '.svg': 'image/svg+xml',
};

export interface SharedAssetsOptions {
  /** Pool root — the folder holding `meshes/`, `images/`, `textures/`, `sky/`. */
  dir: string;
  /** URL prefixes served from the pool. Defaults to `/assets/`. */
  prefixes?: readonly string[];
  /** Directory names skipped when copying into the build. */
  exclude?: readonly string[];
}

/** True when the URL is one this plugin is allowed to answer. */
export function isSharedAssetUrl(
  rawUrl: string,
  prefixes: readonly string[] = DEFAULT_SHARED_PREFIXES
): boolean {
  const pathname = rawUrl.split('?')[0] ?? '';
  return prefixes.some((p) => pathname.startsWith(p));
}

/**
 * Resolve a URL against the shared pool, or null when it is not ours to serve.
 *
 * The example's own `public/` always wins: this only runs after the local
 * middleware called `next()`, so a game can still override a pooled asset by
 * dropping a file with the same path into its own `public/`.
 */
export function resolveSharedAsset(
  dir: string,
  rawUrl: string,
  prefixes: readonly string[] = DEFAULT_SHARED_PREFIXES
): string | null {
  if (!isSharedAssetUrl(rawUrl, prefixes)) return null;
  // `/assets/meshes/x.glb` → `meshes/x.glb` relative to the pool root.
  const pathname = rawUrl.split('?')[0] ?? '';
  const prefix = prefixes.find((p) => pathname.startsWith(p));
  if (!prefix) return null;
  const rest = pathname.slice(prefix.length);
  if (!rest) return null;
  return resolvePublicFile(dir, `/${rest}`);
}

/** Files the build must copy, as `[absoluteSource, relativeTarget]` pairs. */
export function collectSharedFiles(
  dir: string,
  exclude: readonly string[] = DEFAULT_SHARED_EXCLUDE
): Array<[string, string]> {
  const out: Array<[string, string]> = [];
  const skip = new Set(exclude);
  const walk = (abs: string, rel: string): void => {
    let entries: fs.Dirent[];
    try {
      entries = fs.readdirSync(abs, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      if (e.isSymbolicLink()) continue;
      if (e.isDirectory()) {
        if (skip.has(e.name)) continue;
        walk(path.join(abs, e.name), rel ? `${rel}/${e.name}` : e.name);
      } else if (e.isFile()) {
        out.push([path.join(abs, e.name), rel ? `${rel}/${e.name}` : e.name]);
      }
    }
  };
  walk(path.resolve(dir), '');
  return out;
}

/**
 * Serve one asset pool shared by every example — no symlinks, no copies in git.
 *
 * In dev the pool answers `/assets/**` after the example's own `public/` misses;
 * at build time the pool is copied into `outDir` alongside the example's public
 * files, skipping pipeline intermediates and anything the example already ships.
 */
export function vibegameSharedAssets(options: SharedAssetsOptions): Plugin {
  const dir = path.resolve(options.dir);
  const prefixes = options.prefixes ?? DEFAULT_SHARED_PREFIXES;
  const exclude = options.exclude ?? DEFAULT_SHARED_EXCLUDE;
  let outDir = 'dist';

  return {
    name: 'vibegame:shared-assets',
    enforce: 'pre',
    configResolved(config) {
      outDir = path.resolve(config.root, config.build.outDir);
    },
    configureServer(server: ViteDevServer) {
      server.middlewares.use(sharedAssetsMiddleware(dir, prefixes));
    },
    closeBundle() {
      const base = prefixes[0]?.replace(/^\/|\/$/g, '') ?? 'assets';
      for (const [src, rel] of collectSharedFiles(dir, exclude)) {
        const dst = path.join(outDir, base, rel);
        if (fs.existsSync(dst)) continue; // o exemplo manda sobre o pool
        fs.mkdirSync(path.dirname(dst), { recursive: true });
        fs.copyFileSync(src, dst);
      }
    },
  };
}

export function sharedAssetsMiddleware(
  dir: string,
  prefixes: readonly string[] = DEFAULT_SHARED_PREFIXES
): Connect.NextHandleFunction {
  return function vibegameSharedAssetsMiddleware(req, res, next) {
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      next();
      return;
    }
    const filePath = resolveSharedAsset(dir, req.url ?? '', prefixes);
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
    res.statusCode = 200;
    res.setHeader(
      'Content-Type',
      MIME[path.extname(filePath).toLowerCase()] ?? 'application/octet-stream'
    );
    res.setHeader('Content-Length', String(st.size));
    res.setHeader('Cache-Control', 'no-cache');
    if (req.method === 'HEAD') {
      res.end();
      return;
    }
    fs.createReadStream(filePath).pipe(res);
  };
}
