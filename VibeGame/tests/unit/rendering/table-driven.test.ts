import { beforeEach, describe, expect, it } from 'bun:test';
import * as THREE from 'three';
import { State } from 'aigamekit-vibegame';
import {
  AmbientLight,
  DirectionalLight,
  MainCamera,
  MeshRenderer,
  PointLight,
} from 'aigamekit-vibegame/rendering';
import {
  findAvailableInstanceSlot,
  initializeInstancedMesh,
  instanceBoundsDirty,
  markInstanceBoundsDirty,
  recomputeInstanceBounds,
  releaseInstanceSlot,
} from '../../../src/plugins/rendering/utils';

describe('rendering instanced mesh pool table-driven', () => {
  const geo = new THREE.BoxGeometry(1, 1, 1);
  const mat = new THREE.MeshStandardMaterial();

  for (let poolSize = 4; poolSize <= 20; poolSize += 4) {
    it(`free list pop/push poolSize=${poolSize}`, () => {
      const mesh = initializeInstancedMesh(geo, mat, poolSize);
      expect(mesh.frustumCulled).toBe(false);
      const slots: number[] = [];
      for (let s = 0; s < poolSize; s++) {
        const idx = findAvailableInstanceSlot(mesh, new THREE.Matrix4());
        expect(idx).not.toBeNull();
        slots.push(idx!);
      }
      expect(findAvailableInstanceSlot(mesh, new THREE.Matrix4())).toBeNull();
      for (const idx of slots) {
        releaseInstanceSlot(mesh, idx);
      }
      const recycled = findAvailableInstanceSlot(mesh, new THREE.Matrix4());
      expect(recycled).not.toBeNull();
    });
  }

  for (let i = 0; i < 80; i++) {
    it(`bounds dirty flag cycle ${i}`, () => {
      const mesh = initializeInstancedMesh(geo, mat, 8);
      expect(instanceBoundsDirty(mesh)).toBe(false);
      markInstanceBoundsDirty(mesh);
      expect(instanceBoundsDirty(mesh)).toBe(true);
      expect(mesh.frustumCulled).toBe(false);
      recomputeInstanceBounds(mesh);
      expect(instanceBoundsDirty(mesh)).toBe(false);
      expect(mesh.frustumCulled).toBe(true);
    });
  }
});

describe('rendering component fields table-driven', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
  });

  for (let i = 0; i < 50; i++) {
    it(`MeshRenderer roundtrip ${i}`, () => {
      const eid = state.createEntity();
      state.addComponent(eid, MeshRenderer);
      MeshRenderer.shape[eid] = i % 2;
      MeshRenderer.sizeX[eid] = 1 + i * 0.1;
      MeshRenderer.sizeY[eid] = 2 + i * 0.05;
      MeshRenderer.sizeZ[eid] = 0.5 + i * 0.02;
      MeshRenderer.color[eid] = 0xff0000 + (i % 256);
      MeshRenderer.visible[eid] = i % 2;
      MeshRenderer.unlit[eid] = (i + 1) % 2;
      expect(MeshRenderer.shape[eid]).toBe(i % 2);
      expect(MeshRenderer.sizeX[eid]).toBeCloseTo(1 + i * 0.1, 5);
      expect(MeshRenderer.visible[eid]).toBe(i % 2);
    });
  }

  for (let i = 0; i < 25; i++) {
    it(`MainCamera clip planes ${i}`, () => {
      const eid = state.createEntity();
      state.addComponent(eid, MainCamera);
      MainCamera.projection[eid] = i % 2;
      MainCamera.fov[eid] = 45 + (i % 30);
      MainCamera.orthoSize[eid] = 10 + i;
      MainCamera.near[eid] = 0.05 + (i % 5) * 0.01;
      MainCamera.far[eid] = 100 + i * 10;
      expect(MainCamera.fov[eid]).toBeCloseTo(45 + (i % 30), 5);
      expect(MainCamera.far[eid]).toBeGreaterThan(MainCamera.near[eid]);
    });
  }

  for (let i = 0; i < 25; i++) {
    it(`light components ${i}`, () => {
      const amb = state.createEntity();
      const dir = state.createEntity();
      const pt = state.createEntity();
      state.addComponent(amb, AmbientLight);
      state.addComponent(dir, DirectionalLight);
      state.addComponent(pt, PointLight);
      AmbientLight.intensity[amb] = 0.3 + i * 0.01;
      DirectionalLight.intensity[dir] = 1 + i * 0.02;
      DirectionalLight.directionY[dir] = -1;
      PointLight.distance[pt] = 5 + i;
      expect(AmbientLight.intensity[amb]).toBeCloseTo(0.3 + i * 0.01, 5);
      expect(DirectionalLight.directionY[dir]).toBe(-1);
      expect(PointLight.distance[pt]).toBe(5 + i);
    });
  }
});
