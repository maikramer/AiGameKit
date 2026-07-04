import { logger } from '../../core/utils/logger';
import * as RAPIER from '@dimforge/rapier3d-compat';
import * as THREE from 'three';
import { defineQuery } from '../../core';
import type { State, System } from '../../core';
import { CameraSyncSystem } from '../rendering/systems';
import { getRenderingContext, MainCamera } from '../rendering';
import { WorldTransform } from '../transforms';
import { getRapierWorld } from '../physics';
import { Terrain, TerrainChunk, TerrainDebugInfo } from './components';
import { buildChunkGeometry } from './chunk-geometry';
import { createFlatSampler, sampleHeightAt } from './height-sampler';
import type { HeightSampler } from './height-sampler';
import {
  chunkKey,
  effectiveResolution,
  resolutionForLevel,
  selectChunks,
} from './lod-select';
import { buildDensityMap, maxBoostOverAabb } from './density-map';
import { loadHeightfield } from './ahgt-loader';
import { invalidateTerrainBvh } from '../bvh';
import {
  fireHeightmapReloadCallbacks,
  getChunkMeshRegistry,
  getTerrainContext,
  getTerrainHeightmapUrl,
  getTerrainSplat,
  getTerrainTextureUrl,
  setTerrainHeightmapUrl,
} from './utils';
import { getWaterBodies } from '../water/registry';

const _textureLoader = new THREE.TextureLoader();
const _terrainTextureCache = new Map<string, THREE.Texture>();

/** 1×1 transparent texture: the shader's splat/layer default (all weights 0 →
 * pure base texture) until a biome splat is supplied. */
const _emptyTexture = (() => {
  const t = new THREE.DataTexture(
    new Uint8Array([0, 0, 0, 0]),
    1,
    1,
    THREE.RGBAFormat
  );
  t.needsUpdate = true;
  return t;
})();

interface BlendState {
  fromTex: THREE.Texture | null;
  toTex: THREE.Texture | null;
  mix: number;
  active: boolean;
}
const _blendStates = new WeakMap<State, Map<number, BlendState>>();

function _getBlendState(state: State, entity: number): BlendState {
  let m = _blendStates.get(state);
  if (!m) {
    m = new Map();
    _blendStates.set(state, m);
  }
  let s = m.get(entity);
  if (!s) {
    s = { fromTex: null, toTex: null, mix: 1, active: false };
    m.set(entity, s);
  }
  return s;
}

function _loadTex(url: string): THREE.Texture {
  let tex = _terrainTextureCache.get(url);
  if (tex) return tex;
  tex = _textureLoader.load(url, (t) => {
    t.wrapS = THREE.RepeatWrapping;
    t.wrapT = THREE.RepeatWrapping;
    t.repeat.set(1, 1);
    t.colorSpace = THREE.SRGBColorSpace;
  });
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(1, 1);
  _terrainTextureCache.set(url, tex);
  return tex;
}

function _loadNormalTex(url: string): THREE.Texture {
  let tex = _terrainTextureCache.get(url);
  if (tex) return tex;
  tex = _textureLoader.load(url, (t) => {
    t.wrapS = THREE.RepeatWrapping;
    t.wrapT = THREE.RepeatWrapping;
    t.repeat.set(1, 1);
  });
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(1, 1);
  _terrainTextureCache.set(url, tex);
  return tex;
}

/** Flat default for the packed normal+roughness map: RGB = (0.5,0.5,1) flat
 * tangent normal, A = 1 (fully rough). Used until a biome's real map loads. */
const _flatNRTexture = (() => {
  const t = new THREE.DataTexture(
    new Uint8Array([128, 128, 255, 255]),
    1,
    1,
    THREE.RGBAFormat
  );
  t.wrapS = THREE.RepeatWrapping;
  t.wrapT = THREE.RepeatWrapping;
  t.repeat.set(1, 1);
  t.needsUpdate = true;
  return t;
})();

/** Max lakes the terrain sand-blend shader can mask at once. Few real lakes;
 *  keep small so the GLSL loop stays cheap. */
const MAX_LAKES = 16;

/** Max river path segments the sand shader can mask. Rivers register dense
 *  (~3 m) stations; applyLakeSand downsamples each river to fit this budget.
 *  48 keeps a map-length (~700 m) river within ~15 m chords. */
const MAX_RIVER_SEGS = 48;

/**
 * Peak sand opacity of the water-bed mask. Deliberately < 1 so ~30% of the
 * underlying terrain/biome albedo bleeds through — lakebeds pick up a local
 * colour cast (mud in the swamp, snow in the peaks) instead of an identical
 * stamped beach everywhere.
 */
const SAND_BLEND_MAX = 0.7;

/** World metres per sand-texel repeat — sand grains should tile finer than the
 *  biome albedo (which uses the chunk's world-space UV). Sampled by vWorldXZ. */
const SAND_UV_SCALE = 0.25;

function _clamp255(v: number): number {
  return v < 0 ? 0 : v > 255 ? 255 : v;
}

/** Procedural fine-grain pale-gold sand albedo. No asset download: it is a
 *  subtle underwater tint on the carved lake bed, so a low-amplitude value
 *  noise over a warm base reads as wet lakeshore sand through the water
 *  surface. Two octaves (low-freq swell + fine grain) avoid flat static.
 *  Lazy like _loadPackedNR: built on first browser use, so importing the
 *  module in a DOM-less test environment does not touch `document`. */
