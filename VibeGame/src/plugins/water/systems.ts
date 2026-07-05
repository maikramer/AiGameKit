import * as THREE from 'three';
import CustomShaderMaterial from 'three-custom-shader-material/vanilla';
import { defineQuery } from '../../core';
import type { State, System } from '../../core';
import { Transform } from '../transforms/components';
import { getTerrainContext } from '../terrain/utils';
import { TerrainPadApplySystem } from '../terrain/pad-systems';
import { Lake, River, getRiverPath } from './components';
import { shapeRadius } from './carve';
import { LakeBowl } from './lake-bowl';
import { RiverChannel } from './river-channel';
import {
  applyWaterShape,
  type WaterMaterial,
  type WaterMaterialConfig,
  type WaterSideCar,
} from './water-shape';

const lakeQuery = defineQuery([Lake, Transform]);

/** 1×1 transparent placeholder for `uReflectionMap` until `WaterReflectionSystem`
 * (reflection.ts) assigns a real render target to the nearest lake. Exported
 * so that system can point a body's `uReflectionMap` back here the moment it
 * stops being the reflected body — leaving it pointed at the shared render
 * target would make WebGL flag a framebuffer/texture feedback loop as soon as
 * that (now-unhidden) mesh is rendered while the target is bound for writing. */
export const waterEmptyReflectionTexture = (() => {
  const t = new THREE.DataTexture(
    new Uint8Array([0, 0, 0, 0]),
    1,
    1,
    THREE.RGBAFormat
  );
  t.needsUpdate = true;
  return t;
})();

/**
 * Shared sidecar map for all water surfaces (lakes + rivers). `applyWaterShape`
 * writes here; `WaterAnimSystem` reads it to advance `uTime`. Lives here (not in
 * water-shape.ts) to avoid a circular import with the material builder.
 */
const WATER_SIDECARS = new WeakMap<State, Map<number, WaterSideCar>>();

export function waterSideCars(state: State): Map<number, WaterSideCar> {
  let m = WATER_SIDECARS.get(state);
  if (!m) {
    m = new Map();
    WATER_SIDECARS.set(state, m);
  }
  return m;
}

/** Shape-agnostic water material inputs. The depth fade and alpha falloff are
 *  driven by the geometry's `aWaterT` attribute (0 at centre/axis, 1 at margin),
 *  so the same material works for lakes and rivers. */

/**
 * Derive a lighter near-shore tint from a deep-water hex by lifting value a
 * touch toward warm — reads as shallow/suspended sediment at the shoreline.
 */
function shallowTint(hex: number): number {
  const r = (hex >> 16) & 0xff;
  const g = (hex >> 8) & 0xff;
  const b = hex & 0xff;
  const lift = (c: number): number => Math.round(c + (255 - c) * 0.4);
  return ((lift(r) << 16) | (lift(g) << 8) | lift(b)) >>> 0;
}

const WATER_VERTEX_SHADER = `
uniform float uTime;
uniform float uRipple;
uniform float uWaveHeight;
uniform float uWaveSpeed;
uniform mat4 uReflectionMatrix;
attribute float aWaterT;
attribute float aGroundDepth;
attribute float aFoamExtra;
varying float vWaterT;
varying float vGroundDepth;
varying float vFoamExtra;
varying vec2 vWaveXZ;
varying vec3 vViewDir;
varying vec4 vReflectUv;

void main() {
  vWaterT = aWaterT;
  vGroundDepth = aGroundDepth;
  vFoamExtra = aFoamExtra;

  vec3 pos = position;
  vec4 wPos = modelMatrix * vec4(pos, 1.0);
  vWaveXZ = wPos.xz;
  vReflectUv = uReflectionMatrix * wPos;
  float wt = uTime * uWaveSpeed;
  // Two crossing waves plus a slow diagonal swell — reads as gentle open-water
  // motion instead of a single repeating sine.
  float wave = sin(wPos.x * 1.3 + wt * 0.9) * 0.5 +
               cos(wPos.z * 1.6 + wt * 0.7) * 0.5 +
               sin((wPos.x + wPos.z) * 0.55 + wt * 1.4) * 0.35;
  // The disc lies in the local XZ plane (no mesh rotation), so local +Y is
  // world up. Amplitude is per-lake (wave-height XML attr or radius-scaled
  // auto), muted entirely when ripple is 0.
  pos.y += min(uRipple, 1.0) * wave * uWaveHeight;
  csm_Position = pos;

  vViewDir = normalize(cameraPosition - (modelMatrix * vec4(pos, 1.0)).xyz);
}
`;

