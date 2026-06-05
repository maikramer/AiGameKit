import type { State } from '../../core';
import * as THREE from 'three';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import { MainCamera } from './components';

const INITIAL_INSTANCES = 1000;
const MAX_TOTAL_INSTANCES = 50000;
const PERFORMANCE_WARNING_THRESHOLD = 10000;
const DEFAULT_COLOR = 0xffffff;

export const RendererShape = {
  BOX: 0,
  SPHERE: 1,
} as const;

export const CameraProjection = {
  PERSPECTIVE: 0,
  ORTHOGRAPHIC: 1,
} as const;

export const threeCameras = new Map<number, THREE.Camera>();
const canvasElements = new Map<number, HTMLCanvasElement>();

function getCanvasAspect(state: State): {
  width: number;
  height: number;
  aspect: number;
} {
  const context = stateToRenderingContext.get(state);
  const canvas = context?.canvas;

  let width = 16;
  let height = 9;

  if (canvas && canvas.clientWidth && canvas.clientHeight) {
    width = canvas.clientWidth;
    height = canvas.clientHeight;
  } else if (typeof window !== 'undefined') {
    width = window.innerWidth;
    height = window.innerHeight;
  }

  return { width, height, aspect: width / height };
}

function createThreeCamera(
  entity: number,
  state: State,
  projection: number,
  fov: number,
  orthoSize: number
): THREE.Camera {
  const { aspect } = getCanvasAspect(state);

  let camera: THREE.Camera;

  if (projection === CameraProjection.ORTHOGRAPHIC) {
    const halfHeight = orthoSize / 2;
    const halfWidth = halfHeight * aspect;
    camera = new THREE.OrthographicCamera(
      -halfWidth,
      halfWidth,
      halfHeight,
      -halfHeight,
      0.1,
      1000
    );
  } else {
    camera = new THREE.PerspectiveCamera(fov, aspect, 0.1, 1000);
  }

  threeCameras.set(entity, camera);
  return camera;
}

function syncCameraSettings(
  camera: THREE.Camera,
  entity: number,
  state: State
): void {
  const { aspect } = getCanvasAspect(state);

  if (camera instanceof THREE.OrthographicCamera) {
    const orthoSize = MainCamera.orthoSize[entity];
    const halfHeight = orthoSize / 2;
    const halfWidth = halfHeight * aspect;

    if (camera.top !== halfHeight || camera.right !== halfWidth) {
      camera.left = -halfWidth;
      camera.right = halfWidth;
      camera.top = halfHeight;
      camera.bottom = -halfHeight;
      camera.updateProjectionMatrix();
    }
  } else if (camera instanceof THREE.PerspectiveCamera) {
    const fov = MainCamera.fov[entity];
    if (camera.fov !== fov) {
      camera.fov = fov;
      camera.updateProjectionMatrix();
    }
  }
}

export { createThreeCamera, getCanvasAspect, syncCameraSettings };

const instanceFreeLists = new WeakMap<THREE.InstancedMesh, number[]>();

export function releaseInstanceSlot(
  mesh: THREE.InstancedMesh,
  index: number
): void {
  const freeList = instanceFreeLists.get(mesh);
  if (freeList) {
    freeList.push(index);
  }
}

export function findAvailableInstanceSlot(
  mesh: THREE.InstancedMesh,
  _matrix: THREE.Matrix4
): number | null {
  const freeList = instanceFreeLists.get(mesh);
  if (freeList && freeList.length > 0) {
    return freeList.pop()!;
  }
  return null;
}

export function initializeInstancedMesh(
  geometry: THREE.BufferGeometry,
  material: THREE.Material,
  count: number = INITIAL_INSTANCES
): THREE.InstancedMesh {
  const mesh = new THREE.InstancedMesh(geometry, material, count);
  mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  mesh.frustumCulled = true;

  const zeroMatrix = new THREE.Matrix4();
  zeroMatrix.makeScale(0, 0, 0);
  const defaultColor = new THREE.Color(DEFAULT_COLOR);

  for (let i = 0; i < count; i++) {
    mesh.setMatrixAt(i, zeroMatrix);
    mesh.setColorAt(i, defaultColor);
  }

  mesh.instanceMatrix.needsUpdate = true;
  if (mesh.instanceColor) {
    mesh.instanceColor.needsUpdate = true;
  }

  const freeList: number[] = [];
  for (let i = count - 1; i >= 0; i--) {
    freeList.push(i);
  }
  instanceFreeLists.set(mesh, freeList);

  return mesh;
}

