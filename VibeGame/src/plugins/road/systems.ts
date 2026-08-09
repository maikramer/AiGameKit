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
import { setTransformYawRadians } from '../transforms/utils';
import {
  bridgeApproachStubs,
  carveBridgeDeckClearance,
  carveRoadApproaches,
  carveRoadCorridor,
  effectiveBridgeApproachMeters,
  BRIDGE_APPROACH_METERS,
  BRIDGE_CLEARANCE_WIDTH_BONUS,
  BRIDGE_INTO_SPAN_METERS,
  BRIDGE_LANDWARD_METERS,
  BRIDGE_RIBBON_CLEARANCE,
} from './carve';
import { waterPreserveZonesLocal } from './water-guard';
import {
  BRIDGE_BANK_ABOVE_CHANNEL,
  BRIDGE_DECK_LOCAL_Y,
  BRIDGE_NATIVE_SPAN_M,
  BRIDGE_TIP_EMBED_M,
  bridgeSpanScaleX,
  bridgeYawDeg,
  chooseBridgeLip,
  deckContourAt,
  pathArcFraction,
  pickSolidBankY,
  planDeckOriginY,
  type BridgeDeckContour,
} from './bridge';
import { probeDeckLocalContour } from './bridge-deck';
import { bridgeDeckCenterXZ } from './river-crossing';
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
import { createEntityFromRecipe } from '../../core/recipes/parser';
import { logger } from '../../core/utils/logger';
import { GltfLod, GltfPending } from '../gltf-xml/components';
import { setGltfLodUrls, setGltfUrl } from '../gltf-xml/context';
import { getGltfRootGroup } from '../gltf-xml/group-registry';
import { Rigidbody } from '../physics/components';
import { syncBodyQuaternionFromEuler } from '../physics/utils';
import { RiverApplySystem } from '../water';

/** Re-export mesh native span for callers / tests. */
export { BRIDGE_NATIVE_SPAN_M } from './bridge';
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
/** Bridge deck GameObject eid spawned from a bridge Road. */
const BRIDGE_DECK_EIDS = new WeakMap<State, Map<number, number>>();
/** Last fusion-graph signature — rebuild stitched ribbons when network changes. */
const FUSION_SIG = new WeakMap<State, string>();
/**
 * Cheap topology key (`roadCount:gen`) recorded after a successful fusion pass.
 * Avoids re-running junction detect / stitch / string sig every frame when the
 * network membership has not changed.
 */
const FUSION_CHEAP = new WeakMap<State, string>();
/** Bumped on road apply / dispose so fusion knows membership changed. */
const ROAD_TOPO_GEN = new WeakMap<State, number>();
const ROAD_CHAINS = new WeakMap<State, StitchedRoadChain[]>();
const JUNCTION_PATCH_SIG = new WeakMap<State, string>();

/**
 * Draw-order bias handed out per ribbon. City streets overlap (a plaza street
 * crossing the main road), and two coplanar decals with identical depth bias
 * flicker as the camera moves. A unique bias per road makes the winner stable.
 */
const ROAD_BIAS = new WeakMap<State, number>();

function bridgeDeckEids(state: State): Map<number, number> {
  let m = BRIDGE_DECK_EIDS.get(state);
  if (!m) {
    m = new Map();
    BRIDGE_DECK_EIDS.set(state, m);
  }
  return m;
}
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

function bumpRoadTopology(state: State): void {
  ROAD_TOPO_GEN.set(state, (ROAD_TOPO_GEN.get(state) ?? 0) + 1);
  FUSION_CHEAP.delete(state);
}

function cheapFusionKey(state: State): string {
  let n = 0;
  for (const _ of roadQuery(state.world)) n++;
  return `${n}:${ROAD_TOPO_GEN.get(state) ?? 0}`;
}

function hasUnseatedBridgeDeck(state: State): boolean {
  const seated = bridgeDeckSeated(state);
  for (const [, deckEid] of bridgeDeckEids(state)) {
    if (state.exists(deckEid) && !seated.has(deckEid)) return true;
  }
  return false;
}

