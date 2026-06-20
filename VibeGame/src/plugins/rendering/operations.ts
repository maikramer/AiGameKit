import { logger } from '../../core/utils/logger';
import type { State } from '../../core';
import * as THREE from 'three';
import { WorldTransform } from '../transforms';
import { MeshRenderer } from './components';
import {
  findAvailableInstanceSlot,
  initializeInstancedMesh,
  releaseInstanceSlot,
  resizeInstancedMesh,
  RendererShape,
  MAX_TOTAL_INSTANCES,
  PERFORMANCE_WARNING_THRESHOLD,
  type RenderingContext,
} from './utils';

const matrix = new THREE.Matrix4();
const position = new THREE.Vector3();
const rotation = new THREE.Quaternion();
const scale = new THREE.Vector3();
const _color = new THREE.Color();
const _zeroMatrix = new THREE.Matrix4();
_zeroMatrix.makeScale(0, 0, 0);

export function getOrCreateMesh(
  context: RenderingContext,
  shapeId: number,
  unlit: boolean = false
): THREE.InstancedMesh | null {
  const pools = unlit ? context.unlitMeshPools : context.meshPools;
  const material = unlit ? context.unlitMaterial : context.material;
  let mesh = pools.get(shapeId);

  if (!mesh) {
    const geometry = context.geometries.get(shapeId);
    if (!geometry) return null;

    mesh = initializeInstancedMesh(geometry, material);
    pools.set(shapeId, mesh);
    context.scene.add(mesh);
  }

  return mesh;
}

export function updateInstance(
  mesh: THREE.InstancedMesh,
  entity: number,
  context: RenderingContext,
  state: State,
  unlit: boolean = false
): THREE.InstancedMesh {
  let instanceInfo = context.entityInstances.get(entity);

  if (!instanceInfo) {
    let instanceId = findAvailableInstanceSlot(mesh, matrix);

    if (instanceId === null) {
      if (context.totalInstanceCount >= MAX_TOTAL_INSTANCES) {
        throw new Error(
          `Maximum total instances (${MAX_TOTAL_INSTANCES}) exceeded. ` +
            `Cannot render entity ${entity}. Consider reducing the number of rendered objects.`
        );
      }

      const shapeId = MeshRenderer.shape[entity];
      const geometry = context.geometries.get(shapeId);
      if (!geometry) return mesh;

      const pools = unlit ? context.unlitMeshPools : context.meshPools;
      const material = unlit ? context.unlitMaterial : context.material;

      mesh = resizeInstancedMesh(mesh, geometry, material, context.scene);
      pools.set(shapeId, mesh);

      instanceId = findAvailableInstanceSlot(mesh, matrix);
      if (instanceId === null) return mesh;
    }

    instanceInfo = {
      poolId: MeshRenderer.shape[entity],
      instanceId,
      unlit,
      initialized: false,
      px: 0,
      py: 0,
      pz: 0,
      rx: 0,
      ry: 0,
      rz: 0,
      rw: 0,
      sx: 0,
      sy: 0,
      sz: 0,
      color: -1,
    };
    context.entityInstances.set(entity, instanceInfo);
    context.totalInstanceCount++;

    if (
      !context.hasShownPerformanceWarning &&
      context.totalInstanceCount >= PERFORMANCE_WARNING_THRESHOLD
    ) {
      logger.warn(
        `Performance warning: ${context.totalInstanceCount} rendered instances. ` +
          `Consider optimizing your scene or reducing object count for better performance.`
      );
      context.hasShownPerformanceWarning = true;
    }
  }

  if (state.hasComponent(entity, WorldTransform)) {
    position.set(
      WorldTransform.posX[entity],
      WorldTransform.posY[entity],
      WorldTransform.posZ[entity]
    );
    rotation.set(
      WorldTransform.rotX[entity],
      WorldTransform.rotY[entity],
      WorldTransform.rotZ[entity],
      WorldTransform.rotW[entity]
    );
    scale.set(
      WorldTransform.scaleX[entity],
      WorldTransform.scaleY[entity],
      WorldTransform.scaleZ[entity]
    );

    if (instanceInfo.poolId === RendererShape.SPHERE) {
      const sphereScale = MeshRenderer.sizeX[entity] / 2;
      scale.x *= sphereScale;
      scale.y *= sphereScale;
      scale.z *= sphereScale;
    } else {
      scale.x *= MeshRenderer.sizeX[entity];
      scale.y *= MeshRenderer.sizeY[entity];
      scale.z *= MeshRenderer.sizeZ[entity];
    }

    // Only touch the GPU buffers when the transform or color actually changed.
    // setMatrixAt/setColorAt + needsUpdate force a full instance-buffer
    // re-upload, so skipping unchanged (static) instances is the hot-path win.
    const fresh = !instanceInfo.initialized;
    if (
      fresh ||
      instanceInfo.px !== position.x ||
      instanceInfo.py !== position.y ||
      instanceInfo.pz !== position.z ||
      instanceInfo.rx !== rotation.x ||
      instanceInfo.ry !== rotation.y ||
      instanceInfo.rz !== rotation.z ||
      instanceInfo.rw !== rotation.w ||
      instanceInfo.sx !== scale.x ||
      instanceInfo.sy !== scale.y ||
      instanceInfo.sz !== scale.z
    ) {
      matrix.compose(position, rotation, scale);
      mesh.setMatrixAt(instanceInfo.instanceId, matrix);
      mesh.instanceMatrix.needsUpdate = true;
      instanceInfo.px = position.x;
      instanceInfo.py = position.y;
      instanceInfo.pz = position.z;
      instanceInfo.rx = rotation.x;
      instanceInfo.ry = rotation.y;
      instanceInfo.rz = rotation.z;
      instanceInfo.rw = rotation.w;
      instanceInfo.sx = scale.x;
      instanceInfo.sy = scale.y;
      instanceInfo.sz = scale.z;
    }

    const color = MeshRenderer.color[entity];
    if (fresh || instanceInfo.color !== color) {
      _color.set(color);
      mesh.setColorAt(instanceInfo.instanceId, _color);
      if (mesh.instanceColor) {
        mesh.instanceColor.needsUpdate = true;
      }
      instanceInfo.color = color;
    }

    instanceInfo.initialized = true;
  }

  return mesh;
}

export function hideInstance(
  mesh: THREE.InstancedMesh,
  entity: number,
  context: RenderingContext
): void {
  const instanceInfo = context.entityInstances.get(entity);
  if (instanceInfo) {
    mesh.setMatrixAt(instanceInfo.instanceId, _zeroMatrix);
    mesh.instanceMatrix.needsUpdate = true;
    // The buffer slot was zeroed; force a rewrite if this entity is shown again.
    instanceInfo.initialized = false;
    releaseInstanceSlot(mesh, instanceInfo.instanceId);
  }
}
