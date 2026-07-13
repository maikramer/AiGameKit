import * as THREE from 'three';
import { defineQuery } from '../../core';
import type { State, System } from '../../core';
import { getScene, setupCsmMaterials } from '../rendering';
import { sampleTerrainSurface } from '../spawner/surface';
import { Terrain } from '../terrain/components';
import { TerrainPadApplySystem } from '../terrain/pad-systems';
import { getTerrainContext } from '../terrain/utils';
import { deleteRoadData, getRoadData, Road } from './components';
import { makeRoadGeometry, resampleRoadPath, smoothPath } from './geometry';

const roadQuery = defineQuery([Road]);
const terrainQuery = defineQuery([Terrain]);

// Cache de texturas partilhado por URL (mesmo padrão da composition).
const _loader = new THREE.TextureLoader();
const _textureCache = new Map<string, THREE.Texture>();

function loadRoadTexture(url: string, srgb: boolean): THREE.Texture {
  const key = `${srgb ? 's' : 'l'}:${url}`;
  const cached = _textureCache.get(key);
  if (cached) return cached;
  const tex = _loader.load(url);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.LinearSRGBColorSpace;
  tex.anisotropy = 8;
  tex.needsUpdate = true;
  _textureCache.set(key, tex);
  return tex;
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
 * Constrói cada `<Road>` assim que a superfície do terreno está pronta
 * (depois dos TerrainPads aplainarem — a estrada tem de amostrar as alturas
 * pós-flatten). Mundos sem terreno constroem plano a y=0.
 */
export const RoadApplySystem: System = {
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
      let heightAt: (x: number, z: number) => number;
      if (samplerReady) {
        heightAt = (x, z) =>
          sampleTerrainSurface(state, x, z, 0.5)?.worldY ?? 0;
      } else {
        const terrainExists = terrainQuery(state.world).length > 0;
        if (terrainExists || state.time.elapsed < 2) continue;
        heightAt = () => 0;
      }

      const width = Road.width[eid] || 5;
      const spacing = Road.stationSpacing[eid] || 1.5;
      const iterations = Road.smoothing[eid];
      const path = resampleRoadPath(smoothPath(data.path, iterations), spacing);

      const geometry = makeRoadGeometry(path, {
        width,
        textureScale: Road.textureScale[eid] || 16,
        edgeFeather: Road.edgeFeather[eid],
        edgeNoise: Road.edgeNoise[eid],
        endFeatherStart: Road.endFeatherStart[eid],
        endFeatherEnd: Road.endFeatherEnd[eid],
        heightAt,
        yOffset: Road.yOffset[eid] || 0.06,
      });

      const material = new THREE.MeshStandardMaterial({
        color: 0xffffff,
        roughness: Road.roughness[eid] || 1,
        metalness: Road.metalness[eid],
        vertexColors: true,
        transparent: true,
        opacity: Road.opacity[eid] || 1,
        depthWrite: false,
        polygonOffset: true,
        polygonOffsetFactor: -1,
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
};