let _sandAlbedoTexture: THREE.CanvasTexture | null = null;
function _getSandAlbedo(): THREE.Texture {
  if (_sandAlbedoTexture) return _sandAlbedoTexture;
  const size = 256;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d')!;
  ctx.fillStyle = '#dac99b';
  ctx.fillRect(0, 0, size, size);
  const img = ctx.getImageData(0, 0, size, size);
  const d = img.data;
  let seed = 0x9e3779b9;
  const rand = (): number => {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return seed / 0xffffffff;
  };
  for (let i = 0; i < size * size; i++) {
    const low = (rand() - 0.5) * 22;
    const fine = (rand() - 0.5) * 14;
    const n = low + fine;
    d[i * 4] = _clamp255(d[i * 4]! + n);
    d[i * 4 + 1] = _clamp255(d[i * 4 + 1]! + n);
    d[i * 4 + 2] = _clamp255(d[i * 4 + 2]! + n - 6);
    d[i * 4 + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  const tex = new THREE.CanvasTexture(canvas);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.needsUpdate = true;
  _sandAlbedoTexture = tex;
  return tex;
}

/** Packed sand normal(RGB)+roughness(A): flat tangent normal + roughness ~0.92.
 *  Under opaque-to-half water, crisp sand normals add nothing — a near-flat,
 *  rough surface reads correctly and keeps the sampler budget unchanged. */
const _sandNRTexture = (() => {
  const t = new THREE.DataTexture(
    new Uint8Array([128, 128, 255, 235]),
    1,
    1,
    THREE.RGBAFormat
  );
  t.wrapS = THREE.RepeatWrapping;
  t.wrapT = THREE.RepeatWrapping;
  t.needsUpdate = true;
  return t;
})();

const _packedNRCache = new Map<string, THREE.Texture>();

/**
 * Build a packed surface texture for a terrain layer from its PBR maps:
 * RGB = tangent-space normal, A = roughness (= 1 − smoothness). One texture per
 * layer keeps the shader inside the fragment-sampler budget while still driving
 * per-biome normals AND roughness. `albedoUrl` is the layer's colour map
 * (`/assets/textures/<name>.png`); the PBR maps live in `pbr_<name>/`.
 */
function _loadPackedNR(albedoUrl: string): THREE.Texture {
  const cached = _packedNRCache.get(albedoUrl);
  if (cached) return cached;

  const dir = albedoUrl.replace(/\/[^/]+$/, '');
  const name = albedoUrl
    .split('/')
    .pop()!
    .replace(/\.[^.]+$/, '');
  const normalUrl = `${dir}/pbr_${name}/${name}_normal.png`;
  const smoothUrl = `${dir}/pbr_${name}/${name}_smoothness.png`;

  const size = 512;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d')!;
  // Flat tangent normal (128,128,255) + full alpha. NOTE: css rgba() takes
  // alpha in 0–1 — 'rgba(...,255)' is invalid and silently leaves the canvas
  // black (normal (-1,-1,-1), roughness 0 → shiny) until the maps load.
  ctx.fillStyle = 'rgb(128,128,255)';
  ctx.fillRect(0, 0, size, size);

  const tex = new THREE.CanvasTexture(canvas);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(1, 1);
  tex.colorSpace = THREE.NoColorSpace;
  _packedNRCache.set(albedoUrl, tex);

  const nImg = new Image();
  const sImg = new Image();
  let nReady = false;
  let sReady = false;
  const combine = (): void => {
    if (!nReady) return;
    ctx.drawImage(nImg, 0, 0, size, size);
    if (sReady) {
      const tmp = document.createElement('canvas');
      tmp.width = size;
      tmp.height = size;
      const tctx = tmp.getContext('2d')!;
      tctx.drawImage(sImg, 0, 0, size, size);
      const nrm = ctx.getImageData(0, 0, size, size);
      const smo = tctx.getImageData(0, 0, size, size);
      for (let i = 0; i < size * size; i++) {
        // roughness = 1 − smoothness (use the smoothness map's red channel).
        nrm.data[i * 4 + 3] = 255 - smo.data[i * 4];
      }
      ctx.putImageData(nrm, 0, 0);
    }
    tex.needsUpdate = true;
  };
  nImg.onload = () => {
    nReady = true;
    combine();
  };
  sImg.onload = () => {
    sReady = true;
    combine();
  };
  nImg.src = normalUrl;
  sImg.src = smoothUrl;
  return tex;
}

/**
 * Inject the terrain biome-blend shader. Two layers of blending:
 *  1. `uMap2`/`uMixFactor` — legacy global crossfade kept for
 *     {@link swapTerrainTexture}.
 *  2. Biome splat — `uSplatMap` (RGBA, one biome per channel) sampled by world
 *     XZ, blending up to four `uLayer{0..3}` textures over the base. This gives
 *     real spatial cross-fades between adjacent biomes (no whole-map swap).
 * Uniforms default to an empty splat (all weights 0 → pure base) so the shader
 * renders correctly before any splat is supplied; {@link setTerrainSplat} +
 * the mesh system fill them in later.
 */
function _setupBlendShader(
  mat: THREE.MeshStandardMaterial,
  baseNR: THREE.Texture
): void {
  (mat as any)._shaderRefs = [];

  mat.onBeforeCompile = (shader) => {
    shader.uniforms.uMap2 = { value: _emptyTexture };
    shader.uniforms.uMixFactor = { value: 0 };
    shader.uniforms.uSplatMap = { value: _emptyTexture };
    shader.uniforms.uLayer0 = { value: _emptyTexture };
    shader.uniforms.uLayer1 = { value: _emptyTexture };
    shader.uniforms.uLayer2 = { value: _emptyTexture };
    shader.uniforms.uLayer3 = { value: _emptyTexture };
    // Packed normal (RGB) + roughness (A) per layer; base + 4 biome layers.
    shader.uniforms.uNRBase = { value: baseNR };
    shader.uniforms.uNR0 = { value: _flatNRTexture };
    shader.uniforms.uNR1 = { value: _flatNRTexture };
    shader.uniforms.uNR2 = { value: _flatNRTexture };
    shader.uniforms.uNR3 = { value: _flatNRTexture };
    shader.uniforms.uLayerCount = { value: 0 };
    shader.uniforms.uSplatMin = { value: new THREE.Vector2(0, 0) };
    shader.uniforms.uSplatInvSize = { value: new THREE.Vector2(0, 0) };

    // Lake-bed sand blend: independent of the 4-channel splat budget (which is
    // already full), masked in-shader by world-XZ distance to each registered
    // lake. Pure uniform push — no geometry/material rebuild (see applyLakeSand).
    shader.uniforms.uSandAlbedo = { value: _getSandAlbedo() };
    shader.uniforms.uSandNR = { value: _sandNRTexture };
    shader.uniforms.uSandScale = { value: SAND_UV_SCALE };
    shader.uniforms.uSandBlend = { value: 0 };
    shader.uniforms.uLakeCount = { value: 0 };
    // Per-lake: xy = world centre, z = bowl radius, w = shore radius (waterline).
    const lakeVecs: THREE.Vector4[] = [];
    for (let i = 0; i < MAX_LAKES; i++) lakeVecs.push(new THREE.Vector4());
    shader.uniforms.uLakes = { value: lakeVecs };
    // Per river segment: xyzw = (ax, az, bx, bz) world endpoints; dims per
    // segment: x = shore half-width (waterline), y = outer sand half-width.
    const riverSegs: THREE.Vector4[] = [];
    const riverDims: THREE.Vector2[] = [];
    for (let i = 0; i < MAX_RIVER_SEGS; i++) {
      riverSegs.push(new THREE.Vector4());
      riverDims.push(new THREE.Vector2());
    }
    shader.uniforms.uRiverSegs = { value: riverSegs };
    shader.uniforms.uRiverDims = { value: riverDims };
    shader.uniforms.uRiverSegCount = { value: 0 };

    shader.vertexShader = shader.vertexShader.replace(
      '#include <common>',
      `#include <common>
       varying vec2 vWorldXZ;`
    );
    shader.vertexShader = shader.vertexShader.replace(
      '#include <begin_vertex>',
      `#include <begin_vertex>
       vWorldXZ = (modelMatrix * vec4(transformed, 1.0)).xz;`
    );

    // Shared uniforms + a helper that samples the biome splat by world XZ. The
    // chunk overrides below (albedo / normal / roughness) all blend by it.
    shader.fragmentShader = shader.fragmentShader.replace(
      '#include <common>',
      `#include <common>
       varying vec2 vWorldXZ;
       uniform sampler2D uMap2;
       uniform float uMixFactor;
       uniform sampler2D uSplatMap;
       uniform sampler2D uLayer0;
       uniform sampler2D uLayer1;
       uniform sampler2D uLayer2;
       uniform sampler2D uLayer3;
       uniform sampler2D uNRBase;
       uniform sampler2D uNR0;
       uniform sampler2D uNR1;
       uniform sampler2D uNR2;
       uniform sampler2D uNR3;
       uniform float uLayerCount;
       uniform vec2 uSplatMin;
       uniform vec2 uSplatInvSize;
       uniform sampler2D uSandAlbedo;
       uniform sampler2D uSandNR;
       uniform float uSandScale;
       uniform float uSandBlend;
       uniform int uLakeCount;
       uniform vec4 uLakes[${MAX_LAKES}];
       uniform vec4 uRiverSegs[${MAX_RIVER_SEGS}];
       uniform vec2 uRiverDims[${MAX_RIVER_SEGS}];
       uniform int uRiverSegCount;
       vec4 biomeSplat() {
         return texture2D(uSplatMap, (vWorldXZ - uSplatMin) * uSplatInvSize);
       }
       // GLSL port of shapeRadius() — must stay in sync with water/carve.ts.
       const float SHORE_SHAPE_AMP = 0.28;
       float lakeShapeRadius(float angle, float seedX, float seedZ) {
         float phi1 = (seedX * 12.9898 + seedZ * 78.233) * 0.1;
         float phi2 = (seedX * 4.1764 - seedZ * 29.113) * 0.1;
         float n = sin(angle * 2.0 + phi1) * 0.6
                 + sin(angle * 3.0 - phi2) * 0.3
                 + sin(angle * 5.0 + phi1 * 1.7) * 0.1;
         return 1.0 + n * SHORE_SHAPE_AMP;
       }
       float lakeMask() {
         float m = 0.0;
         for (int i = 0; i < ${MAX_LAKES}; i++) {
           if (i >= uLakeCount) break;
           float r = uLakes[i].z;
           float shoreR = uLakes[i].w;
           vec2 rel = vWorldXZ - uLakes[i].xy;
           float d = length(rel);
           float angle = atan(rel.y, rel.x);
           // Organic shoreline: shape both the beach ring radii by the same
           // perturbation the bowl/water use, so the sand outline matches.
           float s = lakeShapeRadius(angle, uLakes[i].x, uLakes[i].y);
           float beach = 1.0 - smoothstep(shoreR * s, max(r * s, shoreR * s + 0.001), d);
           m = max(m, beach);
         }
         return m;
       }
       // Sand band along river channels: full sand inside the waterline
       // (dims.x), fading out at the outer beach edge (dims.y) — the polyline
       // analogue of the lake's shoreR→r ring.
       float riverMask() {
         float m = 0.0;
         for (int i = 0; i < ${MAX_RIVER_SEGS}; i++) {
           if (i >= uRiverSegCount) break;
           vec2 a = uRiverSegs[i].xy;
           vec2 b = uRiverSegs[i].zw;
           vec2 ab = b - a;
           float lenSq = max(dot(ab, ab), 1e-6);
           float t = clamp(dot(vWorldXZ - a, ab) / lenSq, 0.0, 1.0);
           float d = length(vWorldXZ - (a + ab * t));
           float band = 1.0 - smoothstep(uRiverDims[i].x, max(uRiverDims[i].y, uRiverDims[i].x + 0.001), d);
           m = max(m, band);
         }
         return m;
       }
       float sandMask() {
         return clamp(max(lakeMask(), riverMask()), 0.0, 1.0) * uSandBlend;
       }`
    );

    // Albedo: base ↔ uMap2 legacy crossfade, then biome layers by splat.
    shader.fragmentShader = shader.fragmentShader.replace(
      '#include <map_fragment>',
      `#ifdef USE_MAP
         vec4 t1 = texture2D(map, vMapUv);
         vec4 t2 = texture2D(uMap2, vMapUv);
         vec4 groundCol = mix(t1, t2, uMixFactor);
         if (uLayerCount > 0.5) {
           vec4 splat = biomeSplat();
           groundCol = mix(groundCol, texture2D(uLayer0, vMapUv), splat.r);
           if (uLayerCount > 1.5)
             groundCol = mix(groundCol, texture2D(uLayer1, vMapUv), splat.g);
           if (uLayerCount > 2.5)
             groundCol = mix(groundCol, texture2D(uLayer2, vMapUv), splat.b);
         if (uLayerCount > 3.5)
              groundCol = mix(groundCol, texture2D(uLayer3, vMapUv), splat.a);
          }
          float sand = sandMask();
          if (sand > 0.001) {
            vec2 sandUv = vWorldXZ * uSandScale;
            groundCol = mix(groundCol, texture2D(uSandAlbedo, sandUv), sand);
          }
          diffuseColor *= groundCol;
        #endif`
    );

    // Tangent-space normal: blend the base + biome packed normals (RGB), then
    // transform by the TBN that three already computed (USE_NORMALMAP).
    shader.fragmentShader = shader.fragmentShader.replace(
      '#include <normal_fragment_maps>',
      `#ifdef USE_NORMALMAP_TANGENTSPACE
         vec3 nrm = texture2D(uNRBase, vMapUv).xyz;
         if (uLayerCount > 0.5) {
           vec4 splat = biomeSplat();
           nrm = mix(nrm, texture2D(uNR0, vMapUv).xyz, splat.r);
           if (uLayerCount > 1.5)
             nrm = mix(nrm, texture2D(uNR1, vMapUv).xyz, splat.g);
           if (uLayerCount > 2.5)
             nrm = mix(nrm, texture2D(uNR2, vMapUv).xyz, splat.b);
            if (uLayerCount > 3.5)
              nrm = mix(nrm, texture2D(uNR3, vMapUv).xyz, splat.a);
          }
          float sandN = sandMask();
          if (sandN > 0.001)
            nrm = mix(nrm, texture2D(uSandNR, vWorldXZ * uSandScale).xyz, sandN);
           vec3 mapN = nrm * 2.0 - 1.0;
          mapN.xy *= normalScale;
          normal = normalize( tbn * mapN );
         #endif`
    );

    // Roughness: blend the packed alpha (= 1 − smoothness) per biome, then sand.
    shader.fragmentShader = shader.fragmentShader.replace(
      '#include <roughnessmap_fragment>',
      `float roughnessFactor = roughness;
       float rgh = texture2D(uNRBase, vMapUv).a;
       if (uLayerCount > 0.5) {
         vec4 splat = biomeSplat();
         rgh = mix(rgh, texture2D(uNR0, vMapUv).a, splat.r);
         if (uLayerCount > 1.5) rgh = mix(rgh, texture2D(uNR1, vMapUv).a, splat.g);
         if (uLayerCount > 2.5) rgh = mix(rgh, texture2D(uNR2, vMapUv).a, splat.b);
         if (uLayerCount > 3.5) rgh = mix(rgh, texture2D(uNR3, vMapUv).a, splat.a);
       }
       float sandR = sandMask();
       if (sandR > 0.001)
         rgh = mix(rgh, texture2D(uSandNR, vWorldXZ * uSandScale).a, sandR);
       roughnessFactor *= rgh;`
    );

    (mat as any)._shaderRefs.push(shader);
  };
}

/** Tracks the splat version last pushed to each field's material uniforms. */
const _appliedSplatVersion = new WeakMap<State, Map<number, number>>();

/**
 * Push the per-field biome splat (if any) into the material shader uniforms.
 * Idempotent: only re-applies when the splat version changed or the shader was
 * (re)compiled. Layer textures are loaded through the shared cache so they tile
 * identically to the base map.
 */
function applyTerrainSplat(state: State, field: number): void {
  const cfg = getTerrainSplat(state, field);
  if (!cfg) return;
  const mat = getSharedTerrainMaterials(state).get(field);
  const refs = (mat as any)?._shaderRefs as { uniforms: any }[] | undefined;
  if (!mat || !refs || refs.length === 0) return;

  let perState = _appliedSplatVersion.get(state);
  if (!perState) {
    perState = new Map();
    _appliedSplatVersion.set(state, perState);
  }
  if (perState.get(field) === cfg.version) return;

  const layers = cfg.layerUrls
    .slice(0, 4)
    .map((u) => (u ? _loadTex(u) : _emptyTexture));
  while (layers.length < 4) layers.push(_emptyTexture);
  const nrs = cfg.layerUrls
    .slice(0, 4)
    .map((u) => (u ? _loadPackedNR(u) : _flatNRTexture));
  while (nrs.length < 4) nrs.push(_flatNRTexture);

  for (const sh of refs) {
    sh.uniforms.uSplatMap.value = cfg.splatTexture;
    sh.uniforms.uLayer0.value = layers[0];
    sh.uniforms.uLayer1.value = layers[1];
    sh.uniforms.uLayer2.value = layers[2];
    sh.uniforms.uLayer3.value = layers[3];
    sh.uniforms.uNR0.value = nrs[0];
    sh.uniforms.uNR1.value = nrs[1];
    sh.uniforms.uNR2.value = nrs[2];
    sh.uniforms.uNR3.value = nrs[3];
    sh.uniforms.uLayerCount.value = Math.min(4, cfg.layerUrls.length);
    sh.uniforms.uSplatMin.value.set(cfg.worldMinX, cfg.worldMinZ);
    sh.uniforms.uSplatInvSize.value.set(
      cfg.worldSizeX > 0 ? 1 / cfg.worldSizeX : 0,
      cfg.worldSizeZ > 0 ? 1 / cfg.worldSizeZ : 0
    );
  }
  perState.set(field, cfg.version);
}

/** Last (refs-compiled : lake-count) signature pushed per field — re-push only
 *  on first compile (refs 0→N) or when the lake set changes. */
const _appliedLakeSig = new WeakMap<State, Map<number, string>>();

/**
 * Push the lake-bed sand blend into the material uniforms: lake centres/radii
 * (world XZ) + master on/off. Idempotent like {@link applyTerrainSplat}: the
 * carve (water LakeApplySystem, group 'setup') registers WaterBodies before
 * this runs (group 'draw'), so the basin reads sandy on the first post-carve
 * frame without any geometry or material rebuild.
 */
function applyLakeSand(state: State, field: number): void {
  const mat = getSharedTerrainMaterials(state).get(field);
  const refs = (mat as any)?._shaderRefs as { uniforms: any }[] | undefined;
  if (!mat || !refs || refs.length === 0) return;
  const bodies = getWaterBodies(state);
  const sig = `${refs.length}:${bodies.length}`;
  let perState = _appliedLakeSig.get(state);
  if (!perState) {
    perState = new Map();
    _appliedLakeSig.set(state, perState);
  }
  if (perState.get(field) === sig) return;

  const lakeBodies = bodies.filter((b) => b.kind === 'lake');
  const count = Math.min(MAX_LAKES, lakeBodies.length);

  // Rivers: downsample each body's (dense, ~3 m) station path so all rivers
  // share the MAX_RIVER_SEGS budget. The sand band is metres wide and soft —
  // a coarser polyline is invisible in the mask.
  const riverBodies = bodies.filter((b) => b.kind === 'river');
  const segs: Array<{
    ax: number;
    az: number;
    bx: number;
    bz: number;
    shoreHalf: number;
    outerHalf: number;
  }> = [];
  if (riverBodies.length > 0) {
    const budget = Math.max(1, Math.floor(MAX_RIVER_SEGS / riverBodies.length));
    for (const b of riverBodies) {
      const pts = b.path;
      if (pts.length < 2) continue;
      const shoreHalf = (b.shoreWidth ?? b.width * 0.95) / 2;
      // Sand covers the carve footprint (waterline + exposed banks + feather)
      // when the body carries it; legacy fallback scales a beach to the river,
      // clamped so narrow creeks still read and wide rivers don't flood sand.
      const outerHalf =
        b.carveWidth != null
          ? b.carveWidth / 2
          : b.width / 2 + Math.min(4, Math.max(1.5, b.width * 0.35));
      const stride = Math.max(1, Math.ceil((pts.length - 1) / budget));
      for (
        let i = 0;
        i + 1 < pts.length && segs.length < MAX_RIVER_SEGS;
        i += stride
      ) {
        const a = pts[i]!;
        const bPt = pts[Math.min(i + stride, pts.length - 1)]!;
        segs.push({
          ax: a[0],
          az: a[1],
          bx: bPt[0],
          bz: bPt[1],
          shoreHalf,
          outerHalf,
        });
      }
    }
  }

  for (const sh of refs) {
    const lakes = sh.uniforms.uLakes.value as THREE.Vector4[];
    for (let i = 0; i < count; i++) {
      const b = lakeBodies[i]!;
      // Guard for legacy bodies without shoreRadius: fall back to 0.7·radius so
      // the mask degrades gracefully rather than sanding the whole bowl.
      const shoreR = b.shoreRadius ?? b.radius * 0.7;
      lakes[i].set(b.x, b.z, b.radius, shoreR);
    }
    sh.uniforms.uLakeCount.value = count;
    const riverSegs = sh.uniforms.uRiverSegs.value as THREE.Vector4[];
    const riverDims = sh.uniforms.uRiverDims.value as THREE.Vector2[];
    for (let i = 0; i < segs.length; i++) {
      const s = segs[i]!;
      riverSegs[i].set(s.ax, s.az, s.bx, s.bz);
      riverDims[i].set(s.shoreHalf, s.outerHalf);
    }
    sh.uniforms.uRiverSegCount.value = segs.length;
    sh.uniforms.uSandBlend.value =
      count > 0 || segs.length > 0 ? SAND_BLEND_MAX : 0;
  }
  perState.set(field, sig);
}

/** Build per-chunk terrain colliders only within this radius of the player. */
const PHYSICS_COLLIDER_RADIUS = 192;

/** Re-run the (allocating) quadtree LOD selection only after the camera moves
 * this far — LOD boundaries are tens of metres apart, so per-frame reselection
 * is wasted work while standing or moving slowly. */
const LOD_RESELECT_DISTANCE = 6;
const _lastLodCam = new Map<number, { x: number; z: number }>();
let _heightmapRetryFrame = 0;

/** Shared materials per terrain field — avoids N duplicate Material instances for N chunks. */
const _sharedTerrainMaterialsByState = new WeakMap<
  State,
  Map<number, THREE.MeshStandardMaterial>
>();

function getSharedTerrainMaterials(
  state: State
): Map<number, THREE.MeshStandardMaterial> {
  let map = _sharedTerrainMaterialsByState.get(state);
  if (!map) {
    map = new Map();
    _sharedTerrainMaterialsByState.set(state, map);
  }
  return map;
}

const terrainQuery = defineQuery([Terrain]);
const chunkQuery = defineQuery([TerrainChunk]);
const debugQuery = defineQuery([Terrain, TerrainDebugInfo]);
const mainCameraQuery = defineQuery([MainCamera, WorldTransform]);

function fieldWorldOffset(
  state: State,
  entity: number
): {
  x: number;
  y: number;
  z: number;
} {
  if (state.hasComponent(entity, WorldTransform)) {
    return {
      x: WorldTransform.posX[entity],
      y: WorldTransform.posY[entity],
      z: WorldTransform.posZ[entity],
    };
  }
  return { x: 0, y: 0, z: 0 };
}

/** Tear down every per-chunk heightfield body for a terrain field. */
function removeChunkColliders(
  rapierWorld: RAPIER.World | null,
  data: import('./utils').TerrainEntityData
): void {
  if (rapierWorld) {
    for (const body of data.chunkColliders.values()) {
      rapierWorld.removeRigidBody(body);
    }
  }
  data.chunkColliders.clear();
}

/**
 * Recompute the resolution of every live chunk of a field from the current
 * density map, dirtying the ones that changed. Without this, chunks that
 * already exist keep their spawn-time resolution forever (LOD reselect matches
 * them by key and skips re-creation), so a density change — heightmap load or
 * a `<Lake>` override — would never reach the mesh under the camera.
 */
export function refreshChunkResolutions(
  state: State,
  field: number,
  data: import('./utils').TerrainEntityData
): void {
  const baseResolution = Terrain.resolution[field];
  for (const chunk of data.chunks) {
    if (!state.exists(chunk)) continue;
    const level = TerrainChunk.level[chunk];
    let res = resolutionForLevel(baseResolution, level);
    if (data.density) {
      const half = TerrainChunk.size[chunk] / 2;
      const boost = maxBoostOverAabb(data.density, {
        minX: TerrainChunk.originX[chunk] - half,
        minZ: TerrainChunk.originZ[chunk] - half,
        maxX: TerrainChunk.originX[chunk] + half,
        maxZ: TerrainChunk.originZ[chunk] + half,
      });
      if (boost > 0) res = effectiveResolution(baseResolution, level, boost);
    }
    if (TerrainChunk.resolution[chunk] !== res) {
      TerrainChunk.resolution[chunk] = res;
      TerrainChunk.meshDirty[chunk] = 1;
    }
  }
}

/**
 * Install a freshly loaded height sampler on a field and rebuild everything
 * derived from it: density map, BVH, chunk meshes, physics stand-in and
 * per-chunk heightfield colliders. Shared by the bootstrap load, the periodic
 * retry and {@link reloadTerrainHeightmap} so no path misses a derivative
 * (the retry path used to skip the collider/BVH reset, leaving stale physics).
 */
function applyLoadedSampler(
  state: State,
  field: number,
  data: import('./utils').TerrainEntityData,
  sampler: HeightSampler
): void {
  data.sampler = sampler;
  data.density = buildDensityMap(sampler, 64);
  refreshChunkResolutions(state, field, data);
  invalidateTerrainBvh(state, field);
  for (const chunk of data.chunks) {
    TerrainChunk.meshDirty[chunk] = 1;
  }
  const rapierWorld = getRapierWorld(state);
  if (data.physicsBody && rapierWorld) {
    rapierWorld.removeRigidBody(data.physicsBody);
    data.physicsBody = null;
    data.physicsCollider = null;
  }
  removeChunkColliders(rapierWorld, data);
  data.collisionReady = false;
  fireHeightmapReloadCallbacks(state);
}

export const TerrainFieldBootstrapSystem: System = {
  group: 'fixed',
  update(state: State) {
    if (state.headless) return;

    const context = getTerrainContext(state);

    for (const entity of terrainQuery(state.world)) {
      if (context.has(entity)) continue;

      const sampler = createFlatSampler(
        Terrain.worldSize[entity],
        Terrain.maxHeight[entity]
      );

      const heightmapUrl = getTerrainHeightmapUrl(state, entity);
      context.set(entity, {
        sampler,
        chunks: new Set<number>(),
        heightmapUrl,
        textureUrl: undefined,
        initialized: true,
        collisionReady: false,
        worldOffset: fieldWorldOffset(state, entity),
        lastWireframe: Terrain.wireframe[entity],
        lastShowChunkBorders: Terrain.showChunkBorders[entity],
        physicsBody: null,
        physicsCollider: null,
        chunkColliders: new Map(),
      });

      if (heightmapUrl) {
        const field = entity;
        const worldSize = Terrain.worldSize[entity];
        const maxHeight = Terrain.maxHeight[entity];
        loadHeightfield(heightmapUrl, worldSize, maxHeight)
          .then((sampler) => {
            const data = context.get(field);
            if (!data) return;
            applyLoadedSampler(state, field, data, sampler);
          })
          .catch((err) => {
            logger.error(
              `Heightmap load failed: ${heightmapUrl} — ${err instanceof Error ? err.message : err}`
            );
          });
      }
    }

    for (const [entity, data] of context) {
      if (state.exists(entity)) continue;
      const rapierWorld = getRapierWorld(state);
      if (rapierWorld && data.physicsBody) {
        rapierWorld.removeRigidBody(data.physicsBody);
        data.physicsBody = null;
        data.physicsCollider = null;
      }
      removeChunkColliders(rapierWorld, data);
      for (const chunk of data.chunks) {
        if (state.exists(chunk)) state.destroyEntity(chunk);
      }
      context.delete(entity);
    }
  },
  dispose(state: State) {
    const scene = getRenderingContext(state).scene;
    const registry = getChunkMeshRegistry(state);
    for (const [chunk, mesh] of registry) {
      scene.remove(mesh);
      mesh.geometry.dispose();
      registry.delete(chunk);
    }
    getTerrainContext(state).clear();
    const _sharedTerrainMaterials = getSharedTerrainMaterials(state);
    for (const mat of _sharedTerrainMaterials.values()) {
      mat.dispose();
    }
    _sharedTerrainMaterials.clear();
  },
};

export const TerrainLodSelectSystem: System = {
  group: 'draw',
  after: [CameraSyncSystem],
  update(state: State) {
    if (state.headless) return;

    const context = getTerrainContext(state);
    const cameras = mainCameraQuery(state.world);
    if (cameras.length === 0) return;
    const camEntity = cameras[0];
    const camX = WorldTransform.posX[camEntity];
    const camZ = WorldTransform.posZ[camEntity];

    for (const fieldEntity of terrainQuery(state.world)) {
      const data = context.get(fieldEntity);
      if (!data || !data.initialized) continue;

      const currentTexUrl = getTerrainTextureUrl(state, fieldEntity);
      if (currentTexUrl !== data.textureUrl) {
        const oldUrl = data.textureUrl;
        data.textureUrl = currentTexUrl;
        if (currentTexUrl) {
          const newTex = _loadTex(currentTexUrl);
          const bs = _getBlendState(state, fieldEntity);
          const mat = getSharedTerrainMaterials(state).get(fieldEntity);
          if (mat && bs.fromTex && oldUrl) {
            bs.toTex = newTex;
            bs.mix = 0;
            bs.active = true;
            for (const sh of (mat as any)._shaderRefs || []) {
              sh.uniforms.uMap2.value = newTex;
            }
          } else if (mat) {
            mat.map = newTex;
            mat.needsUpdate = true;
            bs.fromTex = newTex;
            bs.toTex = newTex;
          }
        }
      }

      const bs = _getBlendState(state, fieldEntity);
      if (bs.active) {
        const dt = state.time.deltaTime;
        bs.mix = Math.min(1, bs.mix + dt / 2.0);
        const mat = getSharedTerrainMaterials(state).get(fieldEntity);
        for (const sh of (mat as any)?._shaderRefs || []) {
          sh.uniforms.uMixFactor.value = bs.mix;
        }
        if (bs.mix >= 1) {
          bs.active = false;
          if (mat && bs.toTex) {
            mat.map = bs.toTex;
            mat.needsUpdate = true;
            bs.fromTex = bs.toTex;
            for (const sh of (mat as any)._shaderRefs || []) {
              sh.uniforms.uMixFactor.value = 0;
            }
          }
        }
      }

      const worldSize = Terrain.worldSize[fieldEntity];
      const levels = Terrain.levels[fieldEntity];
      const ratio = Terrain.lodDistanceRatio[fieldEntity];
      const hysteresis = Terrain.lodHysteresis[fieldEntity];
      const baseResolution = Terrain.resolution[fieldEntity];

      const offset = data.worldOffset;
      const localCamX = camX - offset.x;
      const localCamZ = camZ - offset.z;

      // Skip reselection if the camera barely moved (and chunks already exist).
      const last = _lastLodCam.get(fieldEntity);
      if (
        last &&
        data.chunks.size > 0 &&
        Math.hypot(localCamX - last.x, localCamZ - last.z) <
          LOD_RESELECT_DISTANCE
      ) {
        continue;
      }
      _lastLodCam.set(fieldEntity, { x: localCamX, z: localCamZ });

      const desired = selectChunks(
        worldSize,
        levels,
        ratio,
        hysteresis,
        localCamX,
        localCamZ
      );

      const desiredKeys = new Map<string, (typeof desired)[number]>();
      for (const desc of desired) {
        desiredKeys.set(chunkKey(desc), desc);
      }

      const existingKeys = new Map<string, number>();
      for (const chunkEid of data.chunks) {
        if (!state.exists(chunkEid)) continue;
        const key = `${TerrainChunk.originX[chunkEid]},${TerrainChunk.originZ[chunkEid]},${TerrainChunk.level[chunkEid]}`;
        existingKeys.set(key, chunkEid);
      }

      for (const [key, desc] of desiredKeys) {
        if (existingKeys.has(key)) continue;

        const chunk = state.createEntity();
        // Camera LOD picks the floor; the density map raises it for chunks
        // overlapping featured regions (carved lakes, ridges). max over the
        // chunk AABB so a chunk merely touching a feature has no crack inside.
        let res = resolutionForLevel(baseResolution, desc.level);
        if (data.density) {
          const half = desc.size / 2;
          const boost = maxBoostOverAabb(data.density, {
            minX: desc.originX - half,
            minZ: desc.originZ - half,
            maxX: desc.originX + half,
            maxZ: desc.originZ + half,
          });
          if (boost > 0) {
            res = effectiveResolution(baseResolution, desc.level, boost);
          }
        }
        state.addComponent(chunk, TerrainChunk, {
          field: fieldEntity,
          originX: desc.originX,
          originZ: desc.originZ,
          size: desc.size,
          level: desc.level,
          resolution: res,
          meshDirty: 1,
        });
        data.chunks.add(chunk);
      }

      for (const [key, chunkEid] of existingKeys) {
        if (desiredKeys.has(key)) continue;
        data.chunks.delete(chunkEid);
        if (state.exists(chunkEid)) {
          state.destroyEntity(chunkEid);
        }
      }
    }
  },
};

export const TerrainMeshSystem: System = {
  group: 'draw',
  update(state: State) {
    if (state.headless) return;

    const scene = getRenderingContext(state).scene;
    const registry = getChunkMeshRegistry(state);
    const context = getTerrainContext(state);
    const _sharedTerrainMaterials = getSharedTerrainMaterials(state);

    for (const [chunk, mesh] of registry) {
      if (state.exists(chunk)) continue;
      scene.remove(mesh);
      mesh.geometry.dispose();
      registry.delete(chunk);
    }

    for (const chunk of chunkQuery(state.world)) {
      if (TerrainChunk.meshDirty[chunk] !== 1) continue;

      const field = TerrainChunk.field[chunk];
      const data = context.get(field);
      if (!data) continue;

      // Shallow apron just deep enough to plug LOD T-junction cracks; kept small
      // so it stays hidden below the surface instead of forming visible cliffs.
      const skirtDepth = Terrain.maxHeight[field] * Terrain.skirtWidth[field];
      // Field-constant epsilon so shared edge vertices get identical normals on
      // both neighbouring chunks (no lighting seam), independent of their LOD.
      const normalEpsilon = Terrain.worldSize[field] / 1024;

      // World-space UV tile: constant texel density on every LOD level and
      // continuous across chunk borders (per-chunk 0..1 UVs made the pattern
      // scale jump/restart at every LOD boundary — a visible seam). Auto (0)
      // matches the old near-camera density: smallest chunk = worldSize /
      // 2^(levels-1), which used to hold 32 tiles.
      const tileSize =
        Terrain.textureTileSize[field] ||
        Terrain.worldSize[field] / 2 ** (Terrain.levels[field] - 1) / 32;

      const geometry = buildChunkGeometry(
        data.sampler,
        TerrainChunk.originX[chunk],
        TerrainChunk.originZ[chunk],
        TerrainChunk.size[chunk],
        TerrainChunk.resolution[chunk],
        skirtDepth,
        normalEpsilon,
        tileSize
      );

      let mesh = registry.get(chunk);
      if (mesh) {
        mesh.geometry.dispose();
        mesh.geometry = geometry;
      } else {
        let material = _sharedTerrainMaterials.get(field);
        const texUrl = getTerrainTextureUrl(state, field);
        const expectedColor = texUrl ? 0xffffff : Terrain.baseColor[field];
        const normalStrength = Terrain.normalStrength[field] || 1;
        if (
          !material ||
          material.wireframe !== (Terrain.wireframe[field] === 1) ||
          material.color.getHex() !== expectedColor ||
          material.roughness !== Terrain.roughness[field] ||
          material.metalness !== Terrain.metalness[field] ||
          (material.normalMap && material.normalScale.x !== normalStrength)
        ) {
          if (material) material.dispose();
          const matOpts: THREE.MeshStandardMaterialParameters = {
            color: texUrl ? 0xffffff : Terrain.baseColor[field],
            roughness: Terrain.roughness[field],
            metalness: Terrain.metalness[field],
            wireframe: Terrain.wireframe[field] === 1,
            side: THREE.DoubleSide,
          };
          let baseNR: THREE.Texture = _flatNRTexture;
          if (texUrl) {
            matOpts.map = _loadTex(texUrl);
            const baseName = texUrl.replace(/\/[^/]+$/, '');
            const texName = texUrl.split('/').pop()!.replace('.png', '');
            const normalUrl = `${baseName}/pbr_${texName}/${texName}_normal.png`;
            matOpts.normalMap = _loadNormalTex(normalUrl);
            // `normal-strength` XML attr (Terrain.normalStrength); the blend
            // shader multiplies the packed uNR* normals by this normalScale.
            matOpts.normalScale = new THREE.Vector2(
              normalStrength,
              normalStrength
            );
            // Packed normal+roughness for the base layer. Assigned as the
            // roughnessMap purely to switch on USE_ROUGHNESSMAP (the shader
            // override re-samples it); the real per-biome blend uses uNR*.
            baseNR = _loadPackedNR(texUrl);
            matOpts.roughnessMap = baseNR;
          }
          material = new THREE.MeshStandardMaterial(matOpts);
          _setupBlendShader(material, baseNR);
          const bs = _getBlendState(state, field);
          bs.fromTex = matOpts.map as THREE.Texture | null;
          bs.toTex = matOpts.map as THREE.Texture | null;
          bs.mix = 0;
          bs.active = false;
          _sharedTerrainMaterials.set(field, material);
        }
        mesh = new THREE.Mesh(geometry, material);
        mesh.receiveShadow = true;
        mesh.castShadow = false;
        registry.set(chunk, mesh);
        scene.add(mesh);
      }

      const offset = data.worldOffset;
      mesh.position.set(
        offset.x + TerrainChunk.originX[chunk],
        offset.y,
        offset.z + TerrainChunk.originZ[chunk]
      );

      TerrainChunk.meshDirty[chunk] = 0;
    }

    // Push any pending biome splat into the shared materials (version-gated, so
    // it costs ~nothing once applied). Runs here because the shader refs only
    // exist after the material has been compiled by a draw.
    for (const field of terrainQuery(state.world)) {
      applyTerrainSplat(state, field);
      applyLakeSand(state, field);
    }

    // Retry heightmap load if sampler still flat after bootstrap (async callback
    // may have failed — ensure terrain eventually gets real data).
    _heightmapRetryFrame++;
    const _heightmapRetryInterval = 60; // retry every 60 frames (~1s at 60fps)
    if (_heightmapRetryFrame % _heightmapRetryInterval === 0) {
      for (const [entity, data] of context) {
        if (data.sampler.data !== null || !data.heightmapUrl) continue;
        loadHeightfield(
          data.heightmapUrl,
          Terrain.worldSize[entity],
          Terrain.maxHeight[entity]
        )
          .then((sampler) => {
            if (!context.has(entity)) return;
            applyLoadedSampler(state, entity, data, sampler);
          })
          .catch((err) => {
            logger.error(
              `Heightmap retry failed: ${data.heightmapUrl} — ${err instanceof Error ? err.message : err}`
            );
          });
      }
    }
  },
};

/**
 * Build a Rapier heightfield (column-major) for one chunk, sampled over
 * [origin ± size/2] at the chunk's mesh resolution so the collider surface is
 * identical to {@link buildChunkGeometry}. The array has (res+1)² vertices.
 * Small per-chunk fields keep each heightfield well under the size at which
 * Rapier's WASM panics on a single giant terrain-wide field.
 */
function buildChunkHeightfield(
  sampler: HeightSampler,
  originX: number,
  originZ: number,
  size: number,
  resolution: number
): { heights: Float32Array; nrows: number; ncols: number } {
  const nrows = Math.max(1, resolution);
  const ncols = nrows;
  const rows = nrows + 1;
  const cols = ncols + 1;
  const heights = new Float32Array(rows * cols);
  const half = size / 2;

  for (let col = 0; col < cols; col++) {
    const localX = originX - half + (col / ncols) * size;
    for (let row = 0; row < rows; row++) {
      const localZ = originZ - half + (row / nrows) * size;
      heights[col * rows + row] = sampleHeightAt(sampler, localX, localZ);
    }
  }

  return { heights, nrows, ncols };
}

function createChunkCollider(
  rapierWorld: RAPIER.World,
  sampler: HeightSampler,
  offset: { x: number; y: number; z: number },
  originX: number,
  originZ: number,
  size: number,
  resolution: number
): RAPIER.RigidBody {
  const { heights, nrows, ncols } = buildChunkHeightfield(
    sampler,
    originX,
    originZ,
    size,
    resolution
  );

  const body = rapierWorld.createRigidBody(
    RAPIER.RigidBodyDesc.fixed().setTranslation(
      offset.x + originX,
      offset.y,
      offset.z + originZ
    )
  );

  const colliderDesc = RAPIER.ColliderDesc.heightfield(nrows, ncols, heights, {
    x: size,
    y: 1.0,
    z: size,
  })
    .setFriction(0.7)
    .setRestitution(0.0);

  rapierWorld.createCollider(colliderDesc, body);
  return body;
}

/**
 * Per-chunk terrain physics. Before the heightmap decodes, a single flat cuboid
 * stands in so the player has ground; once the real heights are available each
 * visual LOD chunk gets its own heightfield collider (built/torn down to track
 * the chunk set), so collision matches the rendered surface everywhere.
 */
export const TerrainChunkColliderSystem: System = {
  group: 'simulation',
  update(state: State) {
    if (state.headless) return;

    const rapierWorld = getRapierWorld(state);
    if (!rapierWorld) return;

    const context = getTerrainContext(state);

    for (const fieldEntity of terrainQuery(state.world)) {
      const data = context.get(fieldEntity);
      if (!data || !data.initialized) continue;

      const sampler = data.sampler;
      const offset = data.worldOffset;
      const worldSize = Terrain.worldSize[fieldEntity];

      if (!sampler.data) {
        // Flat stand-in ground until the heightmap finishes decoding.
        if (!data.physicsBody) {
          const body = rapierWorld.createRigidBody(
            RAPIER.RigidBodyDesc.fixed().setTranslation(
              offset.x,
              offset.y,
              offset.z
            )
          );
          const half = worldSize / 2;
          data.physicsCollider = rapierWorld.createCollider(
            RAPIER.ColliderDesc.cuboid(half, 0.01, half)
              .setFriction(0.7)
              .setRestitution(0.0),
            body
          );
          data.physicsBody = body;
          data.collisionReady = true;
        }
        continue;
      }

      // Heights are ready: keep the flat stand-in until at least one real
      // chunk heightfield is built, so the player never falls through a window
      // between the flat ground and the per-chunk colliders.
      const hasChunkCollider = data.chunkColliders.size > 0;
      if (data.physicsBody && hasChunkCollider) {
        rapierWorld.removeRigidBody(data.physicsBody);
        data.physicsBody = null;
        data.physicsCollider = null;
      }

      // Only the chunks near the player need colliders — building a heightfield
      // for every visible (incl. distant) chunk wastes CPU/memory and churns as
      // far chunks change LOD. Use the camera as the player proxy.
      const cams = mainCameraQuery(state.world);
      const hasCam = cams.length > 0;
      const camLocalX = hasCam ? WorldTransform.posX[cams[0]] - offset.x : 0;
      const camLocalZ = hasCam ? WorldTransform.posZ[cams[0]] - offset.z : 0;
      const inRange = (chunk: number): boolean => {
        if (!hasCam) return true;
        // A chunk is in range when the camera is within PHYSICS_COLLIDER_RADIUS
        // of the chunk's AABB (clamp the camera to the chunk bounds, then measure
        // to that nearest point). This includes the chunk the camera stands on
        // (distance 0) AND any neighbour whose edge is within RADIUS — so as the
        // player nears a chunk boundary the next chunk's collider is already
        // built. A centre/corner distance test only covered the single chunk
        // under the camera once LOD chunks got large (≥1250), letting the player
        // walk off its edge and fall through the unbuilt neighbour.
        //
        // originX/Z is the chunk CENTRE (buildChunkHeightfield samples
        // originX ± size/2); using [ox, ox+size] as the AABB treated the centre
        // as the min corner and skipped the three non-negative quadrants at the
        // player spawn, dropping the hero through any chunk without a collider.
        const ox = TerrainChunk.originX[chunk];
        const oz = TerrainChunk.originZ[chunk];
        const half = TerrainChunk.size[chunk] * 0.5;
        const nearestX = Math.max(ox - half, Math.min(camLocalX, ox + half));
        const nearestZ = Math.max(oz - half, Math.min(camLocalZ, oz + half));
        const dx = camLocalX - nearestX;
        const dz = camLocalZ - nearestZ;
        return (
          dx * dx + dz * dz <= PHYSICS_COLLIDER_RADIUS * PHYSICS_COLLIDER_RADIUS
        );
      };

      for (const chunk of data.chunks) {
        if (
          data.chunkColliders.has(chunk) ||
          !state.exists(chunk) ||
          !inRange(chunk)
        )
          continue;
        const body = createChunkCollider(
          rapierWorld,
          sampler,
          offset,
          TerrainChunk.originX[chunk],
          TerrainChunk.originZ[chunk],
          TerrainChunk.size[chunk],
          TerrainChunk.resolution[chunk]
        );
        data.chunkColliders.set(chunk, body);
      }

      for (const [chunk, body] of data.chunkColliders) {
        if (data.chunks.has(chunk) && state.exists(chunk) && inRange(chunk))
          continue;
        rapierWorld.removeRigidBody(body);
        data.chunkColliders.delete(chunk);
      }

      if (data.chunkColliders.size > 0) data.collisionReady = true;
    }
  },
  dispose(state: State) {
    const rapierWorld = getRapierWorld(state);
    if (!rapierWorld) return;
    const context = getTerrainContext(state);
    for (const [, data] of context) {
      if (data.physicsBody) {
        rapierWorld.removeRigidBody(data.physicsBody);
        data.physicsBody = null;
        data.physicsCollider = null;
      }
      removeChunkColliders(rapierWorld, data);
    }
  },
};

export const TerrainDebugSystem: System = {
  group: 'draw',
  after: [CameraSyncSystem],
  update(state: State) {
    const context = getTerrainContext(state);
    const now = state.time.elapsed;

    for (const entity of debugQuery(state.world)) {
      const data = context.get(entity);
      if (!data || !data.initialized) continue;

      const count = data.chunks.size;
      TerrainDebugInfo.activeChunks[entity] = count;
      TerrainDebugInfo.drawCalls[entity] = count;
      TerrainDebugInfo.totalInstances[entity] = count;
      TerrainDebugInfo.geometryCount[entity] = count;
      TerrainDebugInfo.materialCount[entity] = count;
      TerrainDebugInfo.lastUpdated[entity] = now;
    }
  },
};

export function getTerrainHeightAt(
  state: State,
  worldX: number,
  worldZ: number
): number {
  const context = getTerrainContext(state);
  for (const [, data] of context) {
    if (!data.initialized) continue;
    const localX = worldX - data.worldOffset.x;
    const localZ = worldZ - data.worldOffset.z;
    return sampleHeightAt(data.sampler, localX, localZ);
  }
  return 0;
}

/**
 * True if the terrain has a real chunk heightfield collider that covers
 * (worldX, worldZ). Unlike terrainReady(), this checks the actual collider set
 * under the point rather than the field-level "collisionReady" latch.
 */
export function isTerrainColliderAt(
  state: State,
  worldX: number,
  worldZ: number
): boolean {
  const rapierWorld = getRapierWorld(state);
  if (!rapierWorld) return false;
  const context = getTerrainContext(state);
  for (const [, data] of context) {
    if (!data.initialized || data.chunkColliders.size === 0) continue;
    const localX = worldX - data.worldOffset.x;
    const localZ = worldZ - data.worldOffset.z;
    for (const [chunk, body] of data.chunkColliders) {
      if (!state.exists(chunk)) continue;
      const ox = TerrainChunk.originX[chunk];
      const oz = TerrainChunk.originZ[chunk];
      const half = TerrainChunk.size[chunk] * 0.5;
      if (localX < ox - half || localX > ox + half) continue;
      if (localZ < oz - half || localZ > oz + half) continue;
      if (body.numColliders() > 0) return true;
    }
  }
  return false;
}

export function findNearestTerrainEntity(
  state: State,
  worldX: number,
  worldZ: number
): number {
  const context = getTerrainContext(state);
  let bestEntity = 0;
  let bestDist = Infinity;

  for (const [entity, data] of context) {
    if (!data.initialized) continue;
    const dx = data.worldOffset.x - worldX;
    const dz = data.worldOffset.z - worldZ;
    const dist = dx * dx + dz * dz;
    if (dist < bestDist) {
      bestDist = dist;
      bestEntity = entity;
    }
  }
  return bestEntity;
}

export function setTerrainWireframe(
  state: State,
  entity: number,
  enabled: boolean
): void {
  const context = getTerrainContext(state);
  const data = context.get(entity);
  if (!data) return;

  Terrain.wireframe[entity] = enabled ? 1 : 0;
  data.lastWireframe = enabled ? 1 : 0;

  const registry = getChunkMeshRegistry(state);
  for (const chunk of data.chunks) {
    const mesh = registry.get(chunk);
    if (mesh) {
      (mesh.material as THREE.MeshStandardMaterial).wireframe = enabled;
    }
  }
}

export function reloadTerrainHeightmap(
  state: State,
  entity: number,
  url: string
): void {
  const context = getTerrainContext(state);
  const data = context.get(entity);
  if (!data) return;

  setTerrainHeightmapUrl(state, entity, url);
  data.heightmapUrl = url;

  const worldSize = Terrain.worldSize[entity];
  const maxHeight = Terrain.maxHeight[entity];
  loadHeightfield(url, worldSize, maxHeight)
    .then((sampler) => {
      const d = context.get(entity);
      if (!d) return;
      applyLoadedSampler(state, entity, d, sampler);
    })
    .catch((err) => {
      logger.error(`Heightmap reload failed: ${url}`, err);
    });
}

export function getTerrainStats(
  state: State,
  entity: number
): {
  activeChunks: number;
  drawCalls: number;
  totalInstances: number;
  geometries: number;
  materials: number;
  failedColliderChunks: number;
} | null {
  const context = getTerrainContext(state);
  const data = context.get(entity);
  if (!data?.initialized) return null;

  const count = data.chunks.size;
  return {
    activeChunks: count,
    drawCalls: count,
    totalInstances: count,
    geometries: count,
    materials: count,
    failedColliderChunks: TerrainDebugInfo.failedColliderChunks[entity] ?? 0,
  };
}