/** True when ribbons, fusion, and bridge decks need no work this frame. */
function isRoadApplyIdle(state: State): boolean {
  if (roadDirty(state).size > 0) return false;
  const gen = ROAD_TOPO_GEN.get(state) ?? 0;
  let n = 0;
  for (const eid of roadQuery(state.world)) {
    n++;
    if (Road.applied[eid] !== 1) return false;
  }
  if (FUSION_CHEAP.get(state) !== `${n}:${gen}`) return false;
  return !hasUnseatedBridgeDeck(state);
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
  if (car) {
    car.mesh.removeFromParent();
    car.mesh.geometry.dispose();
    car.material.dispose(); // texturas são cache partilhado — não descartar
    cars!.delete(eid);
  }
  const deckEid = bridgeDeckEids(state).get(eid);
  if (deckEid !== undefined) {
    bridgeDeckEids(state).delete(eid);
    bridgeDeckSeated(state).delete(deckEid);
    state.destroyEntity(deckEid);
  }
  deleteRoadData(state, eid);
  // Network changed — next frame rebuilds fusion discs + neighbour docks.
  FUSION_SIG.delete(state);
  bumpRoadTopology(state);
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
      bridge: Road.bridge[eid] === 1,
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
 * How many coarser LOD lattices (halving res each step) the ribbon also clears
 * on the **centerline**. A chunk whose neighbour sits one level up cuts its
 * triangles above the carved bed at the seam; the transparent decal then loses
 * the depth test and sand shows through as an orange band on the chunk edge.
 * One level only — corridors force the quadtree down to the deepest leaf
 * (`densityNeedsDeeperSplit`), so nothing coarser than the neighbour is ever
 * rendered over a road.
 */
const ROAD_LOD_CLEARANCE_LEVELS = 1;

/**
 * Ceiling on the seam lift (m). Clearing a coarser quad is a centimetre problem
 * on a graded bed, but a quad chord on a mountainside runs metres above the
 * lattice below it: unclamped, the ribbon floated ~7 m over the ground the
 * player walks on (and, on a bridge, above the deck itself). Capping trades a
 * sliver of paint hidden by a distant coarse chunk for a road that always meets
 * the ground.
 *
 * The cap **is** the float on any sustained slope: the coarser lattice runs
 * 0.5-0.8 m above the rendered one for the whole of a climb, so the lift
 * saturates and the ribbon rides the cap. Measured on the simple-rpg west
 * artery (x -195..-227) with the old 0.3 m: the hero stood 0.26-0.28 m below
 * the cobble the whole way up — legs sunk into the road. The chunk the player
 * stands on is always the corridor-boosted leaf (its collider is built from the
 * same lattice this samples), so a coarser neighbour can only cut over the
 * paint at a chunk seam; a couple of centimetres is all that case needs.
 */
const ROAD_LOD_CLEARANCE_MAX_M = 0.06;

/**
 * Bed on the **rendered** chunk lattice (flatten / junction discs). The bilinear
 * heightmap surface is not what the player stands on: a 4096-texel field runs up
 * to ~1.5 m off the 7-8 m chunk quads on a mountainside, either way, so an
 * analytic ribbon floats on ridges and sinks in hollows. Sampling the same
 * lattice as the mesh (and as the heightfield collider) keeps the paint on the
 * surface; the seam lift only clears a neighbour one level coarser.
 */
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
    let res = meshSurfaceResolutionForPoint(baseRes, levels, density, lx, lz);
    const rendered = sampleMeshSurfaceHeight(sampler, lx, lz, res);
    let coarse = rendered;
    for (let i = 0; i < ROAD_LOD_CLEARANCE_LEVELS; i++) {
      res = Math.max(baseRes, Math.floor(res / 2));
      const h = sampleMeshSurfaceHeight(sampler, lx, lz, res);
      if (h > coarse) coarse = h;
    }
    const lift = Math.min(coarse - rendered, ROAD_LOD_CLEARANCE_MAX_M);
    return baseY + rendered + Math.max(lift, 0) + ROAD_DECAL_CLEARANCE;
  };
}

/** Terrain decal height of the first ready field (bridge ribbon fallback). */
function bridgeGroundHeightAt(
  state: State
): ((x: number, z: number) => number) | null {
  for (const [fe] of getTerrainContext(state)) {
    const fn = roadDecalHeightAtField(state, fe);
    if (fn) return fn;
  }
  return null;
}

/**
 * Analytic field height → world Y (no LOD mesh max). LOD clearance inflates
 * bank lips above the visible pad and parks the deck in the air.
 */
function sampleBridgeBankAnalyticY(state: State, x: number, z: number): number {
  for (const [fe, fd] of getTerrainContext(state)) {
    if (!fd.initialized || !fd.sampler.data) continue;
    const lx = x - fd.worldOffset.x;
    const lz = z - fd.worldOffset.z;
    return terrainBaseY(state, fe) + sampleHeightAt(fd.sampler, lx, lz);
  }
  const y = sampleTerrainSurface(state, x, z, 0.75)?.worldY;
  return y !== undefined && Number.isFinite(y) ? y : 0;
}

