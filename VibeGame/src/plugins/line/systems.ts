import * as THREE from 'three';
import type { State } from '../../core';
import { defineQuery, type System } from '../../core';
import { WorldTransform } from '../transforms';
import { getScene, getRenderingContext } from '../rendering';
import { Line as LineComponent } from './components';
import {
  getLineContext,
  getMaterialKey,
  getOrCreateBatch,
  disposeBatch,
} from './utils';

const lineQuery = defineQuery([LineComponent, WorldTransform]);

const ARROW_ANGLE = Math.PI / 6;

const _dir = new THREE.Vector3();
const _arrowTip = new THREE.Vector3();
const _arrowDir = new THREE.Vector3();
const _perp = new THREE.Vector3();
const _wingDir = new THREE.Vector3();
const _wingEnd = new THREE.Vector3();
const _upRef = new THREE.Vector3(0, 1, 0);
const _sideRef = new THREE.Vector3(1, 0, 0);
const _startPos = new THREE.Vector3();
const _scaledOffset = new THREE.Vector3();
const _endPos = new THREE.Vector3();
const _lineColor = new THREE.Color();

interface LineData {
  entity: number;
  startPos: THREE.Vector3;
  endPos: THREE.Vector3;
  color: THREE.Color;
  arrowStart: boolean;
  arrowEnd: boolean;
  arrowSize: number;
  visible: boolean;
}

function computeArrowWing(
  start: THREE.Vector3,
  end: THREE.Vector3,
  arrowSize: number,
  atStart: boolean,
  wingIndex: number
): { tip: THREE.Vector3; wingEnd: THREE.Vector3 } {
  _dir.subVectors(end, start).normalize();
  _arrowTip.copy(atStart ? start : end);
  if (atStart) {
    _arrowDir.copy(_dir);
  } else {
    _arrowDir.copy(_dir).negate();
  }

  if (Math.abs(_dir.y) < 0.9) {
    _perp.crossVectors(_dir, _upRef).normalize();
  } else {
    _perp.crossVectors(_dir, _sideRef).normalize();
  }

  const angle = wingIndex === 0 ? ARROW_ANGLE : -ARROW_ANGLE;
  _wingDir
    .copy(_arrowDir)
    .applyAxisAngle(_perp, angle)
    .multiplyScalar(arrowSize);
  _wingEnd.copy(_arrowTip).add(_wingDir);

  return { tip: _arrowTip, wingEnd: _wingEnd };
}

function pushSegment(
  positions: number[],
  colors: number[],
  start: THREE.Vector3,
  end: THREE.Vector3,
  color: THREE.Color
): void {
  positions.push(start.x, start.y, start.z, end.x, end.y, end.z);
  colors.push(color.r, color.g, color.b, color.r, color.g, color.b);
}

function buildBatchArrays(lines: LineData[]): {
  positions: number[];
  colors: number[];
} {
  const positions: number[] = [];
  const colors: number[] = [];

  for (const line of lines) {
    if (!line.visible) continue;

    pushSegment(positions, colors, line.startPos, line.endPos, line.color);

    if (line.arrowStart && line.arrowSize > 0) {
      for (let i = 0; i < 2; i++) {
        const { tip, wingEnd } = computeArrowWing(
          line.startPos,
          line.endPos,
          line.arrowSize,
          true,
          i
        );
        pushSegment(positions, colors, tip, wingEnd, line.color);
      }
    }

    if (line.arrowEnd && line.arrowSize > 0) {
      for (let i = 0; i < 2; i++) {
        const { tip, wingEnd } = computeArrowWing(
          line.startPos,
          line.endPos,
          line.arrowSize,
          false,
          i
        );
        pushSegment(positions, colors, tip, wingEnd, line.color);
      }
    }
  }

  return { positions, colors };
}

export const LineSystem: System = {
  group: 'draw',
  update(state: State) {
    const scene = getScene(state);
    if (!scene) return;

    const renderContext = getRenderingContext(state);
    const context = getLineContext(state);
    const entities = lineQuery(state.world);

    if (renderContext?.renderer) {
      context.resolution.set(
        renderContext.renderer.domElement.width,
        renderContext.renderer.domElement.height
      );
      for (const batch of context.batches.values()) {
        batch.material.resolution.copy(context.resolution);
      }
    }

    const linesByMaterial = new Map<string, LineData[]>();

    for (const entity of entities) {
      const thickness = LineComponent.thickness[entity];
      const opacity = LineComponent.opacity[entity];
      const key = getMaterialKey(thickness, opacity);

      _startPos.set(
        WorldTransform.posX[entity],
        WorldTransform.posY[entity],
        WorldTransform.posZ[entity]
      );

      _scaledOffset.set(
        LineComponent.offsetX[entity] * WorldTransform.scaleX[entity],
        LineComponent.offsetY[entity] * WorldTransform.scaleY[entity],
        LineComponent.offsetZ[entity] * WorldTransform.scaleZ[entity]
      );

      _endPos.set(
        _startPos.x + _scaledOffset.x,
        _startPos.y + _scaledOffset.y,
        _startPos.z + _scaledOffset.z
      );

      const unscaledLength = Math.sqrt(
        LineComponent.offsetX[entity] ** 2 +
          LineComponent.offsetY[entity] ** 2 +
          LineComponent.offsetZ[entity] ** 2
      );
      const scaledLength = _scaledOffset.length();
      const arrowScale = unscaledLength > 0 ? scaledLength / unscaledLength : 1;

      _lineColor.set(LineComponent.color[entity]);

      const lineData: LineData = {
        entity,
        startPos: _startPos.clone(),
        endPos: _endPos.clone(),
        color: _lineColor.clone(),
        arrowStart: LineComponent.arrowStart[entity] === 1,
        arrowEnd: LineComponent.arrowEnd[entity] === 1,
        arrowSize: LineComponent.arrowSize[entity] * arrowScale,
        visible: LineComponent.visible[entity] === 1,
      };

      let group = linesByMaterial.get(key);
      if (!group) {
        group = [];
        linesByMaterial.set(key, group);
      }
      group.push(lineData);
    }

    const usedBatches = new Set<string>();

    for (const [key, lines] of linesByMaterial) {
      usedBatches.add(key);

      const thickness = LineComponent.thickness[lines[0].entity];
      const opacity = LineComponent.opacity[lines[0].entity];
      const batch = getOrCreateBatch(context, key, thickness, opacity, scene);

      const { positions, colors } = buildBatchArrays(lines);

      if (positions.length > 0) {
        delete (batch.geometry as { _maxInstanceCount?: number })
          ._maxInstanceCount;
        batch.geometry.setPositions(positions);
        batch.geometry.setColors(colors);
        batch.segments.computeLineDistances();
        batch.segments.visible = true;
      } else {
        batch.segments.visible = false;
      }
    }

    for (const [key, batch] of context.batches) {
      if (!usedBatches.has(key)) {
        disposeBatch(batch, scene);
        context.batches.delete(key);
      }
    }
  },
};
