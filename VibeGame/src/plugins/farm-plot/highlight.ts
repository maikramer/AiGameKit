import * as THREE from 'three';
import type { State } from '../../core';
import { FarmGrid } from './components';
import { facingCellFrom } from './grid';
import { getFarmGridData, specOf } from './store';
import { getRenderingContext } from '../rendering/utils';
import { WorldTransform } from '../transforms';

/**
 * The translucent quad that marks the tile the actor is about to work.
 * Drawn with depthTest off and a high renderOrder so grid lines stay readable
 * through crops and terrain alike.
 */

interface HighlightRig {
  quad: THREE.Mesh;
  outline: THREE.LineSegments;
}

const rigs = new WeakMap<State, HighlightRig>();

function rigFor(state: State): HighlightRig {
  let rig = rigs.get(state);
  if (rig) return rig;

  const quadGeom = new THREE.PlaneGeometry(1, 1);
  quadGeom.rotateX(-Math.PI / 2);
  const quadMat = new THREE.MeshBasicMaterial({
    color: 0xfff2b0,
    transparent: true,
    opacity: 0.28,
    depthTest: false,
    depthWrite: false,
  });
  const quad = new THREE.Mesh(quadGeom, quadMat);
  quad.renderOrder = 990;

  const outlineGeom = new THREE.EdgesGeometry(quadGeom);
  const outlineMat = new THREE.LineBasicMaterial({
    color: 0xfff8d8,
    transparent: true,
    opacity: 0.9,
    depthTest: false,
  });
  const outline = new THREE.LineSegments(outlineGeom, outlineMat);
  outline.renderOrder = 991;

  quad.visible = false;
  outline.visible = false;
  getRenderingContext(state).scene.add(quad, outline);

  rig = { quad, outline };
  rigs.set(state, rig);
  return rig;
}

/**
 * Move the highlight onto the facing tile of `actorEid` (or hide it).
 * Returns the highlighted cell, or null when nothing is in reach.
 */
export function updateFarmHighlight(
  state: State,
  gridEid: number,
  actorEid: number,
  forward: { x: number; z: number }
): { col: number; row: number } | null {
  const data = getFarmGridData(state, gridEid);
  const rig = rigFor(state);
  if (!data?.ready) {
    rig.quad.visible = false;
    rig.outline.visible = false;
    return null;
  }

  const px = worldX(state, actorEid);
  const pz = worldZ(state, actorEid);
  if (px === null || pz === null) {
    rig.quad.visible = false;
    rig.outline.visible = false;
    return null;
  }

  const spec = specOf(state, gridEid);
  const cell = facingCellFrom(px, pz, forward.x, forward.z, spec);
  if (!cell) {
    rig.quad.visible = false;
    rig.outline.visible = false;
    return null;
  }

  const cs = spec.cellSize || 1;
  rig.quad.position.set(
    spec.originX + cell.col * cs,
    FarmGrid.baseY[gridEid] + FarmGrid.surfaceEpsilon[gridEid] * 2,
    spec.originZ + cell.row * cs
  );
  rig.quad.scale.set(cs, 1, cs);
  rig.outline.position.copy(rig.quad.position);
  rig.outline.scale.set(cs, 1, cs);
  rig.quad.visible = true;
  rig.outline.visible = true;
  return cell;
}

function worldX(state: State, eid: number): number | null {
  return state.hasComponent(eid, WorldTransform)
    ? WorldTransform.posX[eid]
    : null;
}

function worldZ(state: State, eid: number): number | null {
  return state.hasComponent(eid, WorldTransform)
    ? WorldTransform.posZ[eid]
    : null;
}