const WATER_FRAGMENT_SHADER = `
uniform float uTime;
uniform float uRipple;
uniform float uWaveSpeed;
uniform vec3 uShallowColor;
uniform vec3 uDeepColor;
uniform vec3 uSkyTint;
uniform vec2 uDepthRamp;
uniform float uFresnelStrength;
uniform float uSparkleStrength;
uniform sampler2D uReflectionMap;
uniform float uHasReflection;
varying float vWaterT;
varying float vGroundDepth;
varying float vFoamExtra;
varying vec2 vWaveXZ;
varying vec3 vViewDir;
varying vec4 vReflectUv;

// Shape-agnostic depth/alpha: the geometry bakes t into aWaterT (0 at
// centre/axis, 1 at margin/bank) so these no longer depend on a radial
// distance metric. uDepthRamp compresses the shallow→deep gradient (rivers
// reach deep colour sooner than lakes).
float lakeDepthNorm() {
  return 1.0 - smoothstep(uDepthRamp.x, uDepthRamp.y, vWaterT);
}
float shoreAlpha() {
  return 1.0 - smoothstep(0.9, 1.0, vWaterT);
}
float lakeFresnel() {
  float cosTheta = abs(dot(normalize(vViewDir), vec3(0.0, 1.0, 0.0)));
  return pow(1.0 - cosTheta, 3.0);
}
float hash21(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}
float vnoise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  float a = hash21(i);
  float b = hash21(i + vec2(1.0, 0.0));
  float c = hash21(i + vec2(0.0, 1.0));
  float d = hash21(i + vec2(1.0, 1.0));
  return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}
// Cartoon shoreline foam: a wobbling band just inside the waterline plus a
// broken dashed line further in (wave wash), both toon-stepped.
float lakeFoam() {
  float t = vWaterT;
  float ft = uTime * uWaveSpeed;
  float wob = (vnoise(vWaveXZ * 0.8 + vec2(ft * 0.15, -ft * 0.12)) - 0.5) * 0.1;
  float band = smoothstep(0.86 + wob, 0.90 + wob, t) *
               (1.0 - smoothstep(0.97 + wob, 1.01 + wob, t));
  float dashT = t + wob;
  float dashBand = smoothstep(0.76, 0.78, dashT) *
                   (1.0 - smoothstep(0.82, 0.84, dashT));
  float dashes = step(0.5, vnoise(vWaveXZ * 2.0 + vec2(ft * 0.1, 0.0)));
  float foam = clamp(band + dashBand * dashes * 0.8, 0.0, 1.0);
  // Hard-ish step keeps the froth crisp and cel-shaded.
  return smoothstep(0.25, 0.55, foam);
}
// Contact foam: wherever the water sheet grazes the terrain (per-vertex
// clearance baked as aGroundDepth), froth it up so the waterline reads as
// churned water instead of a polygon edge. A noisy threshold keeps the
// fringe organic. vFoamExtra marks waterfall/rapids stations for a full
// white sheet.
float contactFoam() {
  float n = vnoise(vWaveXZ * 1.4 + vec2(uTime * uWaveSpeed * 0.2, 0.0));
  float edge = 0.22 + n * 0.3;
  float contact = 1.0 - smoothstep(0.03, edge, vGroundDepth);
  return max(contact, clamp(vFoamExtra, 0.0, 1.0));
}

void main() {
  float depthNorm = lakeDepthNorm();
  // Posterize the depth gradient into soft cel bands (cartoon read), blended
  // with the smooth ramp so band edges stay gentle.
  float banded = floor(depthNorm * 3.0 + 0.5) / 3.0;
  csm_DiffuseColor.rgb = mix(uShallowColor, uDeepColor, mix(depthNorm, banded, 0.55));
  float fres = lakeFresnel();
  // Real mirrored scene when a reflection camera has rendered this lake this
  // frame (nearest lake to the player only, see reflection.ts); otherwise
  // fall back to the flat sky tint exactly as before.
  vec3 reflected = uSkyTint;
  if (uHasReflection > 0.5) {
    reflected = texture2DProj(uReflectionMap, vReflectUv).rgb;
  }
  csm_DiffuseColor.rgb = mix(csm_DiffuseColor.rgb, reflected, clamp(fres * uFresnelStrength, 0.0, 0.5));
  // Low-frequency drift — high frequencies read as a checker pattern on the
  // flat disc instead of water.
  float st = uTime * uWaveSpeed;
  float shimmer = sin(vWaveXZ.x * 0.5 + st * 0.6) *
                  cos(vWaveXZ.y * 0.65 - st * 0.5) +
                  0.5 * sin((vWaveXZ.x + vWaveXZ.y) * 1.0 + st * 1.1);
  csm_DiffuseColor.rgb += shimmer * 0.011 * uRipple;
  // Sparse animated glints — stylized sun sparkle without real specular.
  float sp1 = vnoise(vWaveXZ * 0.9 + st * 0.35);
  float sp2 = vnoise(vWaveXZ * 1.7 - st * 0.27);
  float sparkle = smoothstep(0.82, 0.98, sp1 * sp2 * 1.6);
  csm_DiffuseColor.rgb += sparkle * uSparkleStrength * uRipple;
  float foam = max(lakeFoam(), contactFoam());
  csm_DiffuseColor.rgb = mix(csm_DiffuseColor.rgb, vec3(1.0), foam * 0.85);
  // Foam stays visible through the shoreline alpha fade-out.
  csm_DiffuseColor.a = max(csm_DiffuseColor.a * shoreAlpha(), foam * 0.9 * shoreAlpha());
}
`;

