import { logger } from '../../core/utils/logger';
import * as RAPIER from '@dimforge/rapier3d-compat';
import * as THREE from 'three';
import CustomShaderMaterial from 'three-custom-shader-material/vanilla';
import { defineQuery, isPhysicsHeld } from '../../core';
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
  type ChunkDesc,
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

/** Cached hardware anisotropy max. Terrain floor textures benefit most from
 *  anisotropic filtering (near-grazing view angles cover the whole screen). */
let _maxAniso: number | null = null;
function maxAnisotropy(state: State): number {
  if (_maxAniso !== null) return _maxAniso;
  // Resolve lazily — textures load after the renderer exists. Fallback to 8
  // (safe desktop minimum) if the context isn't available yet.
  try {
    const ctx = getRenderingContext(state);
    if (ctx.renderer) {
      _maxAniso = ctx.renderer.capabilities.getMaxAnisotropy();
      return _maxAniso;
    }
  } catch {
    // Renderer not ready yet.
  }
  _maxAniso = 8;
  return _maxAniso;
}

function _loadTex(url: string, state: State): THREE.Texture {
  let tex = _terrainTextureCache.get(url);
  if (tex) return tex;
  const aniso = maxAnisotropy(state);
  tex = _textureLoader.load(url, (t) => {
    t.wrapS = THREE.RepeatWrapping;
    t.wrapT = THREE.RepeatWrapping;
    t.repeat.set(1, 1);
    t.colorSpace = THREE.SRGBColorSpace;
    t.anisotropy = aniso;
  });
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(1, 1);
  tex.anisotropy = aniso;
  _terrainTextureCache.set(url, tex);
  return tex;
}

function _loadNormalTex(url: string, state: State): THREE.Texture {
  let tex = _terrainTextureCache.get(url);
  if (tex) return tex;
  const aniso = maxAnisotropy(state);
  tex = _textureLoader.load(url, (t) => {
    t.wrapS = THREE.RepeatWrapping;
    t.wrapT = THREE.RepeatWrapping;
    t.repeat.set(1, 1);
    t.anisotropy = aniso;
  });
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(1, 1);
  tex.anisotropy = aniso;
  _terrainTextureCache.set(url, tex);
  return tex;
}

/** Flat default for the packed NAR map: R,G = (128,128) flat tangent normal
 *  X,Y; B = 255 (full AO, no darkening); A = 255 (fully rough). Used until a
 *  biome's real map loads. The shader reconstructs Z from X²+Y². */
