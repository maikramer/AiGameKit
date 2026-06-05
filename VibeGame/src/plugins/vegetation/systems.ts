import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { createGLTFLoader } from "../../extras/gltf-bridge";
import { getRenderingContext, getScene, threeCameras } from "../rendering";
import type { State } from "../../core";
import type { VegetationInstanceSpec, VegetationSpawnState } from "./types";

const MAX_INSTANCES = 500;
const LOD1_DISTANCE = 80;
const LOD2_DISTANCE = 200;
const DUMMY = new THREE.Object3D();

interface GeometryMaterialPair {
  geometry: THREE.BufferGeometry;
  material: THREE.Material;
}

function extractGeometryMaterialPairs(scene: THREE.Object3D): GeometryMaterialPair[] {
  const pairs: GeometryMaterialPair[] = [];
  scene.traverse((child) => {
    if ((child as THREE.Mesh).isMesh) {
      const mesh = child as THREE.Mesh;
      pairs.push({ geometry: mesh.geometry, material: mesh.material as THREE.Material });
    }
  });
  return pairs;
}

function toWebGPUMaterial(mat: THREE.Material): THREE.Material {
  if (mat instanceof THREE.MeshStandardMaterial) {
    const nodeMat = new THREE.MeshStandardNodeMaterial();
    nodeMat.map = mat.map;
    nodeMat.normalMap = mat.normalMap;
    nodeMat.roughnessMap = mat.roughnessMap;
    nodeMat.metalnessMap = mat.metalnessMap;
    nodeMat.aoMap = mat.aoMap;
    nodeMat.roughness = mat.roughness;
    nodeMat.metalness = mat.metalness;
    nodeMat.side = mat.side;
    nodeMat.transparent = mat.transparent;
    nodeMat.alphaTest = mat.alphaTest;
    nodeMat.color.copy(mat.color);
    return nodeMat;
  }
  return mat;
}

export class VegetationInstancer {
  private state: VegetationSpawnState;
  private lodPairs = new Map<string, GeometryMaterialPair[]>();
  private gltfLoader: GLTFLoader;
  private lodMeshes = new Map<string, THREE.InstancedMesh[]>();
  private instanceCount = 0;

  constructor() {
    this.state = {
      spec: { url: "", role: "static" },
      clusters: [],
      ready: false,
      maxInstances: MAX_INSTANCES,
      scene: null,
      compiled: false,
    };
    this.gltfLoader = createGLTFLoader();
  }

  async initializeFromSpec(spec: VegetationInstanceSpec, state: State): Promise<void> {
    this.state.spec = spec;
    this.state.scene = getScene(state) ?? null;

    try {
      await this.loadGlbs(spec.url, spec.lod1Url, spec.lod2Url);
      this.buildInstancedMeshes();
    } catch (err) {
      console.warn("[vegetation] Failed to load GLBs:", err);
      this.state.ready = false;
      return;
    }
  }

  private async loadGlbs(url: string, lod1Url?: string, lod2Url?: string): Promise<void> {
    const lodUrls = new Map<string, string>();
    lodUrls.set("lod0", url);
    if (lod1Url) lodUrls.set("lod1", lod1Url);
    if (lod2Url) lodUrls.set("lod2", lod2Url);

    const loadPromises = Array.from(lodUrls.entries()).map(async ([lodKey, lodUrl]) => {
      const gltf = await this.gltfLoader.loadAsync(lodUrl);
      this.lodPairs.set(lodKey, extractGeometryMaterialPairs(gltf.scene));
    });

    await Promise.all(loadPromises);
  }