function makeWaterMaterial(cfg: WaterMaterialConfig): WaterMaterial {
  return new CustomShaderMaterial({
    baseMaterial: THREE.MeshStandardMaterial,
    color: 0xffffff,
    transparent: true,
    opacity: cfg.opacity,
    roughness: 0.08,
    metalness: 0,
    depthWrite: false,
    vertexShader: WATER_VERTEX_SHADER,
    fragmentShader: WATER_FRAGMENT_SHADER,
    uniforms: {
      uTime: { value: 0 },
      uRipple: { value: cfg.ripple },
      uWaveHeight: { value: cfg.waveHeight },
      uWaveSpeed: { value: cfg.waveSpeed },
      uShallowColor: { value: new THREE.Color(shallowTint(cfg.color)) },
      uDeepColor: { value: new THREE.Color(cfg.color) },
      uSkyTint: { value: new THREE.Color(0xbfd8e6) },
      uDepthRamp: {
        value: new THREE.Vector2(
          cfg.depthRampStart ?? 0,
          cfg.depthRampEnd ?? 1
        ),
      },
      uFresnelStrength: { value: cfg.fresnelStrength ?? 0.5 },
      uSparkleStrength: { value: cfg.sparkleStrength ?? 0.3 },
      uReflectionMap: { value: waterEmptyReflectionTexture },
      uReflectionMatrix: { value: new THREE.Matrix4() },
      uHasReflection: { value: 0 },
    },
  }) as unknown as WaterMaterial;
}

function hexToInt(v: number): number {
  return v >>> 0;
}

