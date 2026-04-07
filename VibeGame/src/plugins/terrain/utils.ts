import type * as RAPIER from '@dimforge/rapier3d-compat';
import type { TerrainLOD } from '@interverse/three-terrain-lod';
import type { State } from '../../core';
import type { WebGLTerrainMaterialProvider } from './webgl-material';

export interface TerrainEntityData {
  terrainLOD: TerrainLOD;
  heightmapUrl?: string;
  textureUrl?: string;
  initialized: boolean;
  collisionReady: boolean;
  /** Last applied ECS world translation for terrain rigid bodies + Three object. */
  worldOffset: { x: number; y: number; z: number };
  chunkColliders: Map<
    string,
    { body: RAPIER.RigidBody; collider: RAPIER.Collider }
  >;
  materialProvider: WebGLTerrainMaterialProvider;
  /** Cached ECS values to avoid redundant uniform updates. */
  lastRoughness: number;
  lastMetalness: number;
  lastSkirtDepth: number;
  lastWireframe: number;
  lastHeightSmoothing: number;
  lastHeightSmoothingSpread: number;
}

const stateToTerrainContext = new WeakMap<
  State,
  Map<number, TerrainEntityData>
>();

export function getTerrainContext(
  state: State
): Map<number, TerrainEntityData> {
  let context = stateToTerrainContext.get(state);
  if (!context) {
    context = new Map();
    stateToTerrainContext.set(state, context);
  }
  return context;
}

const heightmapUrls = new WeakMap<State, Map<number, string>>();
const textureUrls = new WeakMap<State, Map<number, string>>();

export function setTerrainHeightmapUrl(
  state: State,
  entity: number,
  url: string
): void {
  let m = heightmapUrls.get(state);
  if (!m) {
    m = new Map();
    heightmapUrls.set(state, m);
  }
  m.set(entity, url.trim());
}

export function getTerrainHeightmapUrl(
  state: State,
  entity: number
): string | undefined {
  return heightmapUrls.get(state)?.get(entity);
}

export function setTerrainTextureUrl(
  state: State,
  entity: number,
  url: string
): void {
  let m = textureUrls.get(state);
  if (!m) {
    m = new Map();
    textureUrls.set(state, m);
  }
  m.set(entity, url.trim());
}

export function getTerrainTextureUrl(
  state: State,
  entity: number
): string | undefined {
  return textureUrls.get(state)?.get(entity);
}