  private buildInstancedMeshes(): void {
    if (!this.state.scene) return;

    for (const [lodKey, pairs] of this.lodPairs) {
      const meshes: THREE.InstancedMesh[] = [];
      for (const { geometry, material } of pairs) {
        const webgpuMat = toWebGPUMaterial(material);
        const im = new THREE.InstancedMesh(geometry, webgpuMat, MAX_INSTANCES);
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
    }
  }

  async markReady(state: State): Promise<void> {
    if (!this.state.scene) {
      this.state.scene = getScene(state) ?? null;
    }
    if (!this.state.scene) return;

    const ctx = getRenderingContext(state);
    const cam = ctx.renderer ? this.getCamera(state) : null;
    if (cam && ctx.renderer) {
      try {
        await ctx.renderer.compileAsync(this.state.scene, cam);
      } catch {
        // Non-fatal: compile may fail in some WebGPU implementations
      }
    }
    this.state.compiled = true;
    this.state.ready = true;
    this.updateAllBoundingSpheres();
  }

  addInstance(x: number, y: number, z: number, scale: number, yaw: number): void {
    if (this.instanceCount >= MAX_INSTANCES) return;

    const lod0Meshes = this.lodMeshes.get("lod0");
    if (!lod0Meshes || lod0Meshes.length === 0) return;

    const meshIndex = this.instanceCount % lod0Meshes.length;
    const im = lod0Meshes[meshIndex];

    DUMMY.position.set(x, y, z);
    DUMMY.scale.setScalar(scale);
    DUMMY.rotation.set(0, yaw, 0);
    DUMMY.updateMatrix();
    im.setMatrixAt(this.instanceCount, DUMMY.matrix);

    for (const [lodKey, meshes] of this.lodMeshes) {
      if (lodKey === "lod0") continue;
      const mesh = meshes[meshIndex];
      if (mesh) {
        mesh.setMatrixAt(this.instanceCount, DUMMY.matrix);
        mesh.instanceMatrix.needsUpdate = true;
      }
    }

    this.instanceCount++;
    im.count = Math.max(im.count, this.instanceCount);
    im.instanceMatrix.needsUpdate = true;
  }

  update(state: State): void {
    if (!this.state.ready) return;

    const cam = this.getCamera(state);
    if (!cam) return;

    const camPos = new THREE.Vector3();
    cam.getWorldPosition(camPos);

    const counts = new Map<string, number[]>();
    for (const [lodKey, meshes] of this.lodMeshes) {
      const lodCounts = new Array<number>(meshes.length).fill(0);
      counts.set(lodKey, lodCounts);
      for (const mesh of meshes) {
        mesh.count = 0;
      }
    }

    const lod0Meshes = this.lodMeshes.get("lod0");
    if (!lod0Meshes || lod0Meshes.length === 0) return;

    for (let i = 0; i < this.instanceCount; i++) {
      const meshIndex = i % lod0Meshes.length;
      const im = lod0Meshes[meshIndex];
      if (!im) continue;

      DUMMY.matrix.fromArray(im.instanceMatrix.array, i * 16);
      DUMMY.position.setFromMatrixPosition(DUMMY.matrix);

      const dist = camPos.distanceTo(DUMMY.position);

      let targetLod: string;
      if (dist < LOD1_DISTANCE) {
        targetLod = "lod0";
      } else if (dist < LOD2_DISTANCE) {
        targetLod = "lod1";
      } else {
        targetLod = "lod2";
      }

      const targetMeshes = this.lodMeshes.get(targetLod) ?? lod0Meshes;
      const targetMesh = targetMeshes[meshIndex];
      if (!targetMesh) continue;

      const lodCounts = counts.get(targetLod)!;
      const slot = lodCounts[meshIndex];
      if (targetLod !== "lod0") {
        const srcMatrix = lod0Meshes[meshIndex]!.instanceMatrix.array;
        const dstArray = targetMesh.instanceMatrix.array;
        for (let j = 0; j < 16; j++) {
          dstArray[slot * 16 + j] = srcMatrix[i * 16 + j];
        }
      } else {
        if (slot !== i) {
          const srcArray = lod0Meshes[meshIndex]!.instanceMatrix.array;
          for (let j = 0; j < 16; j++) {
            targetMesh.instanceMatrix.array[slot * 16 + j] = srcArray[i * 16 + j];
          }
        }
      }

      lodCounts[meshIndex] = slot + 1;
    }

    for (const [lodKey, meshes] of this.lodMeshes) {
      const lodCounts = counts.get(lodKey)!;
      for (let mi = 0; mi < meshes.length; mi++) {
        meshes[mi].count = lodCounts[mi];
        meshes[mi].instanceMatrix.needsUpdate = true;
      }
    }

    this.updateAllBoundingSpheres();
  }

  private updateAllBoundingSpheres(): void {
    for (const [, meshes] of this.lodMeshes) {
      for (const mesh of meshes) {
        mesh.computeBoundingSphere();
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
    this.instanceCount = 0;
    this.state.ready = false;
    this.state.compiled = false;
  }
}
