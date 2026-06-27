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
import {
  createFlatSampler,
  createHeightmapSampler,
  loadHeightmapFromUrl,
  sampleHeightAt,
} from './height-sampler';
import type { HeightSampler } from './height-sampler';
import { chunkKey, resolutionForLevel, selectChunks } from './lod-select';
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
    t.repeat.set(32, 32);
    t.colorSpace = THREE.SRGBColorSpace;
  });
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(32, 32);
  _terrainTextureCache.set(url, tex);
  return tex;
}

function _loadNormalTex(url: string): THREE.Texture {
  let tex = _terrainTextureCache.get(url);
  if (tex) return tex;
  tex = _textureLoader.load(url, (t) => {
    t.wrapS = THREE.RepeatWrapping;
    t.wrapT = THREE.RepeatWrapping;
    t.repeat.set(32, 32);
  });
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(32, 32);
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
  t.repeat.set(32, 32);
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
  ctx.fillStyle = 'rgba(128,128,255,255)';
  ctx.fillRect(0, 0, size, size);

  const tex = new THREE.CanvasTexture(canvas);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(32, 32);
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
       vec4 biomeSplat() {
         return texture2D(uSplatMap, (vWorldXZ - uSplatMin) * uSplatInvSize);
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
         vec3 mapN = nrm * 2.0 - 1.0;
         mapN.xy *= normalScale;
         normal = normalize( tbn * mapN );
       #endif`
    );

    // Roughness: blend the packed alpha (= 1 − smoothness) per biome.
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
        loadHeightmapFromUrl(heightmapUrl)
          .then((imgData) => {
            const data = context.get(field);
            if (!data) return;
            data.sampler = createHeightmapSampler(
              worldSize,
              maxHeight,
              imgData
            );
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
        const res = resolutionForLevel(baseResolution, desc.level);
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

      const geometry = buildChunkGeometry(
        data.sampler,
        TerrainChunk.originX[chunk],
        TerrainChunk.originZ[chunk],
        TerrainChunk.size[chunk],
        TerrainChunk.resolution[chunk],
        skirtDepth,
        normalEpsilon
      );

      let mesh = registry.get(chunk);
      if (mesh) {
        mesh.geometry.dispose();
        mesh.geometry = geometry;
      } else {
        let material = _sharedTerrainMaterials.get(field);
        const texUrl = getTerrainTextureUrl(state, field);
        const expectedColor = texUrl ? 0xffffff : Terrain.baseColor[field];
        if (
          !material ||
          material.wireframe !== (Terrain.wireframe[field] === 1) ||
          material.color.getHex() !== expectedColor ||
          material.roughness !== Terrain.roughness[field] ||
          material.metalness !== Terrain.metalness[field]
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
            matOpts.normalScale = new THREE.Vector2(0.8, 0.8);
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
    }

    // Retry heightmap load if sampler still flat after bootstrap (async callback
    // may have failed — ensure terrain eventually gets real data).
    _heightmapRetryFrame++;
    const _heightmapRetryInterval = 60; // retry every 60 frames (~1s at 60fps)
    if (_heightmapRetryFrame % _heightmapRetryInterval === 0) {
      for (const [entity, data] of context) {
        if (data.sampler.data !== null || !data.heightmapUrl) continue;
        loadHeightmapFromUrl(data.heightmapUrl)
          .then((imgData) => {
            data.sampler = createHeightmapSampler(
              Terrain.worldSize[entity],
              Terrain.maxHeight[entity],
              imgData
            );
            for (const chunk of data.chunks) {
              TerrainChunk.meshDirty[chunk] = 1;
            }
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

      // Heights are ready: drop the flat stand-in and switch to per-chunk fields.
      if (data.physicsBody) {
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
  loadHeightmapFromUrl(url)
    .then((imgData) => {
      const d = context.get(entity);
      if (!d) return;
      d.sampler = createHeightmapSampler(worldSize, maxHeight, imgData);
      for (const chunk of d.chunks) {
        TerrainChunk.meshDirty[chunk] = 1;
      }
      const rapierWorld = getRapierWorld(state);
      if (d.physicsBody && rapierWorld) {
        rapierWorld.removeRigidBody(d.physicsBody);
        d.physicsBody = null;
        d.physicsCollider = null;
      }
      removeChunkColliders(rapierWorld, d);
      d.collisionReady = false;
      fireHeightmapReloadCallbacks(state);
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
