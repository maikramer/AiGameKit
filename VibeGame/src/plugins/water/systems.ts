import * as THREE from 'three';
import { defineQuery } from '../../core';
import type { State, System } from '../../core';
import { getRenderingContext } from '../rendering';
import { Transform } from '../transforms/components';
import { TerrainChunk } from '../terrain/components';
import { getTerrainContext } from '../terrain/utils';
import { invalidateTerrainBvh } from '../bvh';
import { getRapierWorld } from '../physics';
import { applyOverride } from '../terrain/density-map';
import { refreshChunkResolutions } from '../terrain/systems';
import { logger } from '../../core/utils/logger';
import { Lake } from './components';
import { carveBowl, rimHeight, shoreFraction, shapeRadius } from './carve';
import { registerWaterBody, unregisterWaterBody } from './registry';
import type { WaterBody } from './registry';

const lakeQuery = defineQuery([Lake, Transform]);

interface LakeSideCar {
  mesh: THREE.Mesh;
  material: THREE.MeshStandardMaterial;
  shader: { uniforms: Record<string, { value: unknown }> } | null;
  body: WaterBody;
}

const SIDECARS = new WeakMap<State, Map<number, LakeSideCar>>();

function sidecars(state: State): Map<number, LakeSideCar> {
  let m = SIDECARS.get(state);
  if (!m) {
    m = new Map();
    SIDECARS.set(state, m);
  }
  return m;
}

/** Lake-specific material inputs. The water colour, depth fade and alpha
 *  falloff are all driven from these so each lake can be tuned independently. */
interface WaterMaterialConfig {
  /** Lake centre in world XZ (drives the radial depth + alpha falloff). */
  center: THREE.Vector2;
  /** Full bowl radius (m). */
  radius: number;
  /** shoreFraction()·radius — radius of the waterline. */
  shoreRadius: number;
  /** Deep-water tint (hex). */
  color: number;
  /** Base surface opacity 0..1 (faded further at the shoreline). */
  opacity: number;
  /** Ripple animation strength (0 = still water). */
  ripple: number;
  /** Wave amplitude in metres (already resolved: auto or explicit). */
  waveHeight: number;
  /** Wave/shimmer animation speed multiplier. */
  waveSpeed: number;
}

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

