import type * as RAPIER from '@dimforge/rapier3d-compat';
import type { TerrainLOD } from '@interverse/three-terrain-lod';
import type { State } from '../../core';

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
