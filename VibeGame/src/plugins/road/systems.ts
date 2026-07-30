import * as THREE from 'three';
import { defineSystem, defineQuery } from '../../core';
import type { State, System } from '../../core';
import { getScene, setupCsmMaterials } from '../rendering';
import {
  sampleMeshSurfaceHeight,
  sampleTerrainSurface,
} from '../spawner/surface';
import { Terrain } from '../terrain/components';
import {
  minEffectiveWidth,
  rebuildTerrainDerivatives,
} from '../terrain/height-brush';
import {
  applyCorridorDensity,
  densityLeafPad,
} from '../terrain/ground-mutation';
import { sampleHeightAt } from '../terrain/height-sampler';
import { meshSurfaceResolutionForPoint } from '../terrain/lod-select';
import { TerrainPadApplySystem } from '../terrain/pad-systems';
import { registerGroundBrush } from '../terrain/brush-registry';
import { refreshChunkResolutions } from '../terrain/systems';
import {
  getTerrainContext,
  registerGroundMutationCallback,
} from '../terrain/utils';
import { Transform, WorldTransform } from '../transforms/components';
import { carveRoadCorridor } from './carve';
import { deleteRoadData, getRoadData, Road } from './components';
import {
  densifyPathByHeight,
  distanceToPolyline,
  extendPathEnds,
  makeRoadGeometry,
  resampleRoadPath,
  smoothPath,
} from './geometry';
import {
  chainRoleFor,
  detectRoadJunctions,
  emptyFusionPlan,
  junctionNetworkSignature,
  makeFusionWidthAt,
  makeJunctionGeometry,
  makeWidthAtFromVertexWidths,
  planRoadFusion,
  retractPathEnds,
  stitchEndToEndChains,
  type RoadFusionPlan,
  type RoadJunction,
  type RoadJunctionInput,
  type StitchedRoadChain,
} from './junctions';

const roadQuery = defineQuery([Road]);
const terrainQuery = defineQuery([Terrain]);

/** Extra metres of full-weight graded bed beyond painted `width` (both sides = 1 m each). */
export const ROADBED_OVERHANG = 2;

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

interface JunctionSidecar {
  mesh: THREE.Mesh;
  material: THREE.MeshStandardMaterial;
  id: string;
}

const ROAD_SIDECARS = new WeakMap<State, Map<number, RoadSidecar>>();
/** Crossing patches (degree ≥ 3 only — not end-to-end stamps). */
const JUNCTION_SIDECARS = new WeakMap<State, Map<string, JunctionSidecar>>();
/** Last fusion-graph signature — rebuild stitched ribbons when network changes. */
const FUSION_SIG = new WeakMap<State, string>();
const ROAD_CHAINS = new WeakMap<State, StitchedRoadChain[]>();
const JUNCTION_PATCH_SIG = new WeakMap<State, string>();

/**
 * Draw-order bias handed out per ribbon. City streets overlap (a plaza street
 * crossing the main road), and two coplanar decals with identical depth bias
 * flicker as the camera moves. A unique bias per road makes the winner stable.
 */
const ROAD_BIAS = new WeakMap<State, number>();

function nextRoadBias(state: State): number {
  const n = (ROAD_BIAS.get(state) ?? 0) + 1;
  ROAD_BIAS.set(state, n);
  return n;
}

/** Roads whose ribbon must be regenerated: the ground moved under them. */
const ROAD_DIRTY = new WeakMap<State, Set<number>>();
/** Leaders that must re-carve the bed (stitch topology), not only re-pave. */
const ROAD_REGRADE = new WeakMap<State, Set<number>>();
const ROAD_MUTATION_HOOKED = new WeakSet<State>();

function roadDirty(state: State): Set<number> {
  let s = ROAD_DIRTY.get(state);
  if (!s) {
    s = new Set();
    ROAD_DIRTY.set(state, s);
  }
  return s;
}

function roadNeedRegrade(state: State): Set<number> {
  let s = ROAD_REGRADE.get(state);
  if (!s) {
    s = new Set();
    ROAD_REGRADE.set(state, s);
  }
  return s;
}