/**
 * Sample solid ground **behind** each Way (away from the span). Several depths
 * landward + the Way itself; {@link pickSolidBankY} keeps the lowest solid
 * sample so an arterial flatten spike cannot bury the opposite abutment.
 */
function bridgeLandwardBankYs(state: State, path: number[]): [number, number] {
  const x0 = path[0]!;
  const z0 = path[1]!;
  const x1 = path[path.length - 2]!;
  const z1 = path[path.length - 1]!;
  const dx = x1 - x0;
  const dz = z1 - z0;
  const len = Math.hypot(dx, dz);
  const midY = sampleBridgeMidChannelY(state, path);
  if (len < 1e-6) {
    const y = sampleBridgeBankAnalyticY(state, x0, z0);
    return [y, y];
  }
  const ux = dx / len;
  const uz = dz / len;
  const back = BRIDGE_LANDWARD_METERS;
  const depths = [back, back * 0.5, 0];
  const samples0 = depths.map((d) =>
    sampleBridgeBankAnalyticY(state, x0 - ux * d, z0 - uz * d)
  );
  const samples1 = depths.map((d) =>
    sampleBridgeBankAnalyticY(state, x1 + ux * d, z1 + uz * d)
  );
  return [pickSolidBankY(samples0, midY), pickSolidBankY(samples1, midY)];
}

/**
 * Mid-span analytic Y (river bed after River carve). Used so lip choice never
 * stamps a raise that would plug the channel.
 */
function sampleBridgeMidChannelY(state: State, path: number[]): number {
  const { x, z } = bridgeDeckCenterXZ(state, path);
  return sampleBridgeBankAnalyticY(state, x, z);
}

/**
 * Resolve shared lip from landward banks + mid-channel (post pad/river).
 * Prefers lowering the high bank — see {@link chooseBridgeLip}.
 */
export function resolveBridgeDeckY(state: State, path: number[]): number {
  if (path.length < 4) return 0;
  const [a, b] = bridgeLandwardBankYs(state, path);
  return chooseBridgeLip(a, b, sampleBridgeMidChannelY(state, path)).lip;
}

/** Write deckY0/deckY1/deckY SOA for a bridge road. */
export function applyBridgeDeckHeights(
  state: State,
  eid: number,
  path: number[]
): void {
  if (path.length < 4) return;
  const [y0, y1] = bridgeLandwardBankYs(state, path);
  const midY = sampleBridgeMidChannelY(state, path);
  const plan = chooseBridgeLip(y0, y1, midY);
  Road.deckY0[eid] = plan.lip;
  Road.deckY1[eid] = plan.lip;
  Road.deckY[eid] = plan.lip;
  logger.info(
    `[Road] bridge lip eid=${eid} strategy=${plan.strategy} lip=${plan.lip.toFixed(2)} banks=${y0.toFixed(2)}/${y1.toFixed(2)} mid=${midY.toFixed(2)}`
  );
}

/**
 * World Y of the entity origin before the deck mesh has loaded: assume the
 * topmost geometry ({@link BRIDGE_DECK_LOCAL_Y}) sits on the lip. One frame of
 * approximation — {@link seatBridgeDeck} re-seats on the probed contour.
 */
export function bridgeDeckSpawnY(eid: number): number {
  const lip = Road.deckY[eid];
  const y = Number.isFinite(lip) ? lip : 0;
  return y - BRIDGE_TIP_EMBED_M - BRIDGE_DECK_LOCAL_Y;
}

/** Deck walk surface offsets from the entity origin, probed once per deck. */
const BRIDGE_DECK_CONTOUR = new WeakMap<
  State,
  Map<number, BridgeDeckContour>
>();

function bridgeDeckContours(state: State): Map<number, BridgeDeckContour> {
  let m = BRIDGE_DECK_CONTOUR.get(state);
  if (!m) {
    m = new Map();
    BRIDGE_DECK_CONTOUR.set(state, m);
  }
  return m;
}

/**
 * Walk surface of a seated bridge deck in world Y, or null while the mesh is
 * still loading. Read by the ribbon and by the terrain clearance cut so both
 * agree on where the deck actually is.
 */
export function bridgeDeckWorldContour(
  state: State,
  roadEid: number
): BridgeDeckContour | null {
  const deckEid = bridgeDeckEids(state).get(roadEid);
  if (deckEid === undefined || !state.exists(deckEid)) return null;
  const local = bridgeDeckContours(state).get(deckEid);
  if (!local) return null;
  const originY = Transform.posY[deckEid];
  return local.map((y) => y + originY);
}