/**
 * Build an organic lake-surface disc: a 72-segment fan whose outer radius
 * follows {@link shapeRadius}, so the water outline matches the carved bowl and
 * the sand mask instead of reading as a stamped circle. Slightly oversized at
 * the rim so the in-shader alpha fade (not the polygon edge) defines the
 * waterline — otherwise the hard polygon border would clip through the fade.
 *
 * Vertices are in the XZ plane (flat, +Y up) — the mesh is placed on the
 * water level with NO extra rotation. (An earlier version rotated the mesh
 * -PI/2 about X on top of XZ-plane vertices, which stood the disc upright as
 * a giant vertical wall.)
 */
export function makeLakeGeometry(
  radius: number,
  seedX: number,
  seedZ: number,
  segments = 72
): THREE.BufferGeometry {
  const positions: number[] = [0, 0, 0];
  const uvs: number[] = [0.5, 0.5];
  // aWaterT: 0 at the centre vertex, ~1 at the rim. The shader's depth/alpha
  // falloff is driven by this varying instead of a radial distance metric, so
  // the same material works for any shape (lake fan or river ribbon).
  const waterT: number[] = [0];
  const indices: number[] = [];
  // Oversize the outer ring a touch so the rim texels never alpha-clip hard.
  const pad = 1.04;
  for (let i = 0; i < segments; i++) {
    const a = (i / segments) * Math.PI * 2;
    const shaped = shapeRadius(a, seedX, seedZ);
    const r = radius * shaped * pad;
    positions.push(Math.cos(a) * r, 0, Math.sin(a) * r);
    uvs.push(0.5 + Math.cos(a) * 0.5, 0.5 + Math.sin(a) * 0.5);
    // t at the rim: the shaped radius scaled by the oversize pad, clamped to
    // 1 so the margin never overshoots the fade range.
    waterT.push(Math.min(1, shaped * pad));
    // Winding: (centre, next, current) keeps the face normal at +Y so the
    // surface is lit (and not backface-culled) when seen from above.
    indices.push(0, ((i + 1) % segments) + 1, i + 1);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geo.setAttribute('aWaterT', new THREE.Float32BufferAttribute(waterT, 1));
  // Contact-foam attributes exist on every water geometry so the shared
  // material never reads an unbound attribute: lakes opt out (deep + no
  // extra foam) — their shoreline foam comes from the aWaterT band.
  const vcount = positions.length / 3;
  geo.setAttribute(
    'aGroundDepth',
    new THREE.Float32BufferAttribute(new Array(vcount).fill(10), 1)
  );
  geo.setAttribute(
    'aFoamExtra',
    new THREE.Float32BufferAttribute(new Array(vcount).fill(0), 1)
  );
  geo.setIndex(indices);
  geo.computeVertexNormals();
  return geo;
}

/**
 * Applies each `<Lake>` once the terrain heightmap is decoded: probes the
 * rim, carves the bowl into the shared sampler, rebuilds terrain derivatives
 * (chunk meshes, physics heightfields, BVH) and spawns the water surface.
 */
export const LakeApplySystem: System = {
  // 'setup' runs before the spawner's fixed-group placement pass: lakes must
  // carve in the same frame the heightmap lands, or the first spawn batch
  // samples pre-carve heights and plants trees inside the water.
  // After TerrainPadApplySystem: pads write both directions, so a pad applied
  // after a carve would fill the basin back in.
  group: 'setup',
  after: [TerrainPadApplySystem],
  update(state: State) {
    if (state.headless) return;

    const cars = waterSideCars(state);
    for (const eid of lakeQuery(state.world)) {
      if (Lake.applied[eid] === 1) continue;

      const radius = Lake.radius[eid] || 6;
      const depth = Lake.depth[eid] || 1.5;
      const waterOffset = Lake.waterOffset[eid];
      const posX = Transform.posX[eid];
      const posZ = Transform.posZ[eid];

      // LakeBowl works in field-local coords for carve/geometry; worldOrigin()
      // returns the world centre for mesh placement. The field offset is read
      // inside applyWaterShape, so we pass both local and world centres here.
      // We don't have the field offset in this loop cleanly, but the terrain
      // field's worldOffset is (0, y, 0) for the default single-field case;
      // local == world - worldOffset, resolved lazily by reading the field.
      // To keep the contract, fetch the field once for the local offset.
      const context = getTerrainContext(state);
      let fieldData: import('../terrain/utils').TerrainEntityData | null = null;
      for (const fd of context.values()) {
        if (fd.initialized && fd.sampler.data) {
          fieldData = fd;
          break;
        }
      }
      if (!fieldData) continue;
      const lx = posX - fieldData.worldOffset.x;
      const lz = posZ - fieldData.worldOffset.z;

      const bowl = new LakeBowl({
        localX: lx,
        localZ: lz,
        worldX: posX,
        worldZ: posZ,
        radius,
        depth,
        waterOffset,
      });
      const waveHeight =
        Lake.waveHeight[eid] > 0
          ? Lake.waveHeight[eid]
          : Math.min(0.09, Math.max(0.02, radius * 0.006));
      const applied = applyWaterShape(
        state,
        eid,
        bowl,
        makeWaterMaterial,
        {
          color: hexToInt(Lake.color[eid]),
          opacity: Lake.opacity[eid],
          ripple: Lake.ripple[eid],
          waveHeight,
          waveSpeed: Lake.waveSpeed[eid] || 1,
        },
        cars
      );
      if (applied) {
        Lake.applied[eid] = 1;
      }
    }
  },
};

/** Scrolls the ripple time uniform on every live water surface. */
export const WaterAnimSystem: System = {
  group: 'draw',
  update(state: State) {
    if (state.headless) return;
    const cars = WATER_SIDECARS.get(state);
    if (!cars) return;
    for (const car of cars.values()) {
      (car.material.uniforms.uTime as { value: number }).value =
        state.time.elapsed;
    }
  },
};

const riverQuery = defineQuery([River, Transform]);

/**
 * Applies each `<River>` once the terrain heightmap is decoded: builds a
 * RiverChannel (WaterShape) and runs the shared apply flow via
 * {@link applyWaterShape}. The path comes from the side-channel (set by the
 * River recipe parser); width/depth/etc. from the component.
 */
export const RiverApplySystem: System = {
  group: 'setup',
  after: [TerrainPadApplySystem],
  update(state: State) {
    if (state.headless) return;
    const cars = waterSideCars(state);
    for (const eid of riverQuery(state.world)) {
      if (River.applied[eid] === 1) continue;
      const path = getRiverPath(state, eid);
      if (path.length < 4) continue; // need ≥ 2 points
      const channel = new RiverChannel({
        path,
        width: River.width[eid] || 6,
        depth: River.depth[eid] || 1.5,
        waterOffset: River.waterOffset[eid],
        // 0 (fresh addComponent zeroes fields) → shape defaults kick in via ||.
        bankWidth: River.bankWidth[eid] || undefined,
        bankHeight: River.bankHeight[eid] || undefined,
      });
      const applied = applyWaterShape(
        state,
        eid,
        channel,
        makeWaterMaterial,
        {
          color: River.color[eid] >>> 0,
          opacity: River.opacity[eid],
          ripple: River.ripple[eid],
          waveHeight: River.waveHeight[eid] || 0.04,
          waveSpeed: River.waveSpeed[eid] || 1,
          // A ribbon is viewed at grazing angles and is only a few metres
          // wide: reach the deep colour early and mute the sky-fresnel and
          // sparkles, or the whole river washes out to icy white.
          depthRampStart: 0.35,
          depthRampEnd: 0.85,
          fresnelStrength: 0.3,
          sparkleStrength: 0.15,
        },
        cars
      );
      if (applied) River.applied[eid] = 1;
    }
  },
};