/**
 * Any later carve (settlement pad, lake/river, a sibling road) rewrites the
 * sampler under an already-paved ribbon, leaving it buried by up to a metre.
 * Subscribing to ground mutations makes ribbons re-grade and re-pave instead of
 * depending on plugin/system ordering.
 */
function hookGroundMutations(state: State, currentEid: () => number): void {
  if (ROAD_MUTATION_HOOKED.has(state)) return;
  ROAD_MUTATION_HOOKED.add(state);
  registerGroundMutationCallback(state, () => {
    const skip = currentEid();
    const dirty = roadDirty(state);
    for (const eid of roadSidecars(state).keys()) {
      if (eid !== skip) dirty.add(eid);
    }
  });
}

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
  // Network changed — next frame rebuilds fusion discs + neighbour docks.
  FUSION_SIG.delete(state);
}

function disposeJunction(state: State, id: string): void {
  const cars = JUNCTION_SIDECARS.get(state);
  const car = cars?.get(id);
  if (!car) return;
  car.mesh.removeFromParent();
  car.mesh.geometry.dispose();
  car.material.dispose();
  cars!.delete(id);
}

function disposeAllJunctionDiscs(state: State): void {
  const cars = JUNCTION_SIDECARS.get(state);
  if (!cars) return;
  for (const id of [...cars.keys()]) disposeJunction(state, id);
}

function disposeRoadMeshOnly(state: State, eid: number): void {
  const cars = ROAD_SIDECARS.get(state);
  const car = cars?.get(eid);
  if (!car) return;
  car.mesh.removeFromParent();
  car.mesh.geometry.dispose();
  car.material.dispose();
  cars!.delete(eid);
}

function cachedChains(state: State): StitchedRoadChain[] {
  return ROAD_CHAINS.get(state) ?? [];
}

/** Snapshot every `<Road>` for the junction detector. */
export function collectRoadJunctionInputs(state: State): RoadJunctionInput[] {
  const out: RoadJunctionInput[] = [];
  for (const eid of roadQuery(state.world)) {
    const data = getRoadData(state, eid);
    if (!data || data.path.length < 4) continue;
    const width = Road.width[eid] || 2;
    out.push({
      eid,
      path: data.path,
      width,
      widths: data.widths,
      edgeFeather: Road.edgeFeather[eid],
      textureUrl: data.textureUrl,
      normalMapUrl: data.normalMapUrl,
      textureScale: Road.textureScale[eid] || 16,
    });
  }
  return out;
}

function fusionPlanFor(
  state: State,
  eid: number,
  width: number
): RoadFusionPlan {
  const inputs = collectRoadJunctionInputs(state);
  const junctions = detectRoadJunctions(inputs);
  return planRoadFusion(inputs, junctions).get(eid) ?? emptyFusionPlan(width);
}

/**
 * Tiny lift so the decal clears the coplanar mesh. polygonOffset finishes the
 * rest of the z-fight.
 */
const ROAD_DECAL_CLEARANCE = 0.04;

/**
 * How many coarser LOD lattices (halving res each step) the ribbon must also
 * clear on the **centerline**. Coarse chunk triangles cut above a carved bed
 * at T-junctions; without this the transparent decal loses the depth test and
 * sand shows through as an orange band on the chunk edge. Centerline only —
 * never max-neighborhood (that parked the ribbon on every sand ridge = stripes).
 */
const ROAD_LOD_CLEARANCE_LEVELS = 2;

