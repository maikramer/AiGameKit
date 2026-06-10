import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import {
  createGLTFLoader,
  normalizeGltfMaterials,
} from '../../extras/gltf-bridge';
import { getRenderingContext, getScene, threeCameras } from '../rendering';
import type { State } from '../../core';
import type { VegetationInstanceSpec, VegetationSpawnState } from './types';

const MAX_INSTANCES = 500;
const LOD1_DISTANCE = 80;
const LOD2_DISTANCE = 200;
const LOD1_DISTANCE_SQ = LOD1_DISTANCE * LOD1_DISTANCE;
const LOD2_DISTANCE_SQ = LOD2_DISTANCE * LOD2_DISTANCE;
/** Camera must move at least this far (world units) before LOD buckets are
 * recomputed. Standing still or micro-movements cost zero per-frame work — the
 * matrices are static, so nothing changes until the camera travels enough to
 * cross an LOD boundary. Well below the 80u LOD0→LOD1 ring, so no visible pop. */
const REPACK_CAM_MOVE = 4;
const REPACK_CAM_MOVE_SQ = REPACK_CAM_MOVE * REPACK_CAM_MOVE;
const DUMMY = new THREE.Object3D();

/**
 * LOD tier for a squared camera distance: 0 = full detail, 1 = mid, 2 = far.
 * Pure and exported so the distance thresholds can be verified in isolation.
 */
export function vegetationLodTier(distanceSq: number): 0 | 1 | 2 {
  if (distanceSq < LOD1_DISTANCE_SQ) return 0;
  if (distanceSq < LOD2_DISTANCE_SQ) return 1;
  return 2;
}

const LOD_TIER_KEYS = ['lod0', 'lod1', 'lod2'] as const;

interface GeometryMaterialPair {
  geometry: THREE.BufferGeometry;
  material: THREE.Material;
}

function extractGeometryMaterialPairs(
  scene: THREE.Object3D
): GeometryMaterialPair[] {
  const pairs: GeometryMaterialPair[] = [];
  scene.traverse((child) => {
    if ((child as THREE.Mesh).isMesh) {
      const mesh = child as THREE.Mesh;
      pairs.push({
        geometry: mesh.geometry,
        material: mesh.material as THREE.Material,
      });
    }
  });
  return pairs;
}

export class VegetationInstancer {
  private state: VegetationSpawnState;
  private lodPairs = new Map<string, GeometryMaterialPair[]>();
  private gltfLoader: GLTFLoader;
  private lodMeshes = new Map<string, THREE.InstancedMesh[]>();
  private instanceCount = 0;

  // === Immutable per-instance data, computed once at spawn time ===
  /** Local→world matrix per instance (row-major 16 floats), indexed by i. */
  private srcMatrix = new Float32Array(MAX_INSTANCES * 16);
  /** Instance world position (x, y, z) used for cheap LOD distance tests. */
  private instancePos = new Float32Array(MAX_INSTANCES * 3);
  /** Number of geometry/material pairs per LOD (instances round-robin across). */
  private numPairs = 0;
  /** Whether more than one LOD exists; single-LOD groups never need repacking. */
  private lodEnabled = false;
  /** Max bounding radius (geometry radius × scale) over all instances. */
  private maxInstanceRadius = 0;
  /** Geometry bounding radius (from instance origin) of the LOD0 model. */
  private geomRadius = 0.5;

  // === Reused per-frame scratch (no per-frame allocations) ===
  private readonly camPos = new THREE.Vector3();
  private lastCamX = 0;
  private lastCamY = 0;
  private lastCamZ = 0;
  private hasRepacked = false;
  /** Write cursors per LOD key, one entry per geometry/material pair. */
  private cursors = new Map<string, Int32Array>();
  /** AABB of instance positions, for the static bounding sphere. */
  private boundsMin = new THREE.Vector3(Infinity, Infinity, Infinity);
  private boundsMax = new THREE.Vector3(-Infinity, -Infinity, -Infinity);

  constructor() {
    this.state = {
      spec: { url: '', role: 'static' },
      clusters: [],
      ready: false,
      maxInstances: MAX_INSTANCES,
      scene: null,
      compiled: false,
    };
    this.gltfLoader = createGLTFLoader();
  }

  async initializeFromSpec(
    spec: VegetationInstanceSpec,
    state: State
  ): Promise<void> {
    this.state.spec = spec;
    this.state.scene = getScene(state) ?? null;

    try {
      await this.loadGlbs(spec.url, spec.lod1Url, spec.lod2Url);
      this.buildInstancedMeshes();
    } catch (err) {
      console.warn('[vegetation] Failed to load GLBs:', err);
      this.state.ready = false;
      return;
    }
  }

