import { MAX_ENTITIES } from '../../core/ecs/constants';

export const MeshRenderer = {
  shape: new Uint8Array(MAX_ENTITIES),
  sizeX: new Float32Array(MAX_ENTITIES),
  sizeY: new Float32Array(MAX_ENTITIES),
  sizeZ: new Float32Array(MAX_ENTITIES),
  color: new Uint32Array(MAX_ENTITIES),
  visible: new Uint8Array(MAX_ENTITIES),
  unlit: new Uint8Array(MAX_ENTITIES),
} as const;

export const RenderContext = {
  clearColor: new Uint32Array(MAX_ENTITIES),
  hasCanvas: new Uint8Array(MAX_ENTITIES),
} as const;

// Post-processing pipeline (WebGL2-compatible effects).
export const Postprocessing = {
  enabled: new Uint8Array(MAX_ENTITIES),
  bloom: new Uint8Array(MAX_ENTITIES),
  bloomStrength: new Float32Array(MAX_ENTITIES),
  bloomRadius: new Float32Array(MAX_ENTITIES),
  bloomThreshold: new Float32Array(MAX_ENTITIES),
  chromaticAberration: new Uint8Array(MAX_ENTITIES),
  caStrength: new Float32Array(MAX_ENTITIES),
  vignette: new Uint8Array(MAX_ENTITIES),
  vignetteStrength: new Float32Array(MAX_ENTITIES),
  vignetteRadius: new Float32Array(MAX_ENTITIES),
  aa: new Uint8Array(MAX_ENTITIES), // 0=off, 1=FXAA, 2=SMAA
} as const;

export const MainCamera = {
  projection: new Uint8Array(MAX_ENTITIES),
  fov: new Float32Array(MAX_ENTITIES),
  orthoSize: new Float32Array(MAX_ENTITIES),
} as const;

export const AmbientLight = {
  skyColor: new Uint32Array(MAX_ENTITIES),
  groundColor: new Uint32Array(MAX_ENTITIES),
  intensity: new Float32Array(MAX_ENTITIES),
} as const;

export const DirectionalLight = {
  color: new Uint32Array(MAX_ENTITIES),
  intensity: new Float32Array(MAX_ENTITIES),
  castShadow: new Uint8Array(MAX_ENTITIES),
  shadowMapSize: new Uint32Array(MAX_ENTITIES),
  directionX: new Float32Array(MAX_ENTITIES),
  directionY: new Float32Array(MAX_ENTITIES),
  directionZ: new Float32Array(MAX_ENTITIES),
  distance: new Float32Array(MAX_ENTITIES),
} as const;

export const PointLight = {
  color: new Uint32Array(MAX_ENTITIES),
  intensity: new Float32Array(MAX_ENTITIES),
  distance: new Float32Array(MAX_ENTITIES),
  decay: new Float32Array(MAX_ENTITIES),
  castShadow: new Uint8Array(MAX_ENTITIES),
} as const;

export const SpotLight = {
  color: new Uint32Array(MAX_ENTITIES),
  intensity: new Float32Array(MAX_ENTITIES),
  distance: new Float32Array(MAX_ENTITIES),
  decay: new Float32Array(MAX_ENTITIES),
  angle: new Float32Array(MAX_ENTITIES),
  penumbra: new Float32Array(MAX_ENTITIES),
  castShadow: new Uint8Array(MAX_ENTITIES),
} as const;

export const CsmConfig = {
  cascades: new Uint8Array(MAX_ENTITIES),
  maxFar: new Float32Array(MAX_ENTITIES),
  shadowMapSize: new Uint16Array(MAX_ENTITIES),
  enabled: new Uint8Array(MAX_ENTITIES),
} as const;

export const DistanceCull = {
  maxDistance: new Float32Array(MAX_ENTITIES),
  culled: new Uint8Array(MAX_ENTITIES),
} as const;