function makeWaterMaterial(
  cfg: WaterMaterialConfig,
  onShader: (shader: LakeSideCar['shader']) => void
): THREE.MeshStandardMaterial {
  const mat = new THREE.MeshStandardMaterial({
    color: 0xffffff,
    transparent: true,
    opacity: cfg.opacity,
    roughness: 0.08,
    metalness: 0,
    depthWrite: false,
  });
  mat.onBeforeCompile = (shader) => {
    shader.uniforms.uTime = { value: 0 };
    shader.uniforms.uRipple = { value: cfg.ripple };
    shader.uniforms.uWaveHeight = { value: cfg.waveHeight };
    shader.uniforms.uWaveSpeed = { value: cfg.waveSpeed };
    shader.uniforms.uCenter = { value: cfg.center };
    shader.uniforms.uShoreRadius = { value: cfg.shoreRadius };
    shader.uniforms.uOpacity = { value: cfg.opacity };
    shader.uniforms.uShallowColor = {
      value: new THREE.Color(shallowTint(cfg.color)),
    };
    shader.uniforms.uDeepColor = { value: new THREE.Color(cfg.color) };
    shader.uniforms.uSkyTint = { value: new THREE.Color(0xbfd8e6) };
    // Lake centre in local carve space — seeds the organic shoreline so the
    // shader's depth/alpha falloff tracks the carved bowl's outline, not a circle.
    shader.uniforms.uSeedX = { value: cfg.center.x };
    shader.uniforms.uSeedZ = { value: cfg.center.y };
    shader.vertexShader = shader.vertexShader
      .replace(
        '#include <common>',
        `#include <common>
         uniform float uTime;
         uniform float uRipple;
         uniform float uWaveHeight;
         uniform float uWaveSpeed;
         uniform vec2 uCenter;
         uniform float uShoreRadius;
         uniform float uSeedX;
         uniform float uSeedZ;
         varying vec2 vWaveXZ;
         varying vec3 vViewDir;`
      )
      .replace(
        '#include <begin_vertex>',
        `#include <begin_vertex>
         vec4 wPos = modelMatrix * vec4(transformed, 1.0);
         vWaveXZ = wPos.xz;
         float wt = uTime * uWaveSpeed;
         // Two crossing waves plus a slow diagonal swell — reads as gentle
         // open-water motion instead of a single repeating sine.
         float wave = sin(wPos.x * 1.3 + wt * 0.9) * 0.5 +
                      cos(wPos.z * 1.6 + wt * 0.7) * 0.5 +
                      sin((wPos.x + wPos.z) * 0.55 + wt * 1.4) * 0.35;
         // The disc lies in the local XZ plane (no mesh rotation), so local
         // +Y is world up. Amplitude is per-lake (wave-height XML attr or
         // radius-scaled auto), muted entirely when ripple is 0.
         transformed.y += min(uRipple, 1.0) * wave * uWaveHeight;
         vViewDir = normalize(cameraPosition - (modelMatrix * vec4(transformed, 1.0)).xyz);`
      );
    shader.fragmentShader = shader.fragmentShader
      .replace(
        '#include <common>',
        `#include <common>
         uniform float uTime;
         uniform float uRipple;
         uniform float uWaveSpeed;
         uniform vec2 uCenter;
         uniform float uShoreRadius;
         uniform float uOpacity;
         uniform vec3 uShallowColor;
         uniform vec3 uDeepColor;
         uniform vec3 uSkyTint;
         uniform float uSeedX;
         uniform float uSeedZ;
         varying vec2 vWaveXZ;
         varying vec3 vViewDir;
         // GLSL port of shapeRadius() — must stay in sync with carve.ts.
         const float SHORE_SHAPE_AMP = 0.28;
         float shapeRadius(float angle) {
           float phi1 = (uSeedX * 12.9898 + uSeedZ * 78.233) * 0.1;
           float phi2 = (uSeedX * 4.1764 - uSeedZ * 29.113) * 0.1;
           float n = sin(angle * 2.0 + phi1) * 0.6
                   + sin(angle * 3.0 - phi2) * 0.3
                   + sin(angle * 5.0 + phi1 * 1.7) * 0.1;
           return 1.0 + n * SHORE_SHAPE_AMP;
         }
         // Organic depth/alpha: normalise the radial distance by the shaped
         // radius at this angle so the falloff tracks the shoreline, not a circle.
         float lakeShapeT() {
           vec2 rel = vWaveXZ - uCenter;
           float dist = length(rel);
           float angle = atan(rel.y, rel.x);
           float effShore = uShoreRadius * shapeRadius(angle);
           return clamp(dist / max(effShore, 0.001), 0.0, 1.5);
         }
         float lakeDepthNorm() {
           float t = lakeShapeT();
           return 1.0 - smoothstep(0.0, 1.0, t);
         }
         float shoreAlpha() {
           float t = lakeShapeT();
           return 1.0 - smoothstep(0.9, 1.0, t);
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
         // Cartoon shoreline foam: a wobbling band just inside the waterline
         // plus a broken dashed line further in (wave wash), both toon-stepped.
         float lakeFoam() {
           float t = lakeShapeT();
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
         }`
      )
      .replace(
        '#include <color_fragment>',
        `#include <color_fragment>
         float depthNorm = lakeDepthNorm();
         // Posterize the depth gradient into soft cel bands (cartoon read),
         // blended with the smooth ramp so band edges stay gentle.
         float banded = floor(depthNorm * 3.0 + 0.5) / 3.0;
         diffuseColor.rgb = mix(uShallowColor, uDeepColor, mix(depthNorm, banded, 0.55));
         float fres = lakeFresnel();
         diffuseColor.rgb = mix(diffuseColor.rgb, uSkyTint, clamp(fres * 0.5, 0.0, 0.5));
         // Low-frequency drift — high frequencies read as a checker pattern
         // on the flat disc instead of water.
         float st = uTime * uWaveSpeed;
         float shimmer = sin(vWaveXZ.x * 0.5 + st * 0.6) *
                         cos(vWaveXZ.y * 0.65 - st * 0.5) +
                         0.5 * sin((vWaveXZ.x + vWaveXZ.y) * 1.0 + st * 1.1);
         diffuseColor.rgb += shimmer * 0.011 * uRipple;
         // Sparse animated glints — stylized sun sparkle without real specular.
         float sp1 = vnoise(vWaveXZ * 0.9 + st * 0.35);
         float sp2 = vnoise(vWaveXZ * 1.7 - st * 0.27);
         float sparkle = smoothstep(0.82, 0.98, sp1 * sp2 * 1.6);
         diffuseColor.rgb += sparkle * 0.3 * uRipple;
         float foam = lakeFoam();
         diffuseColor.rgb = mix(diffuseColor.rgb, vec3(1.0), foam * 0.85);`
      )
      .replace(
        '#include <alphatest_fragment>',
        `#include <alphatest_fragment>
         // Foam stays visible through the shoreline alpha fade-out.
         diffuseColor.a = max(diffuseColor.a * shoreAlpha(), foam * 0.9 * shoreAlpha());`
      );
    onShader(shader as unknown as LakeSideCar['shader']);
  };
  return mat;
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
  const indices: number[] = [];
  // Oversize the outer ring a touch so the rim texels never alpha-clip hard.
  const pad = 1.04;
  for (let i = 0; i < segments; i++) {
    const a = (i / segments) * Math.PI * 2;
    const r = radius * shapeRadius(a, seedX, seedZ) * pad;
    positions.push(Math.cos(a) * r, 0, Math.sin(a) * r);
    uvs.push(0.5 + Math.cos(a) * 0.5, 0.5 + Math.sin(a) * 0.5);
    // Winding: (centre, next, current) keeps the face normal at +Y so the
    // surface is lit (and not backface-culled) when seen from above.
    indices.push(0, ((i + 1) % segments) + 1, i + 1);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
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
  group: 'setup',
  update(state: State) {
    if (state.headless) return;

    const context = getTerrainContext(state);
    let field: {
      entity: number;
      data: import('../terrain/utils').TerrainEntityData;
    } | null = null;
    for (const [entity, data] of context) {
      if (data.initialized && data.sampler.data) {
        field = { entity, data };
        break;
      }
    }
    if (!field) return;

    const cars = sidecars(state);
    for (const eid of lakeQuery(state.world)) {
      if (Lake.applied[eid] === 1) continue;

      const radius = Lake.radius[eid] || 6;
      const depth = Lake.depth[eid] || 1.5;
      const waterOffset = Lake.waterOffset[eid];
      const { data } = field;
      const lx = Transform.posX[eid] - data.worldOffset.x;
      const lz = Transform.posZ[eid] - data.worldOffset.z;

      // Bump the density map around the lake so chunks rendering it use a
      // finer mesh — capturing the basin detail the carve writes into the
      // heightfield. carveBowl itself is unchanged; this only raises the
      // sampling resolution. Margin covers the organic shoreline overshoot
      // (shapeRadius amplitude) so boosted tiles reach past the rim.
      if (data.density) {
        const margin = 1.3;
        applyOverride(
          data.density,
          {
            minX: lx - radius * margin,
            minZ: lz - radius * margin,
            maxX: lx + radius * margin,
            maxZ: lz + radius * margin,
          },
          255
        );
        refreshChunkResolutions(state, field.entity, data);
      }

      const rimY = rimHeight(data.sampler, lx, lz, radius);
      const waterY = rimY - waterOffset;
      const carved = carveBowl(data.sampler, lx, lz, radius, rimY, depth);
      // Only commit once the carve ran against a real heightmap. On a flat
      // (dataless) sampler carveBowl returns false and carves nothing — without
      // this guard the lake would be marked applied and never retry, so the
      // bowl would silently never appear (colour yes, geometry no).
      if (!carved) {
        logger.warn(
          `[water] Lake ${eid} carve skipped (sampler not ready at ${lx.toFixed(1)},${lz.toFixed(
            1
          )}); will retry next frame`
        );
        continue;
      }
      const shoreR = shoreFraction(depth, waterOffset) * radius;
      Lake.waterY[eid] = waterY;
      Lake.applied[eid] = 1;
      logger.info(
        `[water] Lake ${eid} carved: rimY=${rimY.toFixed(1)} depth=${depth} ` +
          `waterY=${waterY.toFixed(1)} shoreR=${shoreR.toFixed(1)} radius=${radius}`
      );

      // Terrain derivatives all read the sampler — force their rebuild.
      for (const chunk of data.chunks) TerrainChunk.meshDirty[chunk] = 1;
      const world = getRapierWorld(state);
      if (world) {
        for (const body of data.chunkColliders.values()) {
          world.removeRigidBody(body);
        }
        data.chunkColliders.clear();
      }
      invalidateTerrainBvh(state, field.entity);

      // Water surface: the disc covers the full bowl radius; the alpha fades
      // to zero at the waterline (shoreR) in-shader so the edge meets the
      // beach floor, not the outer bowl rim.
      const scene = getRenderingContext(state).scene;
      // Auto wave amplitude: proportional to the lake size so a small pond
      // barely stirs while a big lake visibly swells. Explicit wave-height
      // XML overrides.
      const waveHeight =
        Lake.waveHeight[eid] > 0
          ? Lake.waveHeight[eid]
          : Math.min(0.09, Math.max(0.02, radius * 0.006));
      const material = makeWaterMaterial(
        {
          center: new THREE.Vector2(Transform.posX[eid], Transform.posZ[eid]),
          radius,
          shoreRadius: shoreR,
          color: hexToInt(Lake.color[eid]),
          opacity: Lake.opacity[eid],
          ripple: Lake.ripple[eid],
          waveHeight,
          waveSpeed: Lake.waveSpeed[eid] || 1,
        },
        (shader) => {
          // onBeforeCompile fires on the first draw, after the sidecar below
          // is registered — write the shader ref into the LIVE sidecar. (The
          // old code captured a stale `?? null` at registration time, so
          // uTime was never advanced and every lake rendered frozen.)
          const c = cars.get(eid);
          if (c) c.shader = shader;
        }
      );
      const mesh = new THREE.Mesh(makeLakeGeometry(radius, lx, lz), material);
      mesh.position.set(
        Transform.posX[eid],
        data.worldOffset.y + waterY,
        Transform.posZ[eid]
      );
      mesh.renderOrder = 2;
      mesh.receiveShadow = true;
      scene.add(mesh);

      const body: WaterBody = {
        kind: 'lake',
        x: Transform.posX[eid],
        z: Transform.posZ[eid],
        radius,
        shoreRadius: shoreR,
        waterY: data.worldOffset.y + waterY,
      };
      registerWaterBody(state, body);
      cars.set(eid, { mesh, material, shader: null, body });

      state.onDestroy(eid, () => {
        const c = cars.get(eid);
        if (!c) return;
        cars.delete(eid);
        c.mesh.removeFromParent();
        c.mesh.geometry.dispose();
        c.material.dispose();
        unregisterWaterBody(state, c.body);
      });
    }
  },
};

/** Scrolls the ripple time uniform on every live water surface. */
export const WaterAnimSystem: System = {
  group: 'draw',
  update(state: State) {
    if (state.headless) return;
    const cars = SIDECARS.get(state);
    if (!cars) return;
    for (const car of cars.values()) {
      if (car.shader) {
        (car.shader.uniforms.uTime as { value: number }).value =
          state.time.elapsed;
      }
    }
  },
};
