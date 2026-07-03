import * as THREE from 'three';

export const CLOUD_COUNT = 42;
export const CLOUD_RING_MIN = 70;
export const CLOUD_RING_MAX = 280;

/** Soft round cloud sprite drawn on a canvas (no asset dependency). */
function makeCloudTexture(): THREE.Texture {
  const size = 128;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d')!;
  // Three overlapping radial blobs → puffy silhouette instead of a disc.
  const blobs: Array<[number, number, number]> = [
    [0.5, 0.55, 0.42],
    [0.32, 0.62, 0.3],
    [0.68, 0.6, 0.32],
  ];
  for (const [cx, cy, r] of blobs) {
    const g = ctx.createRadialGradient(
      cx * size,
      cy * size,
      0,
      cx * size,
      cy * size,
      r * size
    );
    g.addColorStop(0, 'rgba(255,255,255,0.85)');
    g.addColorStop(0.6, 'rgba(255,255,255,0.35)');
    g.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, size, size);
  }
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

export interface CloudField {
  mesh: THREE.InstancedMesh;
  material: THREE.MeshBasicMaterial;
  /** Per-instance world offsets relative to the camera anchor. */
  offsets: Float32Array; // x, z per instance
  scales: Float32Array;
  heights: Float32Array;
}

export function createCloudField(): CloudField {
  const geo = new THREE.PlaneGeometry(1, 0.55);
  const material = new THREE.MeshBasicMaterial({
    map: makeCloudTexture(),
    transparent: true,
    opacity: 0,
    depthWrite: false,
    fog: false,
  });
  const mesh = new THREE.InstancedMesh(geo, material, CLOUD_COUNT);
  mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  mesh.frustumCulled = false;
  mesh.renderOrder = 1;

  const offsets = new Float32Array(CLOUD_COUNT * 2);
  const scales = new Float32Array(CLOUD_COUNT);
  const heights = new Float32Array(CLOUD_COUNT);
  for (let i = 0; i < CLOUD_COUNT; i++) {
    const r =
      CLOUD_RING_MIN + Math.random() * (CLOUD_RING_MAX - CLOUD_RING_MIN);
    const a = Math.random() * Math.PI * 2;
    offsets[i * 2] = Math.cos(a) * r;
    offsets[i * 2 + 1] = Math.sin(a) * r;
    scales[i] = 26 + Math.random() * 34;
    heights[i] = (Math.random() - 0.5) * 24;
  }
  return { mesh, material, offsets, scales, heights };
}

const _m = new THREE.Matrix4();
const _q = new THREE.Quaternion();
const _s = new THREE.Vector3();
const _p = new THREE.Vector3();

/**
 * Drift the field by the wind and re-anchor it around the camera (offsets
 * wrap on a ±ringMax box, so clouds recycle behind the player).
 */
export function updateCloudField(
  field: CloudField,
  camX: number,
  camZ: number,
  cloudY: number,
  windX: number,
  windZ: number,
  dt: number,
  cameraQuaternion: THREE.Quaternion
): void {
  const wrap = CLOUD_RING_MAX;
  for (let i = 0; i < CLOUD_COUNT; i++) {
    let ox = field.offsets[i * 2]! + windX * dt;
    let oz = field.offsets[i * 2 + 1]! + windZ * dt;
    if (ox > wrap) ox -= wrap * 2;
    else if (ox < -wrap) ox += wrap * 2;
    if (oz > wrap) oz -= wrap * 2;
    else if (oz < -wrap) oz += wrap * 2;
    field.offsets[i * 2] = ox;
    field.offsets[i * 2 + 1] = oz;

    _p.set(camX + ox, cloudY + field.heights[i]!, camZ + oz);
    _q.copy(cameraQuaternion);
    _s.setScalar(field.scales[i]!);
    _m.compose(_p, _q, _s);
    field.mesh.setMatrixAt(i, _m);
  }
  field.mesh.instanceMatrix.needsUpdate = true;
}
