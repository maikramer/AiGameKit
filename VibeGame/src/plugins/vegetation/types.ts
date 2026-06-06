import type { InstancedMesh, Scene, Vector3 } from 'three';

/** Spawn config entry for one vegetation type. */
export interface VegetationInstanceSpec {
  url: string;
  lod1Url?: string;
  lod2Url?: string;
  role: string;
  profile?: string;
}

/** One InstancedMesh group within a spatial cell. */
export interface VegetationCluster {
  instancedMeshes: Map<string, InstancedMesh>;
  positions: Array<{ x: number; y: number; z: number }>;
  scales: Float32Array;
  yaws: Float32Array;
  clusterCenter: Vector3;
  boundingRadius: number;
}

/** Internal state per SpawnGroup that uses instancing. */
export interface VegetationSpawnState {
  spec: VegetationInstanceSpec;
  clusters: VegetationCluster[];
  ready: boolean;
  maxInstances: number;
  scene: Scene | null;
  compiled: boolean;
}