/** Analytic bed raised to centerline mesh lattices (flatten / junction discs). */
function roadDecalHeightAtField(
  state: State,
  fieldEntity: number
): ((x: number, z: number) => number) | null {
  const fd = getTerrainContext(state).get(fieldEntity);
  if (!fd?.initialized || !fd.sampler.data) return null;
  const baseY = terrainBaseY(state, fieldEntity);
  const ox = fd.worldOffset.x;
  const oz = fd.worldOffset.z;
  const baseRes = Terrain.resolution[fieldEntity];
  const levels = Terrain.levels[fieldEntity];
  const sampler = fd.sampler;
  const density = fd.density;
  return (x, z) => {
    const lx = x - ox;
    const lz = z - oz;
    let y = sampleHeightAt(sampler, lx, lz);
    let res = meshSurfaceResolutionForPoint(baseRes, levels, density, lx, lz);
    for (let i = 0; i <= ROAD_LOD_CLEARANCE_LEVELS; i++) {
      const h = sampleMeshSurfaceHeight(sampler, lx, lz, res);
      if (h > y) y = h;
      res = Math.max(baseRes, Math.floor(res / 2));
    }
    return baseY + y + ROAD_DECAL_CLEARANCE;
  };
}

/**
 * Ribbon Y for flattened roads: carved analytic bed, raised to the centerline
 * mesh lattice when a coarser LOD chord sits above the bed. Non-flatten decals
 * still follow the rendered mesh surface.
 */
export function buildRoadHeightAt(
  state: State,
  eid: number,
  _spacing: number,
  _width: number
): (x: number, z: number) => number {
  if (Road.flatten[eid] === 1) {
    for (const [fe] of getTerrainContext(state)) {
      const fn = roadDecalHeightAtField(state, fe);
      if (fn) return fn;
    }
  }

  return (x, z) => {
    const y = sampleTerrainSurface(state, x, z, 0.5)?.worldY;
    return y !== undefined && Number.isFinite(y) ? y + ROAD_DECAL_CLEARANCE : 0;
  };
}

/**
 * How far each end reaches into the road it meets, and whether that end should
 * stay solid. A ribbon that fades out (end feather) right where a wider road
 * starts leaves a wedge of bare ground on the corner — exactly what reads as
 * "the road breaks where the width changes".
 */
export function roadJunctionEnds(
  state: State,
  eid: number,
  path: number[]
): { start: number; end: number } {
  const ends = { start: 0, end: 0 };
  if (path.length < 4) return ends;
  const n = path.length;
  const probes: Array<[number, 'start' | 'end']> = [
    [0, 'start'],
    [n - 2, 'end'],
  ];
  for (const other of roadQuery(state.world)) {
    if (other === eid) continue;
    const data = getRoadData(state, other);
    if (!data || data.path.length < 4) continue;
    const otherHalf = (Road.width[other] || 2) / 2;
    for (const [i, key] of probes) {
      const d = distanceToPolyline(data.path, path[i]!, path[i + 1]!);
      // Touching means the end sits on the neighbour's carriageway (a little
      // slack for authored polylines that stop just short of the centerline).
      if (d > otherHalf + 1.5) continue;
      const reach = Math.max(otherHalf - d, 0) + 0.75;
      if (reach > ends[key]) ends[key] = reach;
    }
  }
  return ends;
}