  private async loadGlbs(
    url: string,
    lod1Url?: string,
    lod2Url?: string
  ): Promise<void> {
    const lodUrls = new Map<string, string>();
    lodUrls.set('lod0', url);
    if (lod1Url) lodUrls.set('lod1', lod1Url);
    if (lod2Url) lodUrls.set('lod2', lod2Url);

    const loadPromises = Array.from(lodUrls.entries()).map(
      async ([lodKey, lodUrl]) => {
        const gltf = await this.gltfLoader.loadAsync(lodUrl);
        normalizeGltfMaterials(gltf.scene);
        this.lodPairs.set(lodKey, extractGeometryMaterialPairs(gltf.scene));
      }
    );

    await Promise.all(loadPromises);
  }

  private buildInstancedMeshes(): void {
    if (!this.state.scene) return;

    for (const [lodKey, pairs] of this.lodPairs) {
      const meshes: THREE.InstancedMesh[] = [];
      for (const { geometry, material } of pairs) {
        const im = new THREE.InstancedMesh(geometry, material, MAX_INSTANCES);
        im.castShadow = true;
        im.receiveShadow = true;
        im.frustumCulled = true;
        im.count = 0;
        im.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
        im.instanceMatrix.needsUpdate = true;
        this.state.scene.add(im);
        meshes.push(im);
      }
      this.lodMeshes.set(lodKey, meshes);
      this.cursors.set(lodKey, new Int32Array(meshes.length));
    }

    const lod0Pairs = this.lodPairs.get('lod0');
    this.numPairs = lod0Pairs?.length ?? 0;
    this.lodEnabled = this.lodMeshes.size > 1;

    // Geometry bounding radius from the instance origin, computed once so the
    // static bounding sphere can account for the model's own extent.
    if (lod0Pairs) {
      let r = 0;
      for (const { geometry } of lod0Pairs) {
        if (!geometry.boundingSphere) geometry.computeBoundingSphere();
        const bs = geometry.boundingSphere;
        if (bs) r = Math.max(r, bs.center.length() + bs.radius);
      }
      if (r > 0) this.geomRadius = r;
    }
  }

  markReady(state: State): void {
    if (!this.state.scene) {
      this.state.scene = getScene(state) ?? null;
    }
    if (!this.state.scene) return;

    const ctx = getRenderingContext(state);
    const cam = ctx.renderer ? this.getCamera(state) : null;
    if (cam && ctx.renderer) {
      ctx.renderer.compile(this.state.scene, cam);
    }
    this.state.compiled = true;
    this.state.ready = true;

    // Instances are static, so a single bounding sphere covering them all is
    // valid for every LOD mesh and every frame — set it once and let three's
    // frustum culling reuse it (it won't recompute a non-null boundingSphere).
    this.assignStaticBoundingSphere();

    // Initial upload of the LOD0 matrices placed by addInstance.
    const lod0Meshes = this.lodMeshes.get('lod0');
    if (lod0Meshes) {
      for (const mesh of lod0Meshes) mesh.instanceMatrix.needsUpdate = true;
    }
  }

  addInstance(
    x: number,
    y: number,
    z: number,
    scale: number,
    yaw: number,
    alignEuler?: [number, number, number]
  ): void {
    if (this.instanceCount >= MAX_INSTANCES) return;
    if (this.numPairs === 0) return;

    const i = this.instanceCount;
    const meshIndex = i % this.numPairs;

    DUMMY.position.set(x, y, z);
    DUMMY.scale.setScalar(scale);
    if (alignEuler) {
      DUMMY.rotation.set(alignEuler[0], alignEuler[1], alignEuler[2]);
    } else {
      DUMMY.rotation.set(0, yaw, 0);
    }
    DUMMY.updateMatrix();

    // Store immutable source data (the matrix is identical across LODs).
    DUMMY.matrix.toArray(this.srcMatrix, i * 16);
    this.instancePos[i * 3] = x;
    this.instancePos[i * 3 + 1] = y;
    this.instancePos[i * 3 + 2] = z;

    this.boundsMin.min(DUMMY.position);
    this.boundsMax.max(DUMMY.position);
    this.maxInstanceRadius = Math.max(
      this.maxInstanceRadius,
      this.geomRadius * scale
    );

    // Seed the LOD0 mesh so single-LOD groups (which never repack) render
    // immediately; multi-LOD groups overwrite this on the first update().
    const lod0Meshes = this.lodMeshes.get('lod0');
    const im = lod0Meshes?.[meshIndex];
    if (im) {
      im.setMatrixAt(i, DUMMY.matrix);
      im.count = Math.max(im.count, i + 1);
    }

    this.instanceCount++;
  }