const _flatNARTexture = (() => {
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
 * Peak sand opacity of the water-bed / shore mask. Keep a little biome bleed
 * (~8%) so swamp mud / peak snow still tint the bed, but high enough that
 * lake bottoms and banks read as sand through the water disc.
 */
const SAND_BLEND_MAX = 0.92;

/** Extra metres of soft sand fade past the river carve footprint. */
const RIVER_SAND_OUTER_PAD = 1.75;

/**
 * How far into the exposed bank full-strength sand reaches, as a fraction of
 * the waterline half-width. < 1 pushes the fade start onto the dry bank so
 * shores read sandy instead of only the wet channel floor.
 */
const RIVER_SAND_FULL_FRAC = 0.72;

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
 *  Lazy like _loadPackedNAR: built on first browser use, so importing the
 *  module in a DOM-less test environment does not touch `document`. */
let _sandAlbedoTexture: THREE.CanvasTexture | null = null;
function _getSandAlbedo(): THREE.Texture {
  if (_sandAlbedoTexture) return _sandAlbedoTexture;
  const size = 256;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d', { willReadFrequently: true })!;
  ctx.fillStyle = '#e6c98a';
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

/** Packed sand NAR: flat tangent normal X,Y (128,128) + full AO (255) +
 *  roughness ~0.92 (235). Under opaque-to-half water, crisp sand normals add
 *  nothing — a near-flat, rough surface reads correctly and keeps the sampler
 *  budget unchanged. Layout matches {@link _loadPackedNAR}. */
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

const _packedNARCache = new Map<string, THREE.Texture>();

/**
 * Build a packed surface texture for a terrain layer from its PBR maps using
 * the **NAR** (normal-XY + AO + roughness) layout, which fits three PBR channels
 * into a single RGBA sampler so the per-biome surface data stays inside the
 * WebGL2 fragment-sampler budget (14→14, no growth):
 *
 *   R = tangent-space normal.X      (from `*_normal.png` channel R)
 *   G = tangent-space normal.Y      (from `*_normal.png` channel G)
 *   B = ambient occlusion           (from `*_ao.png` channel R; 255 if absent)
 *   A = roughness = 1 − smoothness  (from `*_smoothness.png` channel R)
 *
 * Normal.Z is dropped and reconstructed in the fragment shader via
 * `sqrt(max(1 − X² − Y², 0))` — the standard "two-channel compressed normal"
 * trick, lossless for unit-length tangent-space normals. `albedoUrl` is the
 * layer's colour map (`/assets/textures/<name>.png`); the PBR maps live in
 * `pbr_<name>/`.
 */
function _loadPackedNAR(albedoUrl: string): THREE.Texture {
  const cached = _packedNARCache.get(albedoUrl);
  if (cached) return cached;

  const dir = albedoUrl.replace(/\/[^/]+$/, '');
  const name = albedoUrl
    .split('/')
    .pop()!
    .replace(/\.[^.]+$/, '');
  const normalUrl = `${dir}/pbr_${name}/${name}_normal.png`;
  const aoUrl = `${dir}/pbr_${name}/${name}_ao.png`;
  const smoothUrl = `${dir}/pbr_${name}/${name}_smoothness.png`;

  const size = 512;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d', { willReadFrequently: true })!;
  // Flat tangent normal XY (128,128) + full AO (255) + full rough (255) so the
  // texture reads correctly before any map loads. NOTE: css rgba() takes alpha
  // in 0–1 — 'rgba(...,255)' is invalid and silently leaves the canvas black
  // (normal (-1,-1), AO 0, roughness 0 → shiny) until the maps load.
  ctx.fillStyle = 'rgb(128,128,255)';
  ctx.fillRect(0, 0, size, size);

  const tex = new THREE.CanvasTexture(canvas);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(1, 1);
  tex.colorSpace = THREE.NoColorSpace;
  _packedNARCache.set(albedoUrl, tex);

  const nImg = new Image();
  const aImg = new Image();
  const sImg = new Image();
  let nReady = false;
  let aReady = false;
  let sReady = false;

  // Reusable scratch canvases so we don't allocate per combine() call.
  const nTmp = document.createElement('canvas');
  nTmp.width = size;
  nTmp.height = size;
  const nCtx = nTmp.getContext('2d', { willReadFrequently: true })!;
  const aTmp = document.createElement('canvas');
  aTmp.width = size;
  aTmp.height = size;
  const aCtx = aTmp.getContext('2d', { willReadFrequently: true })!;
  const sTmp = document.createElement('canvas');
  sTmp.width = size;
  sTmp.height = size;
  const sCtx = sTmp.getContext('2d', { willReadFrequently: true })!;

  const combine = (): void => {
    if (!nReady) return;
    // Start from the normal map's RGB (carries X/Y in R/G; B is dropped below).
    nCtx.clearRect(0, 0, size, size);
    nCtx.drawImage(nImg, 0, 0, size, size);
    const out = nCtx.getImageData(0, 0, size, size);

    if (aReady) {
      aCtx.clearRect(0, 0, size, size);
      aCtx.drawImage(aImg, 0, 0, size, size);
      const ao = aCtx.getImageData(0, 0, size, size);
      for (let i = 0; i < size * size; i++) {
        // B = AO (red channel of the AO map).
        out.data[i * 4 + 2] = ao.data[i * 4];
      }
    } else {
      // No AO map → full bright (no darkening).
      for (let i = 0; i < size * size; i++) out.data[i * 4 + 2] = 255;
    }

    if (sReady) {
      sCtx.clearRect(0, 0, size, size);
      sCtx.drawImage(sImg, 0, 0, size, size);
      const smo = sCtx.getImageData(0, 0, size, size);
      for (let i = 0; i < size * size; i++) {
        // A = roughness = 1 − smoothness (red channel of the smoothness map).
        out.data[i * 4 + 3] = 255 - smo.data[i * 4];
      }
    } else {
      // No smoothness map → fully rough.
      for (let i = 0; i < size * size; i++) out.data[i * 4 + 3] = 255;
    }

    ctx.putImageData(out, 0, 0);
    tex.needsUpdate = true;
  };

  nImg.onload = () => {
    nReady = true;
    combine();
  };
  aImg.onload = () => {
    aReady = true;
    combine();
  };
  sImg.onload = () => {
    sReady = true;
    combine();
  };
  // AO/smoothness are optional — 404 leaves the flag false and the channel
  // falls back to its neutral default.
  nImg.src = normalUrl;
  aImg.src = aoUrl;
  sImg.src = smoothUrl;
  return tex;
}

/** Material returned by {@link buildTerrainMaterial}: a `MeshStandardMaterial`
 *  (base-material params like color/roughness/wireframe stay real properties)
 *  whose `uniforms` are live — write `mat.uniforms.uSplatMap.value = …` any
 *  time, no `onBeforeCompile`/shader-ref indirection (see the `uTime`-freeze
 *  bug fixed the same way in the water shader). */
export type TerrainMaterial = THREE.MeshStandardMaterial & {
  uniforms: Record<string, { value: unknown }>;
};

const TERRAIN_VERTEX_SHADER = `
varying vec2 vWorldXZ;
varying float vWorldY;
varying vec3 vGeomNormal;
void main() {
  vec4 worldPos = modelMatrix * vec4(position, 1.0);
  vWorldXZ = worldPos.xz;
  vWorldY = worldPos.y;
  // Geometric (face) normal in world space — used for slope-based rock tint
  // and triplanar blend weights. This is the unsmoothed triangle normal, so
  // low-potency LOD chunks get a slightly harder slope edge; acceptable for
  // a colour tint that fades in at heightBlendStrength.
  vGeomNormal = normalize(mat3(modelMatrix) * normal);
}
`;

/**
 * Biome-blend fragment shader. Two layers of blending:
 *  1. `uMap2`/`uMixFactor` — legacy global crossfade kept for
 *     {@link swapTerrainTexture}.
 *  2. Biome splat — `uSplatMap` (RGBA, one biome per channel) sampled by world
 *     XZ, blending up to four `uLayer{0..3}` textures over the base. This gives
 *     real spatial cross-fades between adjacent biomes (no whole-map swap).
 * Uniforms default to an empty splat (all weights 0 → pure base) so the shader
 * renders correctly before any splat is supplied; {@link setTerrainSplat} +
 * the mesh system fill them in later.
 */
function terrainFragmentShader(): string {
  return `
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
// AO strength: 0 = AO map ignored (no darkening), 1 = full multiply. Packed
// into the NAR blue channel, so this just gates how strongly it applies.
uniform float uAoStrength;
// Height/slope colour tint: world-altitude band (low/mid/high) + rock on steep
// faces. Driven by Terrain component fields colourLow/Mid/High/Rock +
// snowHeight/slopeThreshold. heightBlendStrength gates the overall effect.
uniform vec3 uColorLow;
uniform vec3 uColorMid;
uniform vec3 uColorHigh;
uniform vec3 uColorRock;
uniform float uSnowHeight;   // normalised 0..1 of uMaxHeight where snow starts
uniform float uMaxHeight;    // terrain.maxHeight — converts vWorldY to 0..1
uniform float uSlopeThreshold;
uniform float uSlopeSoftness;
uniform float uHeightBlendStrength;
varying vec2 vWorldXZ;
varying float vWorldY;
varying vec3 vGeomNormal;

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
    // z = outer sand radius (carve footprint), w = waterline (shoreRadius).
    // Full sand on the bed (d ≤ shoreR); fade across the dry beach to the
    // carve rim — previously z was the water-disc radius, so exposed banks
    // stayed grassy.
    float outerR = uLakes[i].z;
    float shoreR = uLakes[i].w;
    vec2 rel = vWorldXZ - uLakes[i].xy;
    float d = length(rel);
    // Cheap reject past the organic overshoot of the carve rim.
    float rejectR = outerR * (1.0 + SHORE_SHAPE_AMP);
    if (d > rejectR) continue;
    float angle = atan(rel.y, rel.x);
    float s = lakeShapeRadius(angle, uLakes[i].x, uLakes[i].y);
    float beach = 1.0 - smoothstep(
      shoreR * s,
      max(outerR * s, shoreR * s + 0.001),
      d
    );
    m = max(m, beach);
  }
  return m;
}
// Sand band along river channels: full sand inside the waterline (dims.x),
// fading out at the outer beach edge (dims.y) — the polyline analogue of the
// lake's shoreR→r ring.
float riverMask() {
  float m = 0.0;
  for (int i = 0; i < ${MAX_RIVER_SEGS}; i++) {
    if (i >= uRiverSegCount) break;
    vec2 a = uRiverSegs[i].xy;
    vec2 b = uRiverSegs[i].zw;
    vec2 ab = b - a;
    float lenSq = max(dot(ab, ab), 1e-6);
    float t = clamp(dot(vWorldXZ - a, ab) / lenSq, 0.0, 1.0);
    vec2 closest = a + ab * t;
    vec2 toFrag = vWorldXZ - closest;
    // Early-out: if the fragment is farther than the outer sand half-width
    // (plus a small epsilon) this segment can't contribute — skip the
    // smoothstep. Confining the band math to the channel neighbourhood.
    float outerHalf = uRiverDims[i].y;
    if (dot(toFrag, toFrag) > outerHalf * outerHalf * 1.04) continue;
    float d = length(toFrag);
    float band = 1.0 - smoothstep(uRiverDims[i].x, max(outerHalf, uRiverDims[i].x + 0.001), d);
    m = max(m, band);
  }
  return m;
}
float sandMask() {
  return clamp(max(lakeMask(), riverMask()), 0.0, 1.0) * uSandBlend;
}

void main() {
  // Triplanar weight: on steep faces the top-down UV stretches the texture
  // along the slope, so we blend toward a side projection along the dominant
  // horizontal axis (X or Z). Only the dominant side axis is sampled (2× fetch
  // total: top + 1 side), not all three planes — keeps the per-fragment cost
  // bounded while still killing the stretched-texture look on cliffs. The
  // weight is zero on flat ground so plains pay nothing.
  float flatness = clamp(vGeomNormal.y, 0.0, 1.0);
  float triWeight = 1.0 - smoothstep(uSlopeThreshold - uSlopeSoftness,
                                     uSlopeThreshold + uSlopeSoftness, flatness);
  // Dominant side axis: pick X or Z from the geometric normal's horizontal
  // components. The side UV is the world plane perpendicular to that axis.
  vec2 triSideUv = vWorldXZ;
  if (abs(vGeomNormal.x) >= abs(vGeomNormal.z)) {
    // X-facing slope → project on the ZY plane (world .zy).
    triSideUv = vec2(vWorldXZ.y, vWorldY);
  } else {
    // Z-facing slope → project on the XY plane (world .xy).
    triSideUv = vec2(vWorldXZ.x, vWorldY);
  }

  // Albedo: base ↔ uMap2 legacy crossfade, then biome layers by splat, then
  // sand. Mirrors the original diffuseColor *= groundCol (diffuseColor starts
  // as vec4(diffuse, opacity) — csm_DiffuseColor's own default already folds
  // that in when there's no map, so the #ifdef USE_MAP branch fully overwrites
  // it and the no-map fallback is left untouched).
  #ifdef USE_MAP
    vec4 t1 = texture2D(map, vMapUv);
    vec4 t2 = texture2D(uMap2, vMapUv);
    vec4 groundCol = mix(t1, t2, uMixFactor);
    // Triplanar: pull the side-axis sample of the base albedo and blend by the
    // slope weight so cliffs show the texture projected face-on instead of
    // stretched along the grade.
    if (triWeight > 0.001) {
      vec4 t1side = texture2D(map, triSideUv);
      vec4 t2side = texture2D(uMap2, triSideUv);
      vec4 sideCol = mix(t1side, t2side, uMixFactor);
      groundCol = mix(groundCol, sideCol, triWeight);
    }
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
    csm_DiffuseColor = vec4(diffuse, opacity) * groundCol;
  #endif

  // Tangent-space normal: blend the base + biome packed normals (RGB), then
  // transform by a TBN reconstructed the same way three's own
  // normal_fragment_begin/_maps chunks do (getTangentFrame is declared at
  // file scope by three whenever USE_NORMALMAP_TANGENTSPACE is active without
  // vertex tangents — safe to call here even though this hook runs before
  // those chunks in program order). csm_FragNormal's default at this point
  // (normalize(vNormal), optionally face-flipped) is exactly what "normal"
  // holds at the same stage in the original chunk-based code, so it's the
  // correct surf_norm input.
  // Packed NAR (normal-XY + AO + roughness): one fetch per layer carries the
  // tangent-space normal X/Y (R/G), ambient occlusion (B), and roughness (A).
  // Normal.Z is reconstructed as sqrt(1 - X² - Y²) — lossless for unit normals
  // and frees the blue channel for AO without spending another sampler.
  vec4 nar = texture2D(uNRBase, vMapUv);
  // Triplanar on the surface data too, so cliff normals/AO/roughness come from
  // the face-on projection rather than the stretched top-down UV.
  if (triWeight > 0.001) {
    vec4 narSide = texture2D(uNRBase, triSideUv);
    nar = mix(nar, narSide, triWeight);
  }
  if (uLayerCount > 0.5) {
    vec4 splat = biomeSplat();
    nar = mix(nar, texture2D(uNR0, vMapUv), splat.r);
    if (uLayerCount > 1.5) nar = mix(nar, texture2D(uNR1, vMapUv), splat.g);
    if (uLayerCount > 2.5) nar = mix(nar, texture2D(uNR2, vMapUv), splat.b);
    if (uLayerCount > 3.5) nar = mix(nar, texture2D(uNR3, vMapUv), splat.a);
  }
  float sandN = sandMask();
  if (sandN > 0.001)
    nar = mix(nar, texture2D(uSandNR, vWorldXZ * uSandScale), sandN);

  #ifdef USE_NORMALMAP_TANGENTSPACE
    // Reconstruct tangent-space normal from X/Y (two-channel compressed form).
    vec2 nrmXY = nar.rg * 2.0 - 1.0;
    float nrmZ = sqrt(max(1.0 - dot(nrmXY, nrmXY), 0.0));
    vec3 nrm = vec3(nrmXY, nrmZ);
    // Named csmTbn/csmMapN (not tbn/mapN) to avoid colliding with the locals
    // three's own normal_fragment_begin/_maps chunks declare later in this
    // same function body (this hook runs before them, in the same GLSL scope
    // — reusing those names is a "redefinition" compile error).
    mat3 csmTbn = getTangentFrame(-vViewPosition, csm_FragNormal, vNormalMapUv);
    vec3 csmMapN = nrm;
    csmMapN.xy *= normalScale;
    csm_FragNormal = normalize(csmTbn * csmMapN);
  #endif

  // AO (blue channel of the packed NAR) — blend per biome via the same nar vec.
  // Multiply diffuse by the AO term so cavities/cracks darken naturally.
  float ao = nar.b;
  csm_DiffuseColor.rgb *= mix(1.0, ao, uAoStrength);

  // Height + slope colour tint. The texture albedo stays dominant; this adds a
  // subtle ambient gradient: cool white at the snow line, warm green in the
  // mid-band, darker valley greens low down, and neutral rock on steep faces.
  // heightBlendStrength=0 disables the whole pass. Sand shores mute the tint
  // so green valley cast doesn't wash out the beach.
  if (uHeightBlendStrength > 0.001) {
    float h = uMaxHeight > 0.0 ? clamp(vWorldY / uMaxHeight, 0.0, 1.0) : 0.0;
    // Three-band altitude blend: low → mid → high, softstepped at the knots.
    float midBand = smoothstep(0.0, 0.45, h);
    float highBand = smoothstep(max(uSnowHeight - 0.18, 0.0), min(uSnowHeight + 0.18, 1.0), h);
    vec3 altTint = mix(uColorLow, uColorMid, midBand);
    altTint = mix(altTint, uColorHigh, highBand);
    // Slope: 1.0 flat up, 0.0 vertical. Steep faces get rock regardless of
    // altitude (a cliff at snow-line shouldn't read as pure white).
    float flatness = clamp(vGeomNormal.y, 0.0, 1.0);
    float rockBand = 1.0 - smoothstep(uSlopeThreshold - uSlopeSoftness,
                                       uSlopeThreshold + uSlopeSoftness, flatness);
    vec3 tint = mix(altTint, uColorRock, rockBand);
    // Re-normalise the tint to unit luminance so it shifts hue/saturation
    // without darkening the albedo (tints are treated as 1.0-luminance anchors).
    tint /= max(dot(tint, vec3(0.299, 0.587, 0.114)), 1e-3);
    float sandMute = 1.0 - sandMask() * 0.9;
    csm_DiffuseColor.rgb = mix(csm_DiffuseColor.rgb,
                               csm_DiffuseColor.rgb * tint,
                               uHeightBlendStrength * sandMute);
  }

  // Roughness (alpha of the packed NAR = 1 − smoothness).
  float rgh = nar.a;
  csm_Roughness = roughness * rgh;
}
`;
}

/**
 * Build the terrain biome-blend material (replaces the old `onBeforeCompile`
 * injection). `matOpts` are forwarded to the underlying `MeshStandardMaterial`
 * (color/roughness/metalness/wireframe/map/normalMap/normalScale all stay
 * real, readable properties — the "should rebuild?" check at the call site
 * relies on this).
 */
function buildTerrainMaterial(
  matOpts: THREE.MeshStandardMaterialParameters,
  baseNR: THREE.Texture
): TerrainMaterial {
  const lakeVecs: THREE.Vector4[] = [];
  for (let i = 0; i < MAX_LAKES; i++) lakeVecs.push(new THREE.Vector4());
  const riverSegs: THREE.Vector4[] = [];
  const riverDims: THREE.Vector2[] = [];
  for (let i = 0; i < MAX_RIVER_SEGS; i++) {
    riverSegs.push(new THREE.Vector4());
    riverDims.push(new THREE.Vector2());
  }

  return new CustomShaderMaterial({
    baseMaterial: THREE.MeshStandardMaterial,
    ...matOpts,
    vertexShader: TERRAIN_VERTEX_SHADER,
    fragmentShader: terrainFragmentShader(),
    uniforms: {
      uMap2: { value: _emptyTexture },
      uMixFactor: { value: 0 },
      uSplatMap: { value: _emptyTexture },
      uLayer0: { value: _emptyTexture },
      uLayer1: { value: _emptyTexture },
      uLayer2: { value: _emptyTexture },
      uLayer3: { value: _emptyTexture },
      // Packed NAR (normal.XY + AO + roughness) per layer; base + 4 biomes.
      uNRBase: { value: baseNR },
      uNR0: { value: _flatNARTexture },
      uNR1: { value: _flatNARTexture },
      uNR2: { value: _flatNARTexture },
      uNR3: { value: _flatNARTexture },
      uLayerCount: { value: 0 },
      uSplatMin: { value: new THREE.Vector2(0, 0) },
      uSplatInvSize: { value: new THREE.Vector2(0, 0) },
      // Lake-bed sand blend: independent of the 4-channel splat budget (which
      // is already full), masked in-shader by world-XZ distance to each
      // registered lake. Pure uniform push — no material rebuild (applyLakeSand).
      uSandAlbedo: { value: _getSandAlbedo() },
      uSandNR: { value: _sandNRTexture },
      uSandScale: { value: SAND_UV_SCALE },
      uSandBlend: { value: 0 },
      uLakeCount: { value: 0 },
      // Per-lake: xy = world centre, z = outer sand radius (carve), w = waterline.
      uLakes: { value: lakeVecs },
      // Per river segment: xyzw = (ax, az, bx, bz) world endpoints; dims per
      // segment: x = full-sand half-width, y = outer sand half-width.
      uRiverSegs: { value: riverSegs },
      uRiverDims: { value: riverDims },
      uRiverSegCount: { value: 0 },
      // AO strength gate for the packed NAR blue channel.
      uAoStrength: { value: 0.85 },
      // Height/slope colour tint uniforms (synced from the Terrain component
      // by TerrainHeightColorSyncSystem).
      uColorLow: { value: new THREE.Color(0x4a6a2a) },
      uColorMid: { value: new THREE.Color(0x7a9a4a) },
      uColorHigh: { value: new THREE.Color(0xffffff) },
      uColorRock: { value: new THREE.Color(0x808080) },
      uSnowHeight: { value: 0.75 },
      uMaxHeight: { value: 50 },
      uSlopeThreshold: { value: 0.55 },
      uSlopeSoftness: { value: 0.1 },
      uHeightBlendStrength: { value: 0.35 },
    },
  }) as unknown as TerrainMaterial;
}

/** Tracks the splat version last pushed to each field's material uniforms. */
const _appliedSplatVersion = new WeakMap<State, Map<number, number>>();

/**
 * Push the per-field biome splat (if any) into the material's (live) shader
 * uniforms. Idempotent: only re-applies when the splat version changed.
 * Layer textures are loaded through the shared cache so they tile identically
 * to the base map.
 */
function applyTerrainSplat(state: State, field: number): void {
  const cfg = getTerrainSplat(state, field);
  if (!cfg) return;
  const mat = getSharedTerrainMaterials(state).get(field);
  if (!mat) return;

  let perState = _appliedSplatVersion.get(state);
  if (!perState) {
    perState = new Map();
    _appliedSplatVersion.set(state, perState);
  }
  if (perState.get(field) === cfg.version) return;

  const layers = cfg.layerUrls
    .slice(0, 4)
    .map((u) => (u ? _loadTex(u, state) : _emptyTexture));
  while (layers.length < 4) layers.push(_emptyTexture);
  const nrs = cfg.layerUrls
    .slice(0, 4)
    .map((u) => (u ? _loadPackedNAR(u) : _flatNARTexture));
  while (nrs.length < 4) nrs.push(_flatNARTexture);

  const u = mat.uniforms;
  u.uSplatMap.value = cfg.splatTexture;
  u.uLayer0.value = layers[0];
  u.uLayer1.value = layers[1];
  u.uLayer2.value = layers[2];
  u.uLayer3.value = layers[3];
  u.uNR0.value = nrs[0];
  u.uNR1.value = nrs[1];
  u.uNR2.value = nrs[2];
  u.uNR3.value = nrs[3];
  u.uLayerCount.value = Math.min(4, cfg.layerUrls.length);
  (u.uSplatMin.value as THREE.Vector2).set(cfg.worldMinX, cfg.worldMinZ);
  (u.uSplatInvSize.value as THREE.Vector2).set(
    cfg.worldSizeX > 0 ? 1 / cfg.worldSizeX : 0,
    cfg.worldSizeZ > 0 ? 1 / cfg.worldSizeZ : 0
  );
  perState.set(field, cfg.version);
}

/** Last lake-count signature pushed per field — re-push only when the lake
 *  set changes. */
const _appliedLakeSig = new WeakMap<State, Map<number, string>>();

/**
 * Push the lake-bed sand blend into the material's (live) shader uniforms:
 * lake centres/radii (world XZ) + master on/off. Idempotent like
 * {@link applyTerrainSplat}: the carve (water LakeApplySystem, group 'setup')
 * registers WaterBodies before this runs (group 'draw'), so the basin reads
 * sandy on the first post-carve frame without any geometry or material rebuild.
 */
function applyLakeSand(state: State, field: number): void {
  const mat = getSharedTerrainMaterials(state).get(field);
  if (!mat) return;
  const bodies = getWaterBodies(state);
  const sig = `${bodies.length}`;
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
      const waterHalf = (b.shoreWidth ?? b.width * 0.95) / 2;
      // Full sand reaches past the waterline onto the dry bank; fade covers
      // the rest of the carve (+ pad) so shores read sandy.
      const shoreHalf = Math.max(0.5, waterHalf * RIVER_SAND_FULL_FRAC);
      const carveHalf =
        b.carveWidth != null
          ? b.carveWidth / 2
          : b.width / 2 + Math.min(4, Math.max(1.5, b.width * 0.35));
      const outerHalf = Math.max(
        carveHalf + RIVER_SAND_OUTER_PAD,
        shoreHalf + 0.5
      );
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

  const u = mat.uniforms;
  const lakes = u.uLakes.value as THREE.Vector4[];
  for (let i = 0; i < count; i++) {
    const b = lakeBodies[i]!;
    // Guard for legacy bodies without shoreRadius: fall back to 0.7·radius so
    // the mask degrades gracefully rather than sanding the whole bowl.
    const shoreR = b.shoreRadius ?? b.radius * 0.7;
    // Outer sand = full carve footprint (banks past the water disc). Legacy
    // bodies without carveRadius keep the old water-disc outer edge.
    const outerR = Math.max(b.carveRadius ?? b.radius, shoreR + 0.25);
    lakes[i].set(b.x, b.z, outerR, shoreR);
  }
  u.uLakeCount.value = count;
  const riverSegs = u.uRiverSegs.value as THREE.Vector4[];
  const riverDims = u.uRiverDims.value as THREE.Vector2[];
  for (let i = 0; i < segs.length; i++) {
    const s = segs[i]!;
    riverSegs[i].set(s.ax, s.az, s.bx, s.bz);
    riverDims[i].set(s.shoreHalf, s.outerHalf);
  }
  u.uRiverSegCount.value = segs.length;
  u.uSandBlend.value = count > 0 || segs.length > 0 ? SAND_BLEND_MAX : 0;
  perState.set(field, sig);
}

/** Build per-chunk terrain colliders only within this radius of the player. */
const PHYSICS_COLLIDER_RADIUS = 128;

/** Re-run the (allocating) quadtree LOD selection only after the camera moves
 * this far — LOD boundaries are tens of metres apart, so per-frame reselection
 * is wasted work while standing or moving slowly. */
const LOD_RESELECT_DISTANCE = 6;
/** After the loading hold lifts, rebuild at most this many dirty chunk meshes
 *  per frame so a camera orbit that triggers a LOD reselect doesn't hitch. */
const MAX_CHUNK_MESH_BUILDS_PER_FRAME = 4;
const _lastLodCam = new Map<number, { x: number; z: number }>();
const _desiredKeysScratch = new Map<string, ChunkDesc>();
const _existingKeysScratch = new Map<string, number>();
let _heightmapRetryFrame = 0;

/** Shared materials per terrain field — avoids N duplicate Material instances for N chunks. */
const _sharedTerrainMaterialsByState = new WeakMap<
  State,
  Map<number, TerrainMaterial>
>();

function getSharedTerrainMaterials(state: State): Map<number, TerrainMaterial> {
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
  // Final teardown — drain the body pool too so nothing leaks on dispose /
  // entity destroy. The pool only holds parked (collider-less) bodies.
  if (data.chunkBodyPool && rapierWorld) {
    for (const body of data.chunkBodyPool) {
      rapierWorld.removeRigidBody(body);
    }
    data.chunkBodyPool.length = 0;
  }
}

/**
 * Recycle a field's chunk bodies into the pool instead of destroying them.
 * Used on heightmap (re)load: the colliders are stale (new heights) but the
 * bodies will be needed again immediately for the rebuilt chunks, so returning
 * them to the pool avoids a destroy+create burst that would otherwise hit the
 * broadphase on every reload.
 */
function recycleChunkColliders(
  rapierWorld: RAPIER.World | null,
  data: import('./utils').TerrainEntityData
): void {
  if (!data.chunkBodyPool) data.chunkBodyPool = [];
  if (rapierWorld) {
    for (const body of data.chunkColliders.values()) {
      recycleChunkBody(rapierWorld, body, data.chunkBodyPool);
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
export function applyLoadedSampler(
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
  // Recycle chunk bodies into the pool (they'll be reused immediately as the
  // rebuilt chunks enter the ring), instead of destroy+create on reload.
  recycleChunkColliders(rapierWorld, data);
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
          const newTex = _loadTex(currentTexUrl, state);
          const bs = _getBlendState(state, fieldEntity);
          const mat = getSharedTerrainMaterials(state).get(fieldEntity);
          if (mat && bs.fromTex && oldUrl) {
            bs.toTex = newTex;
            bs.mix = 0;
            bs.active = true;
            mat.uniforms.uMap2.value = newTex;
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
        if (mat) mat.uniforms.uMixFactor.value = bs.mix;
        if (bs.mix >= 1) {
          bs.active = false;
          if (mat && bs.toTex) {
            mat.map = bs.toTex;
            mat.needsUpdate = true;
            bs.fromTex = bs.toTex;
            mat.uniforms.uMixFactor.value = 0;
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

      const desiredKeys = _desiredKeysScratch;
      desiredKeys.clear();
      for (const desc of desired) {
        desiredKeys.set(chunkKey(desc), desc);
      }

      const existingKeys = _existingKeysScratch;
      existingKeys.clear();
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

    // During loading, build every dirty chunk immediately so the spawn ring is
    // complete before the overlay drops. Afterwards, budget rebuilds so a
    // first-look LOD reselect cannot stall a frame with dozens of geometries.
    const meshBudget = isPhysicsHeld(state)
      ? Number.POSITIVE_INFINITY
      : MAX_CHUNK_MESH_BUILDS_PER_FRAME;
    let meshesBuilt = 0;

    for (const chunk of chunkQuery(state.world)) {
      if (TerrainChunk.meshDirty[chunk] !== 1) continue;
      if (meshesBuilt >= meshBudget) break;

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
          let baseNR: THREE.Texture = _flatNARTexture;
          if (texUrl) {
            matOpts.map = _loadTex(texUrl, state);
            const baseName = texUrl.replace(/\/[^/]+$/, '');
            const texName = texUrl.split('/').pop()!.replace('.png', '');
            const normalUrl = `${baseName}/pbr_${texName}/${texName}_normal.png`;
            matOpts.normalMap = _loadNormalTex(normalUrl, state);
            // `normal-strength` XML attr (Terrain.normalStrength); the blend
            // shader multiplies the packed uNR* normals by this normalScale.
            matOpts.normalScale = new THREE.Vector2(
              normalStrength,
              normalStrength
            );
            // Packed normal+roughness for the base layer. Assigned as the
            // roughnessMap purely to switch on USE_ROUGHNESSMAP (the shader
            // override re-samples it); the real per-biome blend uses uNR*.
            baseNR = _loadPackedNAR(texUrl);
            matOpts.roughnessMap = baseNR;
          }
          material = buildTerrainMaterial(matOpts, baseNR);
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
      meshesBuilt++;
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
  resolution: number,
  pool?: RAPIER.RigidBody[]
): RAPIER.RigidBody {
  const { heights, nrows, ncols } = buildChunkHeightfield(
    sampler,
    originX,
    originZ,
    size,
    resolution
  );

  // Reuse a pooled body when available: the body (and its broadphase entry)
  // is the expensive part to create/destroy in Rapier. We only need to
  // reposition it and attach a fresh heightfield collider (the collider's
  // heights array differs per chunk, so the collider itself can't be reused).
  let body: RAPIER.RigidBody;
  if (pool && pool.length > 0) {
    body = pool.pop()!;
    body.setTranslation(
      {
        x: offset.x + originX,
        y: offset.y,
        z: offset.z + originZ,
      },
      true
    );
  } else {
    body = rapierWorld.createRigidBody(
      RAPIER.RigidBodyDesc.fixed().setTranslation(
        offset.x + originX,
        offset.y,
        offset.z + originZ
      )
    );
  }

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
 * Return a chunk's body to the pool for reuse. Removes the attached colliders
 * (heightfield data differs per chunk and can't be reused) but keeps the body
 * alive in the world with its broadphase entry intact, parked far below the
 * terrain so it can't interfere with anything while idle.
 */
const POOL_PARK_Y = -100000;
function recycleChunkBody(
  rapierWorld: RAPIER.World,
  body: RAPIER.RigidBody,
  pool: RAPIER.RigidBody[]
): void {
  // Detach every collider on this body (a recycled body carries a stale
  // heightfield otherwise). Iterate by index — Rapier shifts indices on
  // removal, so detach from the end (last index stays valid until popped).
  const n = body.numColliders();
  for (let i = n - 1; i >= 0; i--) {
    rapierWorld.removeCollider(body.collider(i), true);
  }
  // Park the body far outside the play area. It stays registered in the
  // broadphase but with no colliders, so it costs ~nothing to keep around
  // and is ready for immediate reuse.
  body.setTranslation({ x: 0, y: POOL_PARK_Y, z: 0 }, true);
  pool.push(body);
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

      if (!data.chunkBodyPool) data.chunkBodyPool = [];
      const pool = data.chunkBodyPool;

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
          TerrainChunk.resolution[chunk],
          pool
        );
        data.chunkColliders.set(chunk, body);
      }

      for (const [chunk, body] of data.chunkColliders) {
        if (data.chunks.has(chunk) && state.exists(chunk) && inRange(chunk))
          continue;
        // Recycle the body (detach colliders, park below the world) instead of
        // destroying it — the next chunk entering the ring reuses it.
        recycleChunkBody(rapierWorld, body, pool);
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

/**
 * Sync the height/slope colour-tint uniforms from each `Terrain` entity's
 * component fields into its shared material. Runs once the material exists
 * (after `TerrainMeshSystem` builds it) and re-pushes whenever a field changes
 * (dirty-gated per field). This is what finally wires up the long-dormant
 * `colorHigh/Mid/Low/Rock`, `snowHeight`, `slopeThreshold`, `slopeSoftness`
 * component fields into the live shader.
 */
interface TintCache {
  colorLow: number;
  colorMid: number;
  colorHigh: number;
  colorRock: number;
  snowHeight: number;
  maxHeight: number;
  slopeThreshold: number;
  slopeSoftness: number;
  heightBlendStrength: number;
  aoStrength: number;
}
const _tintCacheByField = new WeakMap<THREE.Material, TintCache>();

export const TerrainHeightColorSyncSystem: System = {
  group: 'draw',
  after: [TerrainMeshSystem],
  update(state: State) {
    if (state.headless) return;
    const materials = getSharedTerrainMaterials(state);
    if (materials.size === 0) return;

    for (const entity of terrainQuery(state.world)) {
      const mat = materials.get(entity);
      if (!mat) continue;

      const colorLow = Terrain.colorLow[entity];
      const colorMid = Terrain.colorMid[entity];
      const colorHigh = Terrain.colorHigh[entity];
      const colorRock = Terrain.colorRock[entity];
      const snowHeight = Terrain.snowHeight[entity];
      const maxHeight = Terrain.maxHeight[entity];
      const slopeThreshold = Terrain.slopeThreshold[entity];
      const slopeSoftness = Terrain.slopeSoftness[entity];
      const heightBlendStrength = Terrain.heightBlendStrength[entity];
      const aoStrength = Terrain.aoStrength[entity];

      let cache = _tintCacheByField.get(mat);
      if (!cache) {
        cache = {
          colorLow: NaN,
          colorMid: NaN,
          colorHigh: NaN,
          colorRock: NaN,
          snowHeight: NaN,
          maxHeight: NaN,
          slopeThreshold: NaN,
          slopeSoftness: NaN,
          heightBlendStrength: NaN,
          aoStrength: NaN,
        };
        _tintCacheByField.set(mat, cache);
      }

      const u = mat.uniforms;
      if (cache.colorLow !== colorLow) {
        (u.uColorLow.value as THREE.Color).set(colorLow);
        cache.colorLow = colorLow;
      }
      if (cache.colorMid !== colorMid) {
        (u.uColorMid.value as THREE.Color).set(colorMid);
        cache.colorMid = colorMid;
      }
      if (cache.colorHigh !== colorHigh) {
        (u.uColorHigh.value as THREE.Color).set(colorHigh);
        cache.colorHigh = colorHigh;
      }
      if (cache.colorRock !== colorRock) {
        (u.uColorRock.value as THREE.Color).set(colorRock);
        cache.colorRock = colorRock;
      }
      if (cache.snowHeight !== snowHeight) {
        u.uSnowHeight.value = snowHeight;
        cache.snowHeight = snowHeight;
      }
      if (cache.maxHeight !== maxHeight) {
        u.uMaxHeight.value = maxHeight;
        cache.maxHeight = maxHeight;
      }
      if (cache.slopeThreshold !== slopeThreshold) {
        u.uSlopeThreshold.value = slopeThreshold;
        cache.slopeThreshold = slopeThreshold;
      }
      if (cache.slopeSoftness !== slopeSoftness) {
        u.uSlopeSoftness.value = slopeSoftness;
        cache.slopeSoftness = slopeSoftness;
      }
      if (cache.heightBlendStrength !== heightBlendStrength) {
        u.uHeightBlendStrength.value = heightBlendStrength;
        cache.heightBlendStrength = heightBlendStrength;
      }
      if (cache.aoStrength !== aoStrength) {
        u.uAoStrength.value = aoStrength;
        cache.aoStrength = aoStrength;
      }
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
