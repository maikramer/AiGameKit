import {
  defineComponent,
  F32,
  U32,
  U8,
} from '../../core/ecs/component-storage';

export const MeshRenderer = defineComponent({
  shape: U8,
  sizeX: F32,
  sizeY: F32,
  sizeZ: F32,
  color: U32,
  visible: U8,
  unlit: U8,
});

export const RenderContext = defineComponent({
  clearColor: U32,
  hasCanvas: U8,
});

export const MainCamera = defineComponent({
  projection: U8,
  fov: F32,
  orthoSize: F32,
  /** Perspective/ortho near clip (world units). */
  near: F32,
  /** Perspective/ortho far clip (world units). Shorter = less distant draw. */
  far: F32,
});

export const AmbientLight = defineComponent({
  skyColor: U32,
  groundColor: U32,
  intensity: F32,
});

export const DirectionalLight = defineComponent({
  color: U32,
  intensity: F32,
  castShadow: U8,
  shadowMapSize: U32,
  directionX: F32,
  directionY: F32,
  directionZ: F32,
  distance: F32,
  /** Cascaded shadow maps (three/addons/csm/CSM.js) instead of the single
   * tight frustum that follows the player — covers near AND far casters at
   * once, at the cost of `csmCascades` extra shadow passes. Opt-in: when 1,
   * this entity's light is fully owned by a CSM instance (its own internal
   * directional lights), the plain-light sync path below is skipped. */
  csm: U8,
  csmCascades: U8,
  csmMaxFar: F32,
  /** Percentage-Closer Soft Shadows for this directional light (variable
   * penumbra via blocker search). Opt-in per-light; only affects directional
   * shadow sampling, point/spot lights keep stock PCF. Applied globally to
   * the shader chunk when the first opted-in light is created. */
  pcss: U8,
  /** Angular size (`radius / distance`) under which a caster stops casting
   * into this light's shadow map — a shadow too small for the map to resolve
   * costs a full extra draw for a couple of blurred texels. 0 disables the
   * cull; objects closer than `MIN_CULL_DISTANCE` always cast. */
  shadowCullRatio: F32,
});

export const PointLight = defineComponent({
  color: U32,
  intensity: F32,
  distance: F32,
  decay: F32,
  castShadow: U8,
});

export const SpotLight = defineComponent({
  color: U32,
  intensity: F32,
  distance: F32,
  decay: F32,
  angle: F32,
  penumbra: F32,
  castShadow: U8,
});

export const DistanceCull = defineComponent({
  maxDistance: F32,
  culled: U8,
});