/** Same canvas draw as three-terrain-lod `_extractHeightmapImageData` (CPU sampling). */
export function extractTerrainHeightmapImageData(
  terrainLOD: TerrainLOD
): ImageData | null {
  const hm = terrainLOD.getHeightMap();
  if (!hm?.image) return null;
  const img = hm.image as HTMLCanvasElement | HTMLImageElement;
  const w = img.width || 1024;
  const h = img.height || 1024;
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  ctx.drawImage(img as CanvasImageSource, 0, 0);
  return ctx.getImageData(0, 0, w, h);
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/** Bilinear no canal R (0–1) em coordenadas de textura normalizadas; `vv` é o mesmo eixo Z mundo que `resampleChunkHeightsForCollider`. */
function sampleHeightmapRedBilinearNorm(
  imageData: ImageData,
  u: number,
  vv: number,
  matchWebGL: boolean
): number {
  const iw = imageData.width;
  const ih = imageData.height;
  if (iw < 1 || ih < 1) return 0;
  const vRow = matchWebGL ? 1 - vv : vv;
  const fx = u * (iw - 1);
  const fy = vRow * (ih - 1);
  const x0 = Math.floor(fx);
  const y0 = Math.floor(fy);
  const x1 = Math.min(iw - 1, x0 + 1);
  const y1 = Math.min(ih - 1, y0 + 1);
  const tx = fx - x0;
  const ty = fy - y0;
  const rAt = (x: number, y: number) => {
    const cx = Math.max(0, Math.min(iw - 1, x));
    const cy = Math.max(0, Math.min(ih - 1, y));
    const i = (cy * iw + cx) * 4;
    return imageData.data[i] / 255;
  };
  return lerp(lerp(rAt(x0, y0), rAt(x1, y0), tx), lerp(rAt(x0, y1), rAt(x1, y1), tx), ty);
}

/**
 * Altura (m) a partir de posição local ao terreno; UV fora do quadrado viram 0 (como clamp da textura).
 */
function terrainHeightFromLocal(
  imageData: ImageData,
  worldSize: number,
  maxHeight: number,
  lx: number,
  lz: number,
  matchWebGL: boolean
): number {
  const halfWorld = worldSize / 2;
  const lxC = Math.max(-halfWorld, Math.min(halfWorld, lx));
  const lzC = Math.max(-halfWorld, Math.min(halfWorld, lz));
  const u = (lxC + halfWorld) / worldSize;
  const vv = (lzC + halfWorld) / worldSize;
  if (u < 0 || u > 1 || vv < 0 || vv > 1) return 0;
  return sampleHeightmapRedBilinearNorm(imageData, u, vv, matchWebGL) * maxHeight;
}

/**
 * Mesma lógica que o vertex shader em `webgl-material.ts`: bilinear (como `texture2D` linear) e,
 * se `heightSmoothing` > 0, média em cruz + `mix` com o centro — alinha spawn ao mesh deslocado.
 * `heightMapSize` deve ser a largura da textura (igual `uHeightMapSize` no shader).
 */
export function sampleTerrainHeightGpuAligned(
  imageData: ImageData,
  worldSize: number,
  maxHeight: number,
  worldX: number,
  worldZ: number,
  matchWebGL: boolean,
  terrainOriginX: number,
  terrainOriginZ: number,
  heightMapSize: number,
  heightSmoothing: number,
  heightSmoothingSpread: number
): number {
  const lx = worldX - terrainOriginX;
  const lz = worldZ - terrainOriginZ;
  const sm = Math.min(1, Math.max(0, heightSmoothing));
  const h0 = terrainHeightFromLocal(
    imageData,
    worldSize,
    maxHeight,
    lx,
    lz,
    matchWebGL
  );
  if (sm < 1e-6) return h0;

  const spread = Math.max(0.25, heightSmoothingSpread);
  const hm = Math.max(1, heightMapSize);
  const dWorld = (spread / hm) * worldSize;
  const hN = terrainHeightFromLocal(
    imageData,
    worldSize,
    maxHeight,
    lx,
    lz + dWorld,
    matchWebGL
  );
  const hS = terrainHeightFromLocal(
    imageData,
    worldSize,
    maxHeight,
    lx,
    lz - dWorld,
    matchWebGL
  );
  const hE = terrainHeightFromLocal(
    imageData,
    worldSize,
    maxHeight,
    lx + dWorld,
    lz,
    matchWebGL
  );
  const hW = terrainHeightFromLocal(
    imageData,
    worldSize,
    maxHeight,
    lx - dWorld,
    lz,
    matchWebGL
  );
  const hF = (h0 + hN + hS + hE + hW) * 0.2;
  return lerp(h0, hF, sm);
}

/**
 * Amostra altura do heightmap em coordenadas de mundo.
 * `matchWebGL: true` usa a mesma convenção de V que o shader (e `resampleChunkHeightsForCollider` com `invertWorldV: true`).
 * `terrainOriginX/Z` são a origem horizontal do terreno (ex.: `TerrainEntityData.worldOffset`); o heightmap é definido no espaço local do grupo.
 * `boxKernel: 3` faz média dos 9 texels centrados (vizinhança 3×3), útil para heightmaps em “degrau” / voxel.
 */
export function sampleTerrainHeightFromHeightmap(
  imageData: ImageData,
  worldSize: number,
  maxHeight: number,
  worldX: number,
  worldZ: number,
  matchWebGL: boolean,
  terrainOriginX = 0,
  terrainOriginZ = 0,
  boxKernel: 1 | 3 = 1
): number {
  const lx = worldX - terrainOriginX;
  const lz = worldZ - terrainOriginZ;
  const halfWorld = worldSize / 2;
  const u = (lx + halfWorld) / worldSize;
  const vv = (lz + halfWorld) / worldSize;
  if (u < 0 || u > 1 || vv < 0 || vv > 1) return 0;
  const iw = imageData.width;
  const ih = imageData.height;
  const imgX = Math.floor(u * (iw - 1));
  const vRow = matchWebGL ? 1 - vv : vv;
  const imgY = Math.floor(vRow * (ih - 1));
  if (boxKernel === 1) {
    const idx = (imgY * iw + imgX) * 4;
    return (imageData.data[idx] / 255) * maxHeight;
  }
  let sum = 0;
  for (let ky = -1; ky <= 1; ky++) {
    for (let kx = -1; kx <= 1; kx++) {
      const ix = Math.min(iw - 1, Math.max(0, imgX + kx));
      const iy = Math.min(ih - 1, Math.max(0, imgY + ky));
      const idx = (iy * iw + ix) * 4;
      sum += imageData.data[idx] / 255;
    }
  }
  return (sum / 9) * maxHeight;
}

export type TerrainChunkCollisionLike = {
  position: { x: number; y: number; z: number };
  size: number;
  rows: number;
  cols: number;
  heights: Float32Array;
};

/**
 * Rebuild chunk height samples so they match the WebGL displacement path:
 * three-terrain-lod `getHeightAt` follows canvas row order; the WebGL shader uses
 * mesh UV flip `(1-uv.y)` plus typical CanvasTexture `flipY`, which can map world Z
 * to the opposite image row vs raw `getHeightAt`. Set `invertWorldV` to false to mirror
 * the library's CPU collision only.
 */
export function resampleChunkHeightsForCollider(
  chunk: TerrainChunkCollisionLike,
  worldSize: number,
  maxHeight: number,
  imageData: ImageData,
  invertWorldV: boolean
): void {
  const res = chunk.rows - 1;
  const halfWorld = worldSize / 2;
  const centerX = chunk.position.x;
  const centerZ = chunk.position.z;
  const chunkSize = chunk.size;
  const iw = imageData.width;
  const ih = imageData.height;
  for (let row = 0; row < chunk.rows; row++) {
    for (let col = 0; col < chunk.cols; col++) {
      const localX = (col / res - 0.5) * chunkSize;
      const localZ = (row / res - 0.5) * chunkSize;
      const worldX = centerX + localX;
      const worldZ = centerZ + localZ;
      const u = (worldX + halfWorld) / worldSize;
      const vv = (worldZ + halfWorld) / worldSize;
      if (u < 0 || u > 1 || vv < 0 || vv > 1) {
        chunk.heights[row * chunk.cols + col] = 0;
        continue;
      }
      const imgX = Math.floor(u * (iw - 1));
      const vRow = invertWorldV ? 1 - vv : vv;
      const imgY = Math.floor(vRow * (ih - 1));
      const idx = (imgY * iw + imgX) * 4;
      chunk.heights[row * chunk.cols + col] =
        (imageData.data[idx] / 255) * maxHeight;
    }
  }
}