/** Phase A only — used by absorbed chain members that do not paint a ribbon. */
function carveRoadBed(
  state: State,
  eid: number,
  path: number[],
  width: number
): void {
  for (const [fe, fd] of getTerrainContext(state)) {
    if (!fd.initialized || !fd.sampler.data) continue;
    const localPath: number[] = new Array(path.length);
    for (let i = 0; i < path.length; i += 2) {
      localPath[i] = path[i]! - fd.worldOffset.x;
      localPath[i + 1] = path[i + 1]! - fd.worldOffset.z;
    }
    const falloff = Road.flattenFalloff[eid] || 8;
    const window = Road.flattenWindow[eid] || 56;
    const maxGrade = Number.isFinite(Road.flattenMaxGrade[eid])
      ? Road.flattenMaxGrade[eid]
      : 0.22;
    // Prep corridor must match sampler lattice (see minEffectiveWidth) or the
    // density boost densifies a bed that was never written.
    const bedWidth = minEffectiveWidth(
      fd.sampler,
      width + ROADBED_OVERHANG,
      1.5
    );
    const changed = carveRoadCorridor(fd.sampler, {
      path: localPath,
      width: bedWidth,
      falloff,
      window,
      maxGrade,
    });
    if (fd.density) {
      // Shared corridor density + leaf pad (same contract as rivers/lakes).
      const levels = Math.max(1, Terrain.levels[fe] || 1);
      const worldSize = Terrain.worldSize[fe] || fd.sampler.worldSize;
      const reach = bedWidth / 2 + Math.max(falloff, bedWidth / 2);
      applyCorridorDensity(
        fd.density,
        localPath,
        reach,
        255,
        densityLeafPad(worldSize, levels)
      );
      refreshChunkResolutions(state, fe, fd);
    }
    if (changed) rebuildTerrainDerivatives(state, fe, fd);
    let minX = Infinity;
    let maxX = -Infinity;
    let minZ = Infinity;
    let maxZ = -Infinity;
    const brushReach = bedWidth / 2 + Math.max(falloff, bedWidth / 2);
    for (let i = 0; i < localPath.length; i += 2) {
      const px = localPath[i]!;
      const pz = localPath[i + 1]!;
      minX = Math.min(minX, px - brushReach);
      maxX = Math.max(maxX, px + brushReach);
      minZ = Math.min(minZ, pz - brushReach);
      maxZ = Math.max(maxZ, pz + brushReach);
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
    break;
  }
}

/**
 * Grade the bed (when `flatten`) and build the ribbon geometry for one road.
 * Returns `null` while the terrain sampler is still decoding — callers retry.
 * Absorbed chain members return `null` after carving (leader paints the stitch).
 * Runs on first apply *and* on every regrade triggered by a later carve.
 */
function buildRoadGeometry(
  state: State,
  eid: number,
  data: NonNullable<ReturnType<typeof getRoadData>>,
  regrade: boolean
): THREE.BufferGeometry | null {
  // Gate: build only once the terrain heightmap is DECODED (initialized &&
  // sampler.data — same gate as lakes; before that the terrain answers with a
  // flat sampler at 0 and the road would end up buried). The <Terrain> entity
  // may not exist in the first ticks — its absence is only conclusive after a
  // grace period; worlds genuinely without terrain build flat at y=0.
  let samplerReady = false;
  for (const fd of getTerrainContext(state).values()) {
    if (fd.initialized && fd.sampler.data) {
      samplerReady = true;
      break;
    }
  }
  const width = Road.width[eid] || 2;
  const authoredWidths =
    data.widths && data.widths.length === data.path.length / 2
      ? data.widths
      : undefined;
  const spacing = Road.stationSpacing[eid] || 0.35;

  let heightAt: (x: number, z: number) => number;
  if (samplerReady) {
    heightAt = buildRoadHeightAt(state, eid, spacing, width);
  } else {
    const terrainExists = terrainQuery(state.world).length > 0;
    if (terrainExists || state.time.elapsed < 2) return null;
    heightAt = () => 0;
  }
  const iterations = Road.smoothing[eid];
  const role = chainRoleFor(cachedChains(state), eid);
  // Absorbed: leader carves the full stitched corridor. A second carve here
  // re-sampled its own noisy profile and rewrote bumps into the shared bed.
  if (role.role === 'absorbed') {
    return null;
  }

  const fusion = fusionPlanFor(state, eid, width);
  const legacy = roadJunctionEnds(state, eid, data.path);
  const startExtend = fusion.startSolid
    ? Math.max(fusion.startExtend, legacy.start)
    : legacy.start;
  const endExtend = fusion.endSolid
    ? Math.max(fusion.endExtend, legacy.end)
    : legacy.end;

  // Leader of a stitch: one continuous polyline + soft width across joins.
  let authoredPath =
    role.role === 'leader' && role.chain ? role.chain.path : data.path;
  let stitchWidthAt: ((arc: number, totalLen: number) => number) | undefined;
  let stitchFeather = Road.edgeFeather[eid];
  if (role.role === 'leader' && role.chain) {
    stitchWidthAt = makeWidthAtFromVertexWidths(
      role.chain.path,
      role.chain.widths,
      Math.max(role.chain.widths.reduce((a, b) => Math.max(a, b), 0) * 1.2, 6)
    );
    stitchFeather = role.chain.edgeFeather;
    // Chain termini only — no tip extend (would recreate the old wedge spike).
  } else if (authoredWidths) {
    stitchWidthAt = makeWidthAtFromVertexWidths(
      data.path,
      authoredWidths,
      Math.max(width * 1.2, 4)
    );
  }

  let path = resampleRoadPath(
    retractPathEnds(
      extendPathEnds(
        smoothPath(authoredPath, iterations),
        role.role === 'leader' ? 0 : startExtend,
        role.role === 'leader' ? 0 : endExtend
      ),
      fusion.startRetract,
      fusion.endRetract
    ),
    spacing
  );

  const paintWidth =
    role.role === 'leader' && role.chain
      ? role.chain.widths.reduce((a, b) => Math.max(a, b), width)
      : authoredWidths
        ? authoredWidths.reduce((a, b) => Math.max(a, b), width)
        : width;

  // Phase A — prepare roadbed (once). Stitched leader carves the full chain.
  if (regrade && Road.flatten[eid] === 1) {
    carveRoadBed(state, eid, path, paintWidth);
    heightAt = buildRoadHeightAt(state, eid, spacing, paintWidth);
  }

  // After carve, densify so chords hug the walk sampler (kills sand
  // wedges) without lifting verts above CCT height.
  if (samplerReady) {
    heightAt = buildRoadHeightAt(state, eid, spacing, paintWidth);
    path = densifyPathByHeight(path, heightAt, 0.02, 5);
  }

  const yOffset = Number.isFinite(Road.yOffset[eid]) ? Road.yOffset[eid] : 0;
  const widthAt = stitchWidthAt ?? makeFusionWidthAt(width, fusion);
  const texScale =
    role.role === 'leader' && role.chain
      ? role.chain.textureScale
      : Road.textureScale[eid] || 16;
  return makeRoadGeometry(path, {
    width: paintWidth,
    widthAt,
    textureScale: texScale,
    edgeFeather: stitchFeather,
    edgeNoise: Road.edgeNoise[eid],
    // Stitched chain: open termini may still fade; T-docks stay solid.
    endFeatherStart:
      role.role === 'leader'
        ? 0
        : fusion.startSolid || startExtend > 0
          ? 0
          : Road.endFeatherStart[eid],
    endFeatherEnd:
      role.role === 'leader'
        ? 0
        : fusion.endSolid || endExtend > 0
          ? 0
          : Road.endFeatherEnd[eid],
    yOffset,
    heightAt,
  });
}

function makeRoadDecalMaterial(
  state: State,
  eid: number,
  data: NonNullable<ReturnType<typeof getRoadData>>
): { material: THREE.MeshStandardMaterial; bias: number } {
  const bias = nextRoadBias(state);
  const material = new THREE.MeshStandardMaterial({
    color: data.textureUrl ? 0xffffff : 0x8a7a68,
    roughness: Road.roughness[eid] || 1,
    metalness: Road.metalness[eid],
    vertexColors: true,
    transparent: true,
    opacity: Road.opacity[eid] || 1,
    // depthWrite must stay false — transparent decals that write depth lose
    // the depth test against the terrain mesh and the whole road vanishes.
    depthWrite: false,
    depthTest: true,
    polygonOffset: true,
    polygonOffsetFactor: -2,
    polygonOffsetUnits: -2 - bias,
  });
  if (data.textureUrl) material.map = loadRoadTexture(data.textureUrl, true);
  if (data.normalMapUrl) {
    material.normalMap = loadRoadTexture(data.normalMapUrl, false);
  }
  if (data.roughnessMapUrl) {
    material.roughnessMap = loadRoadTexture(data.roughnessMapUrl, false);
  }
  return { material, bias };
}

function junctionSidecars(state: State): Map<string, JunctionSidecar> {
  let m = JUNCTION_SIDECARS.get(state);
  if (!m) {
    m = new Map();
    JUNCTION_SIDECARS.set(state, m);
  }
  return m;
}

/**
 * Crossing patches for junctions with ≥3 tip arms. End-to-end pairs stay
 * stitch-only (no disc stamp).
 */
function syncCrossingPatches(
  state: State,
  scene: THREE.Scene,
  junctions: RoadJunction[]
): void {
  const cars = junctionSidecars(state);
  const crosses = junctions.filter(
    (j) => j.arms.filter((a) => a.end !== 'through').length >= 3
  );
  const sig = crosses
    .map((j) => `${j.id}:${j.radius.toFixed(2)}:${j.maxWidth.toFixed(2)}`)
    .sort()
    .join('|');
  if (JUNCTION_PATCH_SIG.get(state) === sig && cars.size === crosses.length) {
    return;
  }
  JUNCTION_PATCH_SIG.set(state, sig);

  const keep = new Set(crosses.map((j) => j.id));
  for (const id of [...cars.keys()]) {
    if (!keep.has(id)) disposeJunction(state, id);
  }

  let heightAt: ((x: number, z: number) => number) | null = null;
  for (const [fe] of getTerrainContext(state)) {
    heightAt = roadDecalHeightAtField(state, fe);
    if (heightAt) break;
  }
  if (!heightAt) heightAt = () => 0;

  for (const j of crosses) {
    if (cars.has(j.id)) continue;
    const radius = Math.max(j.maxWidth * 0.55, j.radius * 0.7);
    const feather = Math.max(0.35, Math.min(j.feather, radius * 0.45));
    const geometry = makeJunctionGeometry(j.x, j.z, {
      radius,
      feather,
      textureScale: j.textureScale || 16,
      heightAt,
      clearance: 0.02,
      segments: 24,
    });
    const material = new THREE.MeshStandardMaterial({
      color: j.textureUrl ? 0xffffff : 0x8a7a68,
      roughness: 1,
      metalness: 0,
      vertexColors: true,
      transparent: true,
      opacity: 1,
      depthWrite: false,
      depthTest: true,
      polygonOffset: true,
      polygonOffsetFactor: -3,
      polygonOffsetUnits: -4,
    });
    if (j.textureUrl) material.map = loadRoadTexture(j.textureUrl, true);
    if (j.normalMapUrl) {
      material.normalMap = loadRoadTexture(j.normalMapUrl, false);
    }
    const mesh = new THREE.Mesh(geometry, material);
    mesh.name = `RoadCrossing:${j.id}`;
    mesh.castShadow = false;
    mesh.receiveShadow = true;
    mesh.frustumCulled = false;
    setupCsmMaterials(state, mesh);
    scene.add(mesh);
    cars.set(j.id, { mesh, material, id: j.id });
  }
}

/**
 * Rebuild end-to-end stitch chains + crossing patches. Absorbed members do not
 * paint — the leader paints one continuous carriageway with soft width lerp.
 */
function syncRoadFusion(state: State, scene: THREE.Scene): void {
  const inputs = collectRoadJunctionInputs(state);
  if (inputs.length === 0) {
    ROAD_CHAINS.set(state, []);
    disposeAllJunctionDiscs(state);
    JUNCTION_PATCH_SIG.delete(state);
    return;
  }
  const junctions = detectRoadJunctions(inputs);
  const chains = stitchEndToEndChains(inputs, junctions);
  const sig = `${junctionNetworkSignature(junctions)}#c${chains
    .map((c) => `${c.leaderEid}:${c.memberEids.join('+')}`)
    .sort()
    .join('|')}`;
  const prev = FUSION_SIG.get(state);
  ROAD_CHAINS.set(state, chains);
  syncCrossingPatches(state, scene, junctions);
  if (prev === sig) return;
  FUSION_SIG.set(state, sig);

  const dirty = roadDirty(state);
  const cars = roadSidecars(state);
  const absorbed = new Set<number>();
  const regrade = roadNeedRegrade(state);
  for (const c of chains) {
    dirty.add(c.leaderEid);
    // Stitch path is longer than any solo carve — must regrade the full bed.
    regrade.add(c.leaderEid);
    for (const m of c.memberEids) {
      if (m === c.leaderEid) continue;
      absorbed.add(m);
      disposeRoadMeshOnly(state, m);
    }
  }
  // T / cross tips need re-pave when the graph changes (flare + solid dock).
  const plans = planRoadFusion(inputs, junctions);
  for (const [eid, plan] of plans) {
    if (absorbed.has(eid)) continue;
    if (!plan.startSolid && !plan.endSolid) continue;
    if (cars.has(eid) || Road.applied[eid] === 1) dirty.add(eid);
  }
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

    let building = -1;
    hookGroundMutations(state, () => building);

    // Chains before paint — leader needs the stitched path on first apply.
    syncRoadFusion(state, scene);

    for (const eid of roadQuery(state.world)) {
      if (Road.applied[eid] === 1) continue;
      const data = getRoadData(state, eid);
      if (!data || data.path.length < 4) {
        Road.applied[eid] = 1;
        continue;
      }

      const role = chainRoleFor(cachedChains(state), eid);
      building = eid;
      const geometry = buildRoadGeometry(state, eid, data, true);
      building = -1;
      if (!geometry) {
        // Absorbed: leader owns the bed+ribbon. Sampler-not-ready: retry.
        if (role.role === 'absorbed') {
          Road.applied[eid] = 1;
          state.onDestroy(eid, () => disposeRoad(state, eid));
        }
        continue;
      }

      // Blended decal with depth write: without it, terrain chunk cracks
      // (rock-tint + AO on steep skirt/T-junction faces) show through as a
      // dark terracotta band. `alphaTest` still avoided — hard chewed edges.
      const { material, bias } = makeRoadDecalMaterial(state, eid, data);
      const mesh = new THREE.Mesh(geometry, material);
      mesh.castShadow = false;
      mesh.receiveShadow = true;
      mesh.renderOrder = 1 + bias;
      scene.add(mesh);
      setupCsmMaterials(state, mesh);

      roadSidecars(state).set(eid, { mesh, material });
      state.onDestroy(eid, () => disposeRoad(state, eid));
      Road.applied[eid] = 1;
      FUSION_SIG.delete(state);
    }

    // Recompute chains after new roads join the network.
    syncRoadFusion(state, scene);

    // Re-pave ribbons whose ground moved; re-carve when stitch topology changed.
    const dirty = roadDirty(state);
    if (dirty.size === 0) return;
    const cars = roadSidecars(state);
    const needRegrade = roadNeedRegrade(state);
    for (const eid of [...dirty]) {
      dirty.delete(eid);
      const data = getRoadData(state, eid);
      if (!data || data.path.length < 4) continue;
      const role = chainRoleFor(cachedChains(state), eid);
      if (role.role === 'absorbed') {
        disposeRoadMeshOnly(state, eid);
        continue;
      }
      const doRegrade = needRegrade.has(eid);
      needRegrade.delete(eid);
      const geometry = buildRoadGeometry(state, eid, data, doRegrade);
      if (!geometry) continue;
      let car = cars.get(eid);
      if (!car) {
        // Leader was absorbed before / never meshed — create ribbon now.
        const { material, bias } = makeRoadDecalMaterial(state, eid, data);
        const mesh = new THREE.Mesh(geometry, material);
        mesh.castShadow = false;
        mesh.receiveShadow = true;
        mesh.renderOrder = 1 + bias;
        scene.add(mesh);
        setupCsmMaterials(state, mesh);
        cars.set(eid, { mesh, material });
        state.onDestroy(eid, () => disposeRoad(state, eid));
        Road.applied[eid] = 1;
        continue;
      }
      car.mesh.geometry.dispose();
      car.mesh.geometry = geometry;
    }
  },
  dispose(state: State) {
    const cars = ROAD_SIDECARS.get(state);
    if (cars) {
      for (const eid of [...cars.keys()]) disposeRoad(state, eid);
    }
    disposeAllJunctionDiscs(state);
    FUSION_SIG.delete(state);
    ROAD_CHAINS.delete(state);
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