  update(state: State): void {
    if (!this.state.ready) return;
    // Single-LOD groups have fixed matrices placed at spawn — nothing to do.
    if (!this.lodEnabled) return;

    const lod0Meshes = this.lodMeshes.get('lod0');
    if (!lod0Meshes || lod0Meshes.length === 0) return;

    const cam = this.getCamera(state);
    if (!cam) return;
    cam.getWorldPosition(this.camPos);

    // Skip the repack (and the GPU re-upload it triggers) when the camera
    // barely moved — LOD buckets can't have changed enough to matter.
    if (this.hasRepacked) {
      const dx = this.camPos.x - this.lastCamX;
      const dy = this.camPos.y - this.lastCamY;
      const dz = this.camPos.z - this.lastCamZ;
      if (dx * dx + dy * dy + dz * dz < REPACK_CAM_MOVE_SQ) return;
    }
    this.lastCamX = this.camPos.x;
    this.lastCamY = this.camPos.y;
    this.lastCamZ = this.camPos.z;
    this.hasRepacked = true;

    for (const [, cur] of this.cursors) cur.fill(0);

    const cx = this.camPos.x;
    const cy = this.camPos.y;
    const cz = this.camPos.z;
    const numPairs = this.numPairs;

    for (let i = 0; i < this.instanceCount; i++) {
      const px = this.instancePos[i * 3];
      const py = this.instancePos[i * 3 + 1];
      const pz = this.instancePos[i * 3 + 2];
      const ddx = px - cx;
      const ddy = py - cy;
      const ddz = pz - cz;
      const d2 = ddx * ddx + ddy * ddy + ddz * ddz;

      let lodKey: string = LOD_TIER_KEYS[vegetationLodTier(d2)];
      let targetMeshes = this.lodMeshes.get(lodKey);
      if (!targetMeshes) {
        lodKey = 'lod0';
        targetMeshes = lod0Meshes;
      }

      const meshIndex = i % numPairs;
      const mesh = targetMeshes[meshIndex];
      if (!mesh) continue;

      const cur = this.cursors.get(lodKey)!;
      const slot = cur[meshIndex]++;
      const dst = mesh.instanceMatrix.array;
      const srcBase = i * 16;
      const dstBase = slot * 16;
      for (let j = 0; j < 16; j++) {
        dst[dstBase + j] = this.srcMatrix[srcBase + j];
      }
    }

    for (const [lodKey, meshes] of this.lodMeshes) {
      const cur = this.cursors.get(lodKey)!;
      for (let mi = 0; mi < meshes.length; mi++) {
        meshes[mi].count = cur[mi];
        meshes[mi].instanceMatrix.needsUpdate = true;
      }
    }
  }

  /** Build one world-space sphere covering every instance and assign it to all
   * LOD meshes. Called once; instances never move so it stays valid. */
  private assignStaticBoundingSphere(): void {
    if (this.instanceCount === 0) return;

    const center = new THREE.Vector3()
      .addVectors(this.boundsMin, this.boundsMax)
      .multiplyScalar(0.5);
    const halfDiagonal = this.boundsMax.distanceTo(this.boundsMin) * 0.5;
    const radius = halfDiagonal + this.maxInstanceRadius;

    for (const [, meshes] of this.lodMeshes) {
      for (const mesh of meshes) {
        mesh.boundingSphere = new THREE.Sphere(center.clone(), radius);
      }
    }
  }

  private getCamera(state: State): THREE.Camera | null {
    const ctx = getRenderingContext(state);
    if (!ctx.renderer) return null;
    for (const [, cam] of threeCameras) {
      if (cam.isCamera) return cam;
    }
    return null;
  }

  isReady(): boolean {
    return this.state.ready;
  }

  getLoadedCount(): number {
    return this.instanceCount;
  }

  dispose(): void {
    for (const [, meshes] of this.lodMeshes) {
      for (const mesh of meshes) {
        mesh.geometry.dispose();
        if (Array.isArray(mesh.material)) {
          mesh.material.forEach((m) => m.dispose());
        } else {
          mesh.material.dispose();
        }
        this.state.scene?.remove(mesh);
      }
    }
    this.lodMeshes.clear();
    this.lodPairs.clear();
    this.cursors.clear();
    this.instanceCount = 0;
    this.state.ready = false;
    this.state.compiled = false;
  }
}
