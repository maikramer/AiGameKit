// Read-only introspection helpers behind the debug bridge's spatial /
// particle / script / scene queries. Kept out of plugin.ts to keep the
// bridge wiring small; every function here is pure state reads.
import { getAllEntities, defineQuery, type State } from '../../core';
import { getScene } from '../rendering/utils';
import { getScriptFile } from '../entity-script/context';
import { describeParticleEmitters } from '../particles/systems';

export interface BridgePosition {
  x: number;
  y: number;
  z: number;
}

export interface EntityNearRow {
  eid: number;
  name: string | null;
  pos: BridgePosition;
  dist: number;
  components: string[];
}

export interface ScriptRow {
  eid: number;
  name: string | null;
  file: string | null;
  ready: boolean;
  enabled: boolean;
}

interface Vec3Like {
  posX: number[];
  posY: number[];
  posZ: number[];
}

/** Live world position (world-transform, falling back to the local transform). */
export function readWorldPosition(
  state: State,
  eid: number
): BridgePosition | null {
  // bitECS component proxies return 0 for any eid, so membership must be
  // checked before every read — otherwise unknown entities read as origin.
  const wt = state.getComponent('world-transform');
  if (wt && state.hasComponent(eid, wt)) {
    const t = wt as unknown as Vec3Like;
    return { x: t.posX[eid], y: t.posY[eid], z: t.posZ[eid] };
  }
  const t = state.getComponent('transform');
  if (t && state.hasComponent(eid, t)) {
    const v = t as unknown as Vec3Like;
    return { x: v.posX[eid], y: v.posY[eid], z: v.posZ[eid] };
  }
  return null;
}

/** Component-name listing for one entity (names only — no field extraction). */
export function componentNamesFor(state: State, eid: number): string[] {
  const names: string[] = [];
  for (const name of state.getComponentNames()) {
    const comp = state.getComponent(name);
    if (comp && state.hasComponent(eid, comp)) names.push(name);
  }
  return names;
}

/** Entities within `radius` metres of (x, z), nearest first. */
export function findEntitiesNear(
  state: State,
  x: number,
  z: number,
  radius: number,
  options?: { limit?: number; with?: string[] }
): EntityNearRow[] {
  const limit = options?.limit ?? 24;
  const withNames = options?.with;
  if (withNames && withNames.some((n) => state.getComponent(n) == null)) {
    // An unresolvable component name can never match — say so instead of
    // silently returning every entity in range.
    return [];
  }
  const filters = withNames
    ?.map((n) => state.getComponent(n)!)
    .filter((c) => c != null);
  const radiusSq = radius * radius;
  const rows: EntityNearRow[] = [];
  for (const eid of getAllEntities(state.world)) {
    const pos = readWorldPosition(state, eid);
    if (!pos) continue;
    const dx = pos.x - x;
    const dz = pos.z - z;
    const distSq = dx * dx + dz * dz;
    if (distSq > radiusSq) continue;
    if (filters && !filters.every((c) => state.hasComponent(eid, c))) continue;
    rows.push({
      eid,
      name: state.getEntityName(eid) ?? null,
      pos,
      dist: Math.sqrt(distSq),
      components: componentNamesFor(state, eid),
    });
    if (rows.length >= limit * 4) break; // bounded scan; sort trims below
  }
  rows.sort((a, b) => a.dist - b.dist);
  return rows.slice(0, limit);
}

/** Scripted entities with their resolved module file and lifecycle state. */
export function listScripts(state: State): ScriptRow[] {
  const mono = state.getComponent('mono-behaviour');
  if (!mono) return [];
  const q = defineQuery([mono]);
  const fields = mono as unknown as Record<string, number[]>;
  const rows: ScriptRow[] = [];
  for (const eid of q(state.world)) {
    rows.push({
      eid,
      name: state.getEntityName(eid) ?? null,
      file: getScriptFile(state, eid) ?? null,
      ready: fields.ready?.[eid] === 1,
      enabled: fields.enabled?.[eid] === 1,
    });
  }
  rows.sort((a, b) => a.eid - b.eid);
  return rows;
}

export { describeParticleEmitters };

export interface SceneLightRow {
  type: string;
  pos: [number, number, number];
  intensity?: number;
  distance?: number;
}

export interface SceneSummary {
  groups: number;
  meshes: number;
  skinnedMeshes: number;
  points: number;
  lines: number;
  sprites: number;
  lights: SceneLightRow[];
}

/** Census of the rendered scene: draw-relevant counts plus every light. */
export function summarizeScene(state: State): SceneSummary | null {
  const scene = getScene(state);
  if (!scene) return null;
  const summary: SceneSummary = {
    groups: 0,
    meshes: 0,
    skinnedMeshes: 0,
    points: 0,
    lines: 0,
    sprites: 0,
    lights: [],
  };
  scene.traverse((obj) => {
    // Object3D ships no type guards for the subclasses we count; read the
    // runtime `is*` flags off a widened shape instead.
    const o = obj as import('three').Object3D & {
      isGroup?: boolean;
      isMesh?: boolean;
      isSkinnedMesh?: boolean;
      isPoints?: boolean;
      isLine?: boolean;
      isSprite?: boolean;
      isLight?: boolean;
      intensity?: number;
      distance?: number;
    };
    if (o.isLight) {
      const e = o.matrixWorld.elements;
      const light: SceneLightRow = {
        type: o.type,
        pos: [
          Number(e[12].toFixed(1)),
          Number(e[13].toFixed(1)),
          Number(e[14].toFixed(1)),
        ],
      };
      if (typeof o.intensity === 'number') light.intensity = o.intensity;
      if (typeof o.distance === 'number') light.distance = o.distance;
      summary.lights.push(light);
      return;
    }
    if (o.isGroup) summary.groups++;
    else if (o.isSkinnedMesh) summary.skinnedMeshes++;
    else if (o.isMesh) summary.meshes++;
    else if (o.isPoints) summary.points++;
    else if (o.isLine) summary.lines++;
    else if (o.isSprite) summary.sprites++;
  });
  return summary;
}