export function resizeInstancedMesh(
  oldMesh: THREE.InstancedMesh,
  geometry: THREE.BufferGeometry,
  material: THREE.Material,
  scene: THREE.Scene
): THREE.InstancedMesh {
  const oldCount = oldMesh.count;
  const newCount = oldCount * 2;

  const newMesh = initializeInstancedMesh(geometry, material, newCount);

  const matrix = new THREE.Matrix4();
  const color = new THREE.Color();

  for (let i = 0; i < oldCount; i++) {
    oldMesh.getMatrixAt(i, matrix);
    newMesh.setMatrixAt(i, matrix);

    if (oldMesh.instanceColor) {
      oldMesh.getColorAt(i, color);
      newMesh.setColorAt(i, color);
    }
  }

  newMesh.instanceMatrix.needsUpdate = true;
  if (newMesh.instanceColor) {
    newMesh.instanceColor.needsUpdate = true;
  }

  const freeList = instanceFreeLists.get(newMesh);
  if (freeList) {
    freeList.length = 0;
    for (let i = newCount - 1; i >= oldCount; i--) {
      freeList.push(i);
    }
  }

  scene.remove(oldMesh);
  oldMesh.dispose();
  scene.add(newMesh);

  return newMesh;
}

/**
 * Per-entity instance slot plus a cache of the transform inputs and color last
 * written to the GPU buffer. The render loop compares against this cache so it
 * only rewrites `setMatrixAt`/`setColorAt` and flags `needsUpdate` when an
 * instance actually changed — static instances (terrain props, vegetation)
 * then cost zero GPU buffer uploads per frame instead of a full re-upload.
 */
export interface InstanceInfo {
  poolId: number;
  instanceId: number;
  unlit: boolean;
  /** False until the slot has been written once (or after it was hidden). */
  initialized: boolean;
  /** Last composed transform inputs (position, rotation quat, final scale). */
  px: number;
  py: number;
  pz: number;
  rx: number;
  ry: number;
  rz: number;
  rw: number;
  sx: number;
  sy: number;
  sz: number;
  /** Last color written to the instance color buffer (-1 = never written). */
  color: number;
}

export interface RenderingContext {
  scene: THREE.Scene;
  meshPools: Map<number, THREE.InstancedMesh>;
  unlitMeshPools: Map<number, THREE.InstancedMesh>;
  geometries: Map<number, THREE.BufferGeometry>;
  material: THREE.MeshStandardMaterial;
  unlitMaterial: THREE.MeshBasicMaterial;
  entityInstances: Map<number, InstanceInfo>;
  lights: {
    ambient: THREE.HemisphereLight;
    directional: THREE.DirectionalLight;
    pointLights: THREE.PointLight[];
    spotLights: THREE.SpotLight[];
  };
  renderer?: THREE.WebGLRenderer;
  postProcessing?: any | null;
  canvas?: HTMLCanvasElement;
  totalInstanceCount: number;
  hasShownPerformanceWarning: boolean;
}

const stateToRenderingContext = new WeakMap<State, RenderingContext>();

export function createGeometries(): Map<number, THREE.BufferGeometry> {
  const geometries = new Map<number, THREE.BufferGeometry>();
  geometries.set(RendererShape.BOX, new THREE.BoxGeometry());
  geometries.set(RendererShape.SPHERE, new THREE.SphereGeometry(1));
  return geometries;
}

export function initializeContext(): RenderingContext {
  const scene = new THREE.Scene();

  const ambient = new THREE.HemisphereLight(0xb1e1ff, 0xb97a20, 1.0);
  scene.add(ambient);

  const directional = new THREE.DirectionalLight(0xffffff, 1.8);
  directional.castShadow = true;
  directional.shadow.mapSize.width = 2048;
  directional.shadow.mapSize.height = 2048;
  scene.add(directional);
  scene.add(directional.target);

  return {
    scene,
    meshPools: new Map(),
    unlitMeshPools: new Map(),
    geometries: createGeometries(),
    material: new THREE.MeshStandardMaterial({
      metalness: 0.0,
      roughness: 1.0,
    }),
    unlitMaterial: new THREE.MeshBasicMaterial(),
    entityInstances: new Map(),
    lights: {
      ambient: ambient,
      directional: directional,
      pointLights: [],
      spotLights: [],
    },
    totalInstanceCount: 0,
    hasShownPerformanceWarning: false,
  };
}

