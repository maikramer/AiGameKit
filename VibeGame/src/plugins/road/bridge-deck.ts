/**
 * Deck walk-surface probe. The shipped bridge LODs are ramp→plateau→ramp, and
 * the topmost geometry is the parapet crown, not the lane — so the AABB says
 * nothing useful about where a road can sit. Ray-cast the lane from above once
 * per deck and keep the contour as offsets from the entity origin: seating,
 * ribbon height and the terrain clearance cut all read the same curve.
 */
import * as THREE from 'three';

import {
  fillContourGaps,
  pathArcLength,
  pathPointAtArc,
  type BridgeDeckContour,
} from './bridge';

/** Contour samples along the span. Odd count keeps a sample on the crown. */
export const BRIDGE_CONTOUR_SAMPLES = 33;

/**
 * Rays at the exact mesh ends graze the boundary edge and miss, so the tip
 * samples step this far inward along the path.
 */
export const BRIDGE_CONTOUR_TIP_INSET_M = 0.25;

/** Vertical headroom above the deck AABB where rays start. */
const PROBE_HEADROOM_M = 2;

/**
 * LOD0 child if the group is a LOD root, else the group itself. Raycasting the
 * whole root would also hit the hidden LOD1/LOD2 copies (three's raycaster
 * ignores `visible`), and each LOD has a slightly different surface.
 */
function pickProbeTarget(group: THREE.Object3D): THREE.Object3D {
  for (const child of group.children) {
    if (child.name === 'lod0') return child;
  }
  return group;
}

/**
 * Walk-surface offsets from `originY` at even arc fractions along `path`
 * (world XZ). Returns null while the mesh is missing or every ray misses.
 */
export function probeDeckLocalContour(
  group: THREE.Object3D,
  path: number[],
  originY: number,
  samples = BRIDGE_CONTOUR_SAMPLES
): BridgeDeckContour | null {
  if (path.length < 4 || samples < 2) return null;
  group.updateWorldMatrix(true, true);
  const box = new THREE.Box3().setFromObject(group);
  if (box.isEmpty()) return null;

  const target = pickProbeTarget(group);
  const total = pathArcLength(path);
  if (!(total > 1e-6)) return null;
  const inset = Math.min(BRIDGE_CONTOUR_TIP_INSET_M, total * 0.02);

  const raycaster = new THREE.Raycaster();
  raycaster.far = box.max.y - box.min.y + PROBE_HEADROOM_M * 2;
  const down = new THREE.Vector3(0, -1, 0);
  const from = new THREE.Vector3();
  const rayY = box.max.y + PROBE_HEADROOM_M;

  const raw: Array<number | null> = [];
  for (let i = 0; i < samples; i++) {
    const u = i / (samples - 1);
    const arc = Math.min(Math.max(u * total, inset), total - inset);
    const p = pathPointAtArc(path, arc);
    from.set(p.x, rayY, p.z);
    raycaster.set(from, down);
    const hits = raycaster.intersectObject(target, true);
    raw.push(hits.length > 0 ? hits[0]!.point.y - originY : null);
  }
  return fillContourGaps(raw);
}
