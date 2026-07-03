import * as THREE from 'three';
import { defineQuery } from '../../core';
import type { State, System } from '../../core';
import { getRenderingContext } from '../rendering';
import { Transform } from '../transforms/components';
import { TerrainChunk } from '../terrain/components';
import { getTerrainContext } from '../terrain/utils';
import { invalidateTerrainBvh } from '../bvh';
import { getRapierWorld } from '../physics';
import { Lake } from './components';
import { carveBowl, rimHeight } from './carve';
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

function makeWaterMaterial(
  color: number,
  opacity: number,
  ripple: number,
  onShader: (shader: LakeSideCar['shader']) => void
): THREE.MeshStandardMaterial {
  const mat = new THREE.MeshStandardMaterial({
    color,
    transparent: true,
    opacity,
    roughness: 0.12,
    metalness: 0,
    depthWrite: false,
  });
  mat.onBeforeCompile = (shader) => {
    shader.uniforms.uTime = { value: 0 };
    shader.uniforms.uRipple = { value: ripple };
    shader.vertexShader = shader.vertexShader
      .replace(
        '#include <common>',
        `#include <common>
         uniform float uTime;
         uniform float uRipple;
         varying vec2 vWaveXZ;`
      )
      .replace(
        '#include <begin_vertex>',
        `#include <begin_vertex>
         vec4 wPos = modelMatrix * vec4(transformed, 1.0);
         vWaveXZ = wPos.xz;
         transformed.z += uRipple *
           (sin(wPos.x * 1.3 + uTime * 0.6) * 0.5 +
            cos(wPos.z * 1.6 + uTime * 0.45) * 0.5) * 0.03;`
      );
    shader.fragmentShader = shader.fragmentShader
      .replace(
        '#include <common>',
        `#include <common>
         uniform float uTime;
         uniform float uRipple;
         varying vec2 vWaveXZ;`
      )
      .replace(
        '#include <color_fragment>',
        `#include <color_fragment>
         // Low-frequency drift — high frequencies read as a checker pattern
         // on the flat disc instead of water.
         float shimmer = sin(vWaveXZ.x * 0.5 + uTime * 0.6) *
                         cos(vWaveXZ.y * 0.65 - uTime * 0.5) +
                         0.5 * sin((vWaveXZ.x + vWaveXZ.y) * 1.0 + uTime * 1.1);
         diffuseColor.rgb += shimmer * 0.013 * uRipple;`
      );
    onShader(shader as unknown as LakeSideCar['shader']);
  };
  return mat;
}

function hexToInt(v: number): number {
  return v >>> 0;
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

      const rimY = rimHeight(data.sampler, lx, lz, radius);
      const waterY = rimY - waterOffset;
      carveBowl(data.sampler, lx, lz, radius, waterY, depth);
      Lake.waterY[eid] = waterY;
      Lake.applied[eid] = 1;

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

      // Water surface: slightly inside the rim so the shore never gaps.
      const scene = getRenderingContext(state).scene;
      const car: Partial<LakeSideCar> = { shader: null };
      const material = makeWaterMaterial(
        hexToInt(Lake.color[eid]),
        Lake.opacity[eid],
        Lake.ripple[eid],
        (shader) => {
          car.shader = shader;
        }
      );
      const mesh = new THREE.Mesh(
        new THREE.CircleGeometry(radius * 0.985, 48),
        material
      );
      mesh.rotation.x = -Math.PI / 2;
      mesh.position.set(
        Transform.posX[eid],
        data.worldOffset.y + waterY,
        Transform.posZ[eid]
      );
      mesh.renderOrder = 2;
      mesh.receiveShadow = true;
      scene.add(mesh);

      const body: WaterBody = {
        x: Transform.posX[eid],
        z: Transform.posZ[eid],
        radius,
        waterY: data.worldOffset.y + waterY,
      };
      registerWaterBody(state, body);
      cars.set(eid, { mesh, material, shader: car.shader ?? null, body });

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