export function getRenderingContext(state: State): RenderingContext {
  let context = stateToRenderingContext.get(state);
  if (!context) {
    context = initializeContext();
    stateToRenderingContext.set(state, context);
  }
  return context;
}

export function getScene(state: State): THREE.Scene | null {
  const context = stateToRenderingContext.get(state);
  return context?.scene || null;
}

export function setCanvasElement(
  entity: number,
  canvas: HTMLCanvasElement
): void {
  canvasElements.set(entity, canvas);
}

export function getCanvasElement(
  entity: number
): HTMLCanvasElement | undefined {
  return canvasElements.get(entity);
}

export function deleteCanvasElement(entity: number): void {
  canvasElements.delete(entity);
}

export function setRenderingCanvas(
  state: State,
  canvas: HTMLCanvasElement
): void {
  const context = getRenderingContext(state);
  context.canvas = canvas;
}

export async function createRenderer(
  canvas: HTMLCanvasElement,
  clearColor: number
): Promise<THREE.WebGLRenderer> {
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    powerPreference: 'high-performance',
  });

  const width = canvas.clientWidth || window.innerWidth;
  const height = canvas.clientHeight || window.innerHeight;
  const pixelRatio = Math.min(
    window.devicePixelRatio,
    /Mobi|Android/i.test(navigator.userAgent) ? 1.25 : 1.5
  );
  renderer.setPixelRatio(pixelRatio);
  renderer.setSize(width, height, false);

  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  if (clearColor !== 0) {
    renderer.setClearColor(clearColor);
  }

  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.AgXToneMapping;
  renderer.toneMappingExposure = 1;

  return renderer;
}

/**
 * Give the scene an image-based lighting environment. Without it, glTF PBR
 * materials with non-zero metalness (very common in exported characters) have
 * nothing to reflect and render black. A prefiltered neutral room is the
 * standard fix and lights every metallic/rough surface plausibly.
 */
export function applyNeutralEnvironment(
  renderer: THREE.WebGLRenderer,
  scene: THREE.Scene
): void {
  const pmrem = new THREE.PMREMGenerator(renderer);
  try {
    scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  } finally {
    pmrem.dispose();
  }
}

export function handleWindowResize(
  state: State,
  renderer: THREE.WebGLRenderer
): void {
  const context = getRenderingContext(state);
  const canvas = context.canvas;

  const width = canvas?.clientWidth || window.innerWidth;
  const height = canvas?.clientHeight || window.innerHeight;
  const aspect = width / height;

  renderer.setPixelRatio(
    Math.min(
      window.devicePixelRatio,
      /Mobi|Android/i.test(navigator.userAgent) ? 1.25 : 1.5
    )
  );
  renderer.setSize(width, height, false);

  if (context.postProcessing && typeof context.postProcessing.setSize === 'function') {
    context.postProcessing.setSize(width, height);
  }

  for (const [, camera] of threeCameras) {
    if (camera instanceof THREE.PerspectiveCamera) {
      camera.aspect = aspect;
      camera.updateProjectionMatrix();
    } else if (camera instanceof THREE.OrthographicCamera) {
      const halfHeight = (camera.top - camera.bottom) / 2;
      const halfWidth = halfHeight * aspect;
      camera.left = -halfWidth;
      camera.right = halfWidth;
      camera.updateProjectionMatrix();
    }
  }
}

export const SHADOW_CONFIG = {
  LIGHT_DIRECTION: new THREE.Vector3(5, 10, 2).normalize(),
  LIGHT_DISTANCE: 25,
  /** Raio ortográfico da câmara de sombras (metade da largura do frustum). Maior = mais cobertura ao redor do alvo. */
  CAMERA_RADIUS: 140,
  NEAR_PLANE: 0.5,
  FAR_PLANE: 250,
  /**
   * Centro da frustum ortográfica do shadow map em espaço de mundo (Y≈altura média do chão).
   * Centrar na câmara/jogador faz o limite do mapa “seguir” o ecrã (parece um quadrado que anda);
   * âncora fixa cobre o mapa centrado na origem (ex.: terrain pos 0,0,0).
   */
  FIXED_FRUSTUM_CENTER: new THREE.Vector3(0, 0, 0),
} as const;

export { MAX_TOTAL_INSTANCES, PERFORMANCE_WARNING_THRESHOLD };
