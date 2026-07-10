#!/usr/bin/env node
/**
 * Servidor estático minimalista para o Animation Viewer do VibeGame.
 *
 * Serve:
 *   - O viewer HTML da engine (tools/anim-viewer/index.html) em /
 *   - Os assets do projecto alvo (public/ do exemplo) em /
 *
 * Uso: node anim-viewer-server.mjs <viewerHtmlPath> <assetsRoot> [port]
 * Onde <assetsRoot> é a pasta que contém /assets/meshes/*.glb (ex: examples/simple-rpg/public).
 */
import { createServer } from 'node:http';
import { createReadStream, statSync } from 'node:fs';
import { extname, join, normalize, resolve } from 'node:path';

const viewerHtml = resolve(process.argv[2]);
const assetsRoot = resolve(process.argv[3]);
const port = parseInt(process.argv[4] || '5175', 10);

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript',
  '.mjs': 'text/javascript',
  '.css': 'text/css',
  '.json': 'application/json',
  '.glb': 'model/gltf-binary',
  '.gltf': 'model/gltf+json',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.svg': 'image/svg+xml',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ogg': 'audio/ogg',
  '.wav': 'audio/wav',
  '.mp3': 'audio/mpeg',
  '.ktx2': 'image/ktx2',
};

const server = createServer((req, res) => {
  const urlPath = decodeURIComponent(req.url.split('?')[0]);

  // Rotas especiais
  if (urlPath === '/' || urlPath === '/index.html') {
    serveFile(res, viewerHtml);
    return;
  }

  // Prevenir path traversal
  const safePath = normalize(urlPath).replace(/^(\.\.[/\\])+/, '');
  const filePath = join(assetsRoot, safePath);

  // Garantir que o ficheiro está dentro do assetsRoot
  if (!filePath.startsWith(assetsRoot)) {
    res.writeHead(403);
    res.end('Forbidden');
    return;
  }

  serveFile(res, filePath);
});

/**
 * @param {import('node:http').ServerResponse} res
 * @param {string} filePath
 */
function serveFile(res, filePath) {
  try {
    const stat = statSync(filePath);
    if (!stat.isFile()) {
      res.writeHead(404);
      res.end('Not found');
      return;
    }
    const ext = extname(filePath).toLowerCase();
    const mime = MIME[ext] || 'application/octet-stream';
    res.writeHead(200, {
      'Content-Type': mime,
      'Content-Length': stat.size,
      'Cache-Control': 'no-cache',
      'Access-Control-Allow-Origin': '*',
    });
    createReadStream(filePath).pipe(res);
  } catch {
    res.writeHead(404);
    res.end('Not found');
  }
}

server.listen(port, '0.0.0.0', () => {
  console.log(`\n  🎬 Animation Viewer:  http://localhost:${port}/\n`);
  console.log(`  Viewer:  ${viewerHtml}`);
  console.log(`  Assets:  ${assetsRoot}\n`);
});
