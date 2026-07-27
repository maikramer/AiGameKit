import * as THREE from 'three';
import { defineSystem, defineQuery } from '../../core';
import type { State, System } from '../../core';
import { getScene, setupCsmMaterials } from '../rendering';
import { sampleTerrainSurface } from '../spawner/surface';
import { Terrain } from '../terrain/components';
import { applyOverride } from '../terrain/density-map';
import { rebuildTerrainDerivatives } from '../terrain/height-brush';
import { sampleHeightAt } from '../terrain/height-sampler';
import { TerrainPadApplySystem } from '../terrain/pad-systems';
import { registerGroundBrush } from '../terrain/brush-registry';
import { refreshChunkResolutions } from '../terrain/systems';
import { getTerrainContext } from '../terrain/utils';
import { Transform, WorldTransform } from '../transforms/components';
import { carveRoadCorridor } from './carve';
import { deleteRoadData, getRoadData, Road } from './components';
import {
  densifyPathByHeight,
  makeRoadGeometry,
  resampleRoadPath,
  smoothPath,
} from './geometry';

const roadQuery = defineQuery([Road]);
const terrainQuery = defineQuery([Terrain]);

/** Extra metres of full-weight graded bed beyond painted `width` (both sides). */
const ROADBED_OVERHANG = 1.5;

// Y base do field do terreno (igual ao helper privado do spawner/surface).
function terrainBaseY(state: State, terrainEntity: number): number {
  if (state.hasComponent(terrainEntity, WorldTransform)) {
    return WorldTransform.posY[terrainEntity];
  }
  return Transform.posY[terrainEntity];
}

// Cache de texturas partilhado por URL (mesmo padrão da composition).
const _loader = new THREE.TextureLoader();
const _textureCache = new Map<string, THREE.Texture>();

function loadRoadTexture(url: string, srgb: boolean): THREE.Texture {
  const key = `${srgb ? 's' : 'l'}:${url}`;
  const cached = _textureCache.get(key);
  if (cached) return cached;
  // Do not set needsUpdate before the image arrives — TextureLoader does that
  // on load, and premature flags spam "no image data found" in the console.
  const tex = _loader.load(url);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.LinearSRGBColorSpace;
  tex.anisotropy = 8;
  _textureCache.set(key, tex);
  return tex;
}

/**
 * Max height over a small XZ neighborhood. Ribbon chords between stations sit
 * under convex mesh peaks when LOD refines; sampling the max (not the center)
 * parks verts on local crests so sand does not poke through the decal.
 */
export function maxNeighborhoodHeight(
  sample: (x: number, z: number) => number,
  x: number,
  z: number,
  reach: number
): number {
  return Math.max(
    sample(x, z),
    sample(x + reach, z),
    sample(x - reach, z),
    sample(x, z + reach),
    sample(x, z - reach)
  );
}

interface RoadSidecar {
  mesh: THREE.Mesh;
  material: THREE.MeshStandardMaterial;
}

const ROAD_SIDECARS = new WeakMap<State, Map<number, RoadSidecar>>();

function roadSidecars(state: State): Map<number, RoadSidecar> {
  let m = ROAD_SIDECARS.get(state);
  if (!m) {
    m = new Map();
    ROAD_SIDECARS.set(state, m);
  }
  return m;
}

function disposeRoad(state: State, eid: number): void {
  const cars = ROAD_SIDECARS.get(state);
  const car = cars?.get(eid);
  if (!car) return;
  car.mesh.removeFromParent();
  car.mesh.geometry.dispose();
  car.material.dispose(); // texturas são cache partilhado — não descartar
  cars!.delete(eid);
  deleteRoadData(state, eid);
}

/**
 * Ribbon Y = walk surface after roadbed prep (CCT / heightfield).
 * Flatten roads: exact `sampleHeightAt` on the prepared sampler.
 * Non-flatten: mesh surface at the same point (decal on raw terrain).
 */
