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
  /** Cascaded shadow maps (three/addons/csm/CSM.js) instead of the single
   * tight frustum that follows the player — covers near AND far casters at
   * once, at the cost of `csmCascades` extra shadow passes. Opt-in: when 1,
   * this entity's light is fully owned by a CSM instance (its own internal
   * directional lights), the plain-light sync path below is skipped. */
  csm: new Uint8Array(MAX_ENTITIES),
  csmCascades: new Uint8Array(MAX_ENTITIES),
  csmMaxFar: new Float32Array(MAX_ENTITIES),
  /** Percentage-Closer Soft Shadows for this directional light (variable
   * penumbra via blocker search). Opt-in per-light; only affects directional
   * shadow sampling, point/spot lights keep stock PCF. Applied globally to
   * the shader chunk when the first opted-in light is created. */
  pcss: new Uint8Array(MAX_ENTITIES),
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

export const DistanceCull = {
  maxDistance: new Float32Array(MAX_ENTITIES),
  culled: new Uint8Array(MAX_ENTITIES),
} as const;