/**
 * Deck origin Y from the cached contour and the current lip, or null while the
 * mesh has not been probed. Pure arithmetic: a later carve that moves the lip
 * re-seats the deck without another raycast pass.
 */
function bridgeDeckSeatY(
  state: State,
  roadEid: number,
  deckEid: number
): number | null {
  const local = bridgeDeckContours(state).get(deckEid);
  if (!local) return null;
  const lip = Number.isFinite(Road.deckY[roadEid]) ? Road.deckY[roadEid] : 0;
  return planDeckOriginY(local, lip);
}

/**
 * Move an already-probed deck onto the current lip. The ribbon samples the deck
 * contour, so it has to run **before** the cobble is rebuilt: a regrade that
 * lowers the lip (west artery: 37.19 → 36.85 once the pads finished carving)
 * used to re-seat the deck only in {@link spawnBridgeDeck}, which the apply loop
 * calls *after* the geometry — the cobble stayed on the old lip and floated the
 * whole lip delta (0.34 m) over the stone, legs sunk into the span.
 *
 * No-op while the mesh is still loading (no contour yet) — {@link seatBridgeDeck}
 * seats it on load and marks the road dirty.
 */
function reseatBridgeDeckToLip(state: State, roadEid: number): void {
  const deckEid = bridgeDeckEids(state).get(roadEid);
  if (deckEid === undefined || !state.exists(deckEid)) return;
  if (!bridgeDeckSeated(state).has(deckEid)) return;
  const seatY = bridgeDeckSeatY(state, roadEid, deckEid);
  if (seatY === null || Transform.posY[deckEid] === seatY) return;
  Transform.posY[deckEid] = seatY;
  Transform.dirty[deckEid] = 1;
  if (state.hasComponent(deckEid, Rigidbody)) {
    Rigidbody.posY[deckEid] = seatY;
    Rigidbody.poseDirty[deckEid] = 1;
  }
}

const BRIDGE_DECK_SEATED = new WeakMap<State, Set<number>>();

function bridgeDeckSeated(state: State): Set<number> {
  let s = BRIDGE_DECK_SEATED.get(state);
  if (!s) {
    s = new Set();
    BRIDGE_DECK_SEATED.set(state, s);
  }
  return s;
}

/**
 * After GLTF load: centre the deck on the span and drop it so the abutment
 * tips sit {@link BRIDGE_TIP_EMBED_M} under the bank lip. Seating on the AABB
 * top instead parks the parapet crown at bank level and buries the whole ramp.
 *
 * Returns true once the deck is seated (contour cached, transform final).
 */
function seatBridgeDeck(
  state: State,
  roadEid: number,
  deckEid: number,
  path: number[]
): boolean {
  if (GltfPending.loaded[deckEid] !== 1) return false;
  if (bridgeDeckSeated(state).has(deckEid)) return true;
  const group = getGltfRootGroup(state, deckEid);
  if (!group) return false;
  group.updateWorldMatrix(true, true);
  const box = new THREE.Box3().setFromObject(group);
  if (box.isEmpty()) return false;

  // Centre XZ on the river centreline (not Ways mid — can sit off-channel).
  const { x: mx, z: mz } = bridgeDeckCenterXZ(state, path);
  const cx = (box.min.x + box.max.x) * 0.5;
  const cz = (box.min.z + box.max.z) * 0.5;
  Transform.posX[deckEid] += mx - cx;
  Transform.posZ[deckEid] += mz - cz;
  Transform.dirty[deckEid] = 1;

  const contour = probeDeckLocalContour(group, path, Transform.posY[deckEid]);
  const lip = Number.isFinite(Road.deckY[roadEid]) ? Road.deckY[roadEid] : 0;
  if (contour) {
    bridgeDeckContours(state).set(deckEid, contour);
    Transform.posY[deckEid] = planDeckOriginY(contour, lip);
  } else {
    // No lane hit (degenerate mesh): fall back to the AABB top on the lip.
    Transform.posY[deckEid] += lip - BRIDGE_TIP_EMBED_M - box.max.y;
  }
  if (state.hasComponent(deckEid, Rigidbody)) {
    Rigidbody.posX[deckEid] = Transform.posX[deckEid];
    Rigidbody.posY[deckEid] = Transform.posY[deckEid];
    Rigidbody.posZ[deckEid] = Transform.posZ[deckEid];
    Rigidbody.poseDirty[deckEid] = 1;
  }
  bridgeDeckSeated(state).add(deckEid);
  if (contour) {
    const world = contour.map((y) => y + Transform.posY[deckEid]);
    logger.info(
      `[Road] bridge deck seated eid=${deckEid} lip=${lip.toFixed(2)} tips=${world[0]!.toFixed(2)}/${world[world.length - 1]!.toFixed(2)} crown=${Math.max(...world).toFixed(2)}`
    );
  }
  return true;
}