export function buildRoadHeightAt(
  state: State,
  eid: number,
  _spacing: number,
  _width: number
): (x: number, z: number) => number {
  if (Road.flatten[eid] === 1) {
    for (const [fe, fd] of getTerrainContext(state)) {
      if (!fd.initialized || !fd.sampler.data) continue;
      const baseY = terrainBaseY(state, fe);
      const ox = fd.worldOffset.x;
      const oz = fd.worldOffset.z;
      return (x, z) => baseY + sampleHeightAt(fd.sampler, x - ox, z - oz);
    }
  }

  return (x, z) => {
    const y = sampleTerrainSurface(state, x, z, 0.5)?.worldY;
    return y !== undefined && Number.isFinite(y) ? y : 0;
  };
}

/**
 * Constrói cada `<Road>` assim que a superfície do terreno está pronta
 * (depois dos TerrainPads aplainarem — a estrada tem de amostrar as alturas
 * pós-flatten). Mundos sem terreno constroem plano a y=0.
 */
export const RoadApplySystem: System = defineSystem({
  name: 'RoadApplySystem',
  group: 'setup',
  after: [TerrainPadApplySystem],
  update(state: State) {
    if (state.headless) return;
    const scene = getScene(state);
    if (!scene) return;

    for (const eid of roadQuery(state.world)) {
      if (Road.applied[eid] === 1) continue;
      const data = getRoadData(state, eid);
      if (!data || data.path.length < 4) {
        Road.applied[eid] = 1;
        continue;
      }

      // Gate: constrói só quando o heightmap do terreno está DECODIFICADO
      // (initialized && sampler.data — o mesmo gate dos lakes; antes disso o
      // terreno responde com um sampler flat a 0 e a estrada ficaria
      // enterrada). A entidade <Terrain> pode ainda não existir nos
      // primeiros ticks — a ausência de terreno só é conclusiva após um
      // período de graça; mundos genuinamente sem terreno (ex.: hello-world)
      // constroem plano a y=0 passado esse período.
      let samplerReady = false;
      for (const fd of getTerrainContext(state).values()) {
        if (fd.initialized && fd.sampler.data) {
          samplerReady = true;
          break;
        }
      }
      const width = Road.width[eid] || 5;
      const spacing = Road.stationSpacing[eid] || 0.35;

      let heightAt: (x: number, z: number) => number;
      if (samplerReady) {
        heightAt = buildRoadHeightAt(state, eid, spacing, width);
      } else {
        const terrainExists = terrainQuery(state.world).length > 0;
        if (terrainExists || state.time.elapsed < 2) continue;
        heightAt = () => 0;
      }
      const iterations = Road.smoothing[eid];
      let path = resampleRoadPath(smoothPath(data.path, iterations), spacing);

      // Phase A — prepare roadbed (real-world grading). Mutate sampler first
      // so mesh/CCT/BVH/ribbon share one surface. Phase B pavements below.
      if (Road.flatten[eid] === 1) {
        for (const [fe, fd] of getTerrainContext(state)) {
          if (!fd.initialized || !fd.sampler.data) continue;
          const localPath: number[] = new Array(path.length);
          for (let i = 0; i < path.length; i += 2) {
            localPath[i] = path[i]! - fd.worldOffset.x;
            localPath[i + 1] = path[i + 1]! - fd.worldOffset.z;
          }
          const falloff = Road.flattenFalloff[eid] || 5;
          const window = Road.flattenWindow[eid] || 16;
          const maxGrade = Number.isFinite(Road.flattenMaxGrade[eid])
            ? Road.flattenMaxGrade[eid]
            : 0.22;
          // Bed slightly wider than the painted ribbon (platform + shoulders).
          const bedWidth = width + ROADBED_OVERHANG;
          const changed = carveRoadCorridor(fd.sampler, {
            path: localPath,
            width: bedWidth,
            falloff,
            window,
            maxGrade,
          });
          // Phase B height source = prepared analytic bed (same as CCT).
          heightAt = buildRoadHeightAt(state, eid, spacing, width);
          // Density boost: without it the carved bed is invisible — base mesh
          // verts are ~worldSize/resolution apart and miss a ~width corridor.
          if (fd.density) {
            const reach = bedWidth / 2 + falloff;
            for (let i = 0; i + 3 < localPath.length; i += 2) {
              applyOverride(
                fd.density,
                {
                  minX: Math.min(localPath[i]!, localPath[i + 2]!) - reach,
                  maxX: Math.max(localPath[i]!, localPath[i + 2]!) + reach,
                  minZ: Math.min(localPath[i + 1]!, localPath[i + 3]!) - reach,
                  maxZ: Math.max(localPath[i + 1]!, localPath[i + 3]!) + reach,
                },
                255
              );
            }
            refreshChunkResolutions(state, fe, fd);
          }
          if (changed) rebuildTerrainDerivatives(state, fe, fd);
          // Footprint for navmesh adaptive source + diagnostics.
          let minX = Infinity;
          let maxX = -Infinity;
          let minZ = Infinity;
          let maxZ = -Infinity;
          const reach = bedWidth / 2 + falloff;
          for (let i = 0; i < localPath.length; i += 2) {
            const px = localPath[i]!;
            const pz = localPath[i + 1]!;
            minX = Math.min(minX, px - reach);
            maxX = Math.max(maxX, px + reach);
            minZ = Math.min(minZ, pz - reach);
            maxZ = Math.max(maxZ, pz + reach);
          }
          registerGroundBrush(state, {
            kind: 'road',
            minX,
            maxX,
            minZ,
            maxZ,
            path: localPath.slice(),
            halfWidth: bedWidth / 2,
          });
          break; // só o primeiro field ready (mesmo limite dos pads)
        }
      }

      // After carve, densify so chords hug the walk sampler (kills sand
      // wedges) without lifting verts above CCT height.
      if (samplerReady) {
        heightAt = buildRoadHeightAt(state, eid, spacing, width);
        path = densifyPathByHeight(path, heightAt, 0.02, 5);
      }

      const yOffset = Number.isFinite(Road.yOffset[eid])
        ? Road.yOffset[eid]
        : 0;
      const geometry = makeRoadGeometry(path, {
        width,
        textureScale: Road.textureScale[eid] || 16,
        edgeFeather: Road.edgeFeather[eid],
        edgeNoise: Road.edgeNoise[eid],
        endFeatherStart: Road.endFeatherStart[eid],
        endFeatherEnd: Road.endFeatherEnd[eid],
        yOffset,
        heightAt,
      });

      // Opaque + alphaTest (not transparent): transparent pass sorted poorly
      // vs terrain and lost near-camera z-fights even with depthWrite.
      // Feather still works — fragments below alphaTest are discarded.
      const material = new THREE.MeshStandardMaterial({
        color: data.textureUrl ? 0xffffff : 0x8a7a68,
        roughness: Road.roughness[eid] || 1,
        metalness: Road.metalness[eid],
        vertexColors: true,
        transparent: false,
        opacity: Road.opacity[eid] || 1,
        alphaTest: 0.35,
        depthWrite: true,
        depthTest: true,
        polygonOffset: true,
        polygonOffsetFactor: -2,
        polygonOffsetUnits: -2,
      });
      if (data.textureUrl)
        material.map = loadRoadTexture(data.textureUrl, true);
      if (data.normalMapUrl) {
        material.normalMap = loadRoadTexture(data.normalMapUrl, false);
      }
      if (data.roughnessMapUrl) {
        material.roughnessMap = loadRoadTexture(data.roughnessMapUrl, false);
      }

      const mesh = new THREE.Mesh(geometry, material);
      mesh.castShadow = false;
      mesh.receiveShadow = true;
      mesh.renderOrder = 1;
      scene.add(mesh);
      setupCsmMaterials(state, mesh);

      roadSidecars(state).set(eid, { mesh, material });
      state.onDestroy(eid, () => disposeRoad(state, eid));
      Road.applied[eid] = 1;
    }
  },
  dispose(state: State) {
    const cars = ROAD_SIDECARS.get(state);
    if (!cars) return;
    for (const eid of [...cars.keys()]) disposeRoad(state, eid);
  },
});

/**
 * Retarget removed: flatten ribbons sample the same analytic heightfield as
 * CCT/chunk meshes. Periodic rebuild was a no-op (or fought depth) and burned
 * CPU. Kept as a no-op export so older imports/tests don't break.
 */
export const RoadRetargetSystem: System = defineSystem({
  name: 'RoadRetargetSystem',
  group: 'simulation',
  update() {
    /* intentionally empty — see comment above */
  },
});