/**
 * Ribbon Y for flattened roads: carved analytic bed, raised to the centerline
 * mesh lattice when a coarser LOD chord sits above the bed.
 *
 * Bridges follow the **probed deck contour** — ramps included — so the cobble
 * climbs onto the deck instead of a flat plane forcing the whole span down to
 * bank level. Where the deck is still under ground (the embedded tips) the
 * ribbon falls back to the terrain so the paint never disappears.
 */
export function buildRoadHeightAt(
  state: State,
  eid: number,
  _spacing: number,
  _width: number
): (x: number, z: number) => number {
  if (Road.bridge[eid] === 1) {
    const data = getRoadData(state, eid);
    const contour = bridgeDeckWorldContour(state, eid);
    const groundAt = bridgeGroundHeightAt(state);
    if (contour && data && data.path.length >= 4) {
      const path = data.path;
      return (x, z) => {
        const deck =
          deckContourAt(contour, pathArcFraction(path, x, z)) +
          BRIDGE_RIBBON_CLEARANCE;
        const ground = groundAt?.(x, z);
        return ground !== undefined && ground > deck ? ground : deck;
      };
    }
    // Pre-load: keep the ribbon on the lip so it is never buried mid-span.
    const lip = Number.isFinite(Road.deckY[eid]) ? Road.deckY[eid] : 0;
    return () => lip + BRIDGE_RIBBON_CLEARANCE;
  }
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
  const isBridge = Road.bridge[eid] === 1;
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
    const baseY = terrainBaseY(state, fe);
    // Lake/river carve first. Arteries: skip wet waterline + no-raise floor so
    // blend cannot re-fill bowls. Bridges: only noRaiseBelowY — preserve discs
    // would skip bank tips that must terrace up to the deck lip (south gap).
    const waterZones = waterPreserveZonesLocal(
      state,
      localPath,
      bedWidth * 0.5 + falloff,
      fd.worldOffset,
      baseY
    );
    const corridorOpts = {
      path: localPath,
      width: bedWidth,
      falloff,
      window,
      maxGrade,
      noRaiseBelowY: waterZones.noRaiseBelowY,
      preserveDiscs: isBridge ? undefined : waterZones.discs,
      preserveRibbons: isBridge ? undefined : waterZones.ribbons,
    };
    // Bridge: landward stubs only when texel fine enough; coarse maps skip
    // (minEffectiveWidth would stamp a ~texel-wide sand plug over the river).
    const approachM = isBridge
      ? effectiveBridgeApproachMeters(fd.sampler, BRIDGE_APPROACH_METERS)
      : 0;
    const lipWorld = Road.deckY[eid];
    const flatTargetY =
      isBridge && Number.isFinite(lipWorld) ? lipWorld - baseY : undefined;
    let changed: boolean;
    if (isBridge) {
      // Approach seat: grade the landward stubs onto the deck plane. Texels
      // already in the channel may only be cut, never filled into the water.
      const midChannel = sampleBridgeMidChannelY(state, path);
      const channelFloor = Number.isFinite(midChannel)
        ? midChannel - baseY + BRIDGE_BANK_ABOVE_CHANNEL
        : undefined;
      const waterFloor = waterZones.noRaiseBelowY;
      const noRaiseBelowY =
        channelFloor !== undefined && waterFloor !== undefined
          ? Math.min(channelFloor, waterFloor)
          : (channelFloor ?? waterFloor);
      changed = carveRoadApproaches(fd.sampler, {
        ...corridorOpts,
        // No preserve discs here: abutments sit on the beach just outside the
        // shore line and must terrace up to the lip. Channel safety comes from
        // clamped approach falloff (bridgeApproachCorridorOpts) + noRaiseBelowY
        // + tiny into-span — not from skipping the whole waterline footprint.
        preserveDiscs: undefined,
        preserveRibbons: undefined,
        approachMeters: approachM,
        landwardMeters: BRIDGE_LANDWARD_METERS,
        intoSpanMeters: BRIDGE_INTO_SPAN_METERS,
        flatTargetY,
        noRaiseBelowY,
      });
      // Span clearance: cut whatever pokes through the deck so only the
      // abutment tips stay buried. Lower-only — the channel is never filled.
      const contour = bridgeDeckWorldContour(state, eid);
      if (contour) {
        const cleared = carveBridgeDeckClearance(fd.sampler, {
          path: localPath,
          width: width + BRIDGE_CLEARANCE_WIDTH_BONUS,
          falloff: Math.max(falloff * 0.35, 1.5),
          deckYAt: (u) => deckContourAt(contour, u) - baseY,
        });
        if (cleared) changed = true;
      }
    } else {
      changed = carveRoadCorridor(fd.sampler, corridorOpts);
    }
    if (isBridge && !changed) {
      // Coarse heightmap: approach carve skipped (would plug the river).
      // Deck seats on TerrainPad / analytic lip only.
      logger.info(
        `[Road] bridge eid=${eid} skip approach carve (coarse texel) lip=${lipWorld.toFixed(2)}`
      );
    }
    // Density + brush AABB. Bridges: landward stubs plus the span itself once
    // the deck contour is known — the clearance cut only shows up in the mesh
    // if the chunks under the abutments are refined to the carved lattice.
    const densityPaths =
      isBridge && !bridgeDeckWorldContour(state, eid)
        ? bridgeApproachStubs(
            localPath,
            BRIDGE_INTO_SPAN_METERS,
            BRIDGE_LANDWARD_METERS
          )
        : isBridge
          ? [
              localPath,
              ...bridgeApproachStubs(
                localPath,
                BRIDGE_INTO_SPAN_METERS,
                BRIDGE_LANDWARD_METERS
              ),
            ]
          : [localPath];
    if (fd.density) {
      const levels = Math.max(1, Terrain.levels[fe] || 1);
      const worldSize = Terrain.worldSize[fe] || fd.sampler.worldSize;
      const reach = bedWidth / 2 + Math.max(falloff, bedWidth / 2);
      const leafPad = densityLeafPad(worldSize, levels);
      for (const densPath of densityPaths) {
        applyCorridorDensity(fd.density, densPath, reach, 255, leafPad);
      }
      refreshChunkResolutions(state, fe, fd);
    }
    if (changed) rebuildTerrainDerivatives(state, fe, fd);
    let minX = Infinity;
    let maxX = -Infinity;
    let minZ = Infinity;
    let maxZ = -Infinity;
    const brushReach = bedWidth / 2 + Math.max(falloff, bedWidth / 2);
    for (const densPath of densityPaths) {
      for (let i = 0; i < densPath.length; i += 2) {
        const px = densPath[i]!;
        const pz = densPath[i + 1]!;
        minX = Math.min(minX, px - brushReach);
        maxX = Math.max(maxX, px + brushReach);
        minZ = Math.min(minZ, pz - brushReach);
        maxZ = Math.max(maxZ, pz + brushReach);
      }
    }
    if (Number.isFinite(minX)) {
      registerGroundBrush(state, {
        kind: 'road',
        minX,
        maxX,
        minZ,
        maxZ,
        // Brush path = stubs joined for bridges (queries stay bank-local).
        path:
          densityPaths.length === 1
            ? densityPaths[0]!.slice()
            : localPath.slice(),
        // Exclusion corridor = bed + talus: `avoid-road` spawners must stay
        // off the full carve footprint (leito + falloff walls), not just the
        // flat bed — props planted on the cut slope read as "tree on the
        // road" from the driver's seat. Matches brushReach used for the AABB.
        halfWidth: bedWidth / 2 + falloff,
      });
    }
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
    if (Road.bridge[eid] === 1) {
      applyBridgeDeckHeights(state, eid, data.path);
      reseatBridgeDeckToLip(state, eid);
    }
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
  // Bridge: resolve bank heights + approach-only flatten (profile flatten off).
  if (regrade && Road.bridge[eid] === 1) {
    applyBridgeDeckHeights(state, eid, authoredPath);
    carveRoadBed(state, eid, path, paintWidth);
    reseatBridgeDeckToLip(state, eid);
    heightAt = buildRoadHeightAt(state, eid, spacing, paintWidth);
  } else if (regrade && Road.flatten[eid] === 1) {
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
 *
 * Cheap key (`count:gen`) skips the full junction walk when membership is
 * unchanged; the expensive string sig still gates dirty-marking of leaders.
 */
function syncRoadFusion(state: State, scene: THREE.Scene): void {
  const cheap = cheapFusionKey(state);
  if (FUSION_CHEAP.get(state) === cheap) return;

  const inputs = collectRoadJunctionInputs(state);
  if (inputs.length === 0) {
    ROAD_CHAINS.set(state, []);
    disposeAllJunctionDiscs(state);
    JUNCTION_PATCH_SIG.delete(state);
    FUSION_SIG.delete(state);
    FUSION_CHEAP.set(state, cheap);
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
  FUSION_CHEAP.set(state, cheap);
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
 * Spawn or sync fixed GLB deck for a bridge Road. Mesh spans local +X; yaw
 * aligns +X to path A→B. Authored meshes are **centred** — entity Y is
 * lip−{@link BRIDGE_DECK_LOCAL_Y} as a pre-load estimate; {@link seatBridgeDeck}
 * drops it onto the probed contour after load. Collider matches the visual
 * (no `mesh-anchor: base` — that would put pier feet on the lip).
 *
 * Wires {@link GltfPending} + URL sidecar explicitly (no merge-recipe child) so
 * runtime spawn cannot silently skip the loader adapters.
 */
function spawnBridgeDeck(
  state: State,
  eid: number,
  data: NonNullable<ReturnType<typeof getRoadData>>
): void {
  if (Road.bridge[eid] !== 1) return;
  const url = data.bridgeUrl?.trim() || null;
  if (!url) {
    logger.warn(
      `[Road] bridge eid=${eid} missing bridge-url — cobble ribbon only, no GLB deck`
    );
    return;
  }

  const path = data.path;
  if (path.length < 4) return;
  const nativeSpan = data.bridgeNativeSpan ?? BRIDGE_NATIVE_SPAN_M;
  const scaleX = bridgeSpanScaleX(path, nativeSpan);
  if (scaleX < 0.05) return;

  const { x: mx, z: mz } = bridgeDeckCenterXZ(state, path);
  const deckY = bridgeDeckSpawnY(eid);
  const yawDeg = bridgeYawDeg(path);

  const existing = bridgeDeckEids(state).get(eid);
  if (existing !== undefined) {
    if (!state.exists(existing)) {
      bridgeDeckEids(state).delete(eid);
      bridgeDeckSeated(state).delete(existing);
    } else {
      // Regrade may have moved the lip: re-seat from the cached contour when
      // the deck is already probed, otherwise keep the pre-load estimate.
      const seatedY = bridgeDeckSeatY(state, eid, existing);
      Transform.posX[existing] = mx;
      Transform.posY[existing] = seatedY ?? deckY;
      Transform.posZ[existing] = mz;
      Transform.scaleX[existing] = scaleX;
      Transform.scaleY[existing] = 1;
      Transform.scaleZ[existing] = 1;
      setTransformYawRadians(Transform, existing, (yawDeg * Math.PI) / 180);
      Transform.dirty[existing] = 1;
      if (state.hasComponent(existing, Rigidbody)) {
        Rigidbody.posX[existing] = mx;
        Rigidbody.posY[existing] = Transform.posY[existing];
        Rigidbody.posZ[existing] = mz;
        Rigidbody.eulerX[existing] = Transform.eulerX[existing];
        Rigidbody.eulerY[existing] = Transform.eulerY[existing];
        Rigidbody.eulerZ[existing] = Transform.eulerZ[existing];
        syncBodyQuaternionFromEuler(existing);
        Rigidbody.poseDirty[existing] = 1;
      }
      seatBridgeDeck(state, eid, existing, path);
      return;
    }
  }

  const colUrl = (data.bridgeCollisionUrl ?? url).trim();
  // Do NOT use place= here: mid-span XZ samples the river bed and would yank
  // the deck down into the water. Entity Y accounts for centred mesh origin.
  const deckEid = createEntityFromRecipe(state, 'GameObject', {
    name: `road_bridge_${eid}`,
    transform: `pos: ${mx} ${deckY} ${mz}; rotation: 0 ${yawDeg} 0; scale: ${scaleX} 1 1`,
    rigidbody: 'type: fixed; mass: 0; gravity-scale: 0',
    collider: `shape: trimesh; mesh-url: ${colUrl}`,
  });

  // Explicit GLTF pending — same contract as <GLTFLoader url> merge, but
  // guaranteed at runtime (no dependency on processRecipeChildElements merge).
  if (!state.hasComponent(deckEid, GltfPending)) {
    state.addComponent(deckEid, GltfPending);
  }
  GltfPending.loaded[deckEid] = 0;
  setGltfUrl(state, deckEid, url);

  const lod1 = data.bridgeLod1Url?.trim() || null;
  const lod2 = data.bridgeLod2Url?.trim() || null;
  if (lod1 || lod2) {
    const mid = lod1 ?? url;
    const far = lod2 ?? mid;
    setGltfLodUrls(state, deckEid, [url, mid, far]);
    if (!state.hasComponent(deckEid, GltfLod)) {
      state.addComponent(deckEid, GltfLod);
    }
    GltfLod.thresholdNear[deckEid] = 70;
    GltfLod.thresholdMid[deckEid] = 160;
    GltfLod.activeLevel[deckEid] = 0;
    GltfLod.settled[deckEid] = 0;
  }

  // Physics bodies live in world space — without this mirror, Rapier spawns at
  // origin and the sync loop yanks Transform (visual) off the river span.
  if (state.hasComponent(deckEid, Rigidbody)) {
    Rigidbody.posX[deckEid] = Transform.posX[deckEid];
    Rigidbody.posY[deckEid] = Transform.posY[deckEid];
    Rigidbody.posZ[deckEid] = Transform.posZ[deckEid];
    Rigidbody.eulerX[deckEid] = Transform.eulerX[deckEid];
    Rigidbody.eulerY[deckEid] = Transform.eulerY[deckEid];
    Rigidbody.eulerZ[deckEid] = Transform.eulerZ[deckEid];
    syncBodyQuaternionFromEuler(deckEid);
    Rigidbody.poseDirty[deckEid] = 1;
  }

  bridgeDeckEids(state).set(eid, deckEid);
  seatBridgeDeck(state, eid, deckEid, path);
  logger.info(
    `[Road] bridge deck spawned eid=${deckEid} url=${url} at (${mx.toFixed(1)}, ${Transform.posY[deckEid].toFixed(1)}, ${mz.toFixed(1)}) scaleX=${scaleX.toFixed(2)} lip=${Road.deckY[eid].toFixed(2)}`
  );
}

/**
 * Constrói cada `<Road>` assim que a superfície do terreno está pronta
 * (depois dos TerrainPads e do River — a estrada / ponte tem de amostrar as
 * alturas pós-flatten e pós-carve do canal). Mundos sem terreno: y=0.
 */
export const RoadApplySystem: System = defineSystem({
  name: 'RoadApplySystem',
  group: 'setup',
  after: [TerrainPadApplySystem, RiverApplySystem],
  update(state: State) {
    if (state.headless) return;
    const scene = getScene(state);
    if (!scene) return;

    let building = -1;
    hookGroundMutations(state, () => building);

    // Steady-state network: skip fusion walk, pending sort, and deck polls.
    if (isRoadApplyIdle(state)) return;

    // Chains before paint — leader needs the stitched path on first apply.
    syncRoadFusion(state, scene);

    // Arteries first, bridges last — lip samples must see post-flatten banks
    // (otherwise a later artery spike buries one abutment).
    const pendingRoads: number[] = [];
    for (const eid of roadQuery(state.world)) {
      if (Road.applied[eid] !== 1) pendingRoads.push(eid);
    }
    let appliedAny = false;
    if (pendingRoads.length > 0) {
      pendingRoads.sort(
        (a, b) => (Road.bridge[a] || 0) - (Road.bridge[b] || 0)
      );
      for (const eid of pendingRoads) {
        const data = getRoadData(state, eid);
        if (!data || data.path.length < 4) {
          Road.applied[eid] = 1;
          appliedAny = true;
          bumpRoadTopology(state);
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
            appliedAny = true;
            bumpRoadTopology(state);
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
        spawnBridgeDeck(state, eid, data);
        state.onDestroy(eid, () => disposeRoad(state, eid));
        Road.applied[eid] = 1;
        appliedAny = true;
        bumpRoadTopology(state);
      }
    }

    // Recompute chains after new roads join the network.
    if (appliedAny) syncRoadFusion(state, scene);

    // Bridge GLBs load async — seat on the probed contour once the root exists,
    // then re-grade so the ribbon and the terrain cut use that contour.
    for (const [roadEid, deckEid] of bridgeDeckEids(state)) {
      if (!state.exists(deckEid)) continue;
      const data = getRoadData(state, roadEid);
      if (!data || data.path.length < 4) continue;
      if (bridgeDeckSeated(state).has(deckEid)) continue;
      if (!seatBridgeDeck(state, roadEid, deckEid, data.path)) continue;
      roadDirty(state).add(roadEid);
      roadNeedRegrade(state).add(roadEid);
    }

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
        spawnBridgeDeck(state, eid, data);
        continue;
      }
      car.mesh.geometry.dispose();
      car.mesh.geometry = geometry;
      spawnBridgeDeck(state, eid, data);
    }
  },
  dispose(state: State) {
    const cars = ROAD_SIDECARS.get(state);
    if (cars) {
      for (const eid of [...cars.keys()]) disposeRoad(state, eid);
    }
    disposeAllJunctionDiscs(state);
    FUSION_SIG.delete(state);
    FUSION_CHEAP.delete(state);
    ROAD_TOPO_GEN.delete(state);
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
