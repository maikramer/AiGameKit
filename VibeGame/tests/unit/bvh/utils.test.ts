import * as THREE from 'three';
import { beforeEach, describe, expect, it } from 'bun:test';
import {
  State,
  castBvhRay,
  getBvhContext,
  getBvhStats,
  getBvhSurfaceHeight,
  registerBvhMesh,
  unregisterBvhForEntity,
  unregisterBvhMesh,
} from 'vibegame';
import type { BvhRaycastHit } from 'vibegame';

function makeTriangleGeometry(): THREE.BufferGeometry {
  const geo = new THREE.BufferGeometry();
  geo.setAttribute(
    'position',
    new THREE.BufferAttribute(new Float32Array([0, 0, 0, 2, 0, 0, 0, 2, 0]), 3)
  );
  geo.setIndex(new THREE.BufferAttribute(new Uint32Array([0, 1, 2]), 1));
  return geo;
}

describe('getBvhContext', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
  });

  it('retorna um contexto com entries e entityKeys vazios', () => {
    const ctx = getBvhContext(state);
    expect(ctx.entries).toBeInstanceOf(Map);
    expect(ctx.entityKeys).toBeInstanceOf(Map);
    expect(ctx.entries.size).toBe(0);
    expect(ctx.entityKeys.size).toBe(0);
  });

  it('memoiza por State (mesma referência nas chamadas subsequentes)', () => {
    expect(getBvhContext(state)).toBe(getBvhContext(state));
  });

  it('isola contextos entre States distintos', () => {
    const other = new State();
    expect(getBvhContext(state)).not.toBe(getBvhContext(other));
  });
});

describe('getBvhStats', () => {
  it('reporta zeros num State sem meshes registradas', () => {
    const state = new State();
    const stats = getBvhStats(state);
    expect(stats.meshCount).toBe(0);
    expect(stats.entityCount).toBe(0);
  });
});

describe('registerBvhMesh / unregisterBvhMesh', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
  });

  it('registra com defaults entity=0 e layer=0xffff e atualiza getBvhStats', () => {
    const entry = registerBvhMesh(state, 'm1', makeTriangleGeometry());

    expect(entry.entity).toBe(0);
    expect(entry.layer).toBe(0xffff);
    expect(entry.mesh).toBeInstanceOf(THREE.Mesh);

    const stats = getBvhStats(state);
    expect(stats.meshCount).toBe(1);
    expect(stats.entityCount).toBe(0);
  });

  it('respeita entity e layer custom e indexa entityKeys por entidade', () => {
    registerBvhMesh(state, 'gltf:5', makeTriangleGeometry(), {
      entity: 5,
      layer: 0x0002,
    });

    expect(getBvhContext(state).entityKeys.get(5)).toEqual(['gltf:5']);
    const stats = getBvhStats(state);
    expect(stats.meshCount).toBe(1);
    expect(stats.entityCount).toBe(1);
  });

  it('sobrescreve a chave ao re-registrar a mesma chave', () => {
    registerBvhMesh(state, 'dup', makeTriangleGeometry());
    registerBvhMesh(state, 'dup', makeTriangleGeometry());

    expect(getBvhStats(state).meshCount).toBe(1);
  });

  it('unregisterBvhMesh remove a entrada e libera entityKeys', () => {
    registerBvhMesh(state, 'gltf:9', makeTriangleGeometry(), { entity: 9 });

    unregisterBvhMesh(state, 'gltf:9');

    expect(getBvhContext(state).entries.has('gltf:9')).toBe(false);
    expect(getBvhContext(state).entityKeys.has(9)).toBe(false);
  });

  it('unregisterBvhMesh é no-op para chave inexistente', () => {
    expect(() => unregisterBvhMesh(state, 'missing')).not.toThrow();
    expect(getBvhStats(state).meshCount).toBe(0);
  });

  it('unregisterBvhForEntity remove todos os meshes daquela entidade', () => {
    registerBvhMesh(state, 'a:3', makeTriangleGeometry(), { entity: 3 });
    registerBvhMesh(state, 'b:3', makeTriangleGeometry(), { entity: 3 });
    registerBvhMesh(state, 'c:7', makeTriangleGeometry(), { entity: 7 });

    unregisterBvhForEntity(state, 3);

    const ctx = getBvhContext(state);
    expect(ctx.entries.has('a:3')).toBe(false);
    expect(ctx.entries.has('b:3')).toBe(false);
    expect(ctx.entries.has('c:7')).toBe(true);
    expect(ctx.entityKeys.has(3)).toBe(false);
    expect(ctx.entityKeys.has(7)).toBe(true);
  });
});

describe('castBvhRay', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
  });

  it('retorna null quando não há meshes registradas', () => {
    const origin = new THREE.Vector3(0, 0, 10);
    const dir = new THREE.Vector3(0, 0, -1);
    expect(castBvhRay(state, origin, dir, 100)).toBeNull();
  });

  it('acerta um triângulo e devolve distance/point/layer/key preenchidos', () => {
    registerBvhMesh(state, 'tri', makeTriangleGeometry(), {
      entity: 1,
      layer: 0x0001,
    });

    const origin = new THREE.Vector3(0.5, 0.5, 5);
    const dir = new THREE.Vector3(0, 0, -1);
    const hit = castBvhRay(state, origin, dir, 100) as BvhRaycastHit | null;

    expect(hit).not.toBeNull();
    expect(hit!.entity).toBe(1);
    expect(hit!.layer).toBe(0x0001);
    expect(hit!.distance).toBeGreaterThan(0);
    expect(hit!.distance).toBeLessThanOrEqual(100);
    expect(hit!.point.z).toBeCloseTo(0, 5);
    expect(hit!.key).toBe('tri');
  });

  it('respeita o layerMask: máscara sem bits sobrepostos ignora o mesh', () => {
    registerBvhMesh(state, 'tri', makeTriangleGeometry(), { layer: 0x0001 });

    const origin = new THREE.Vector3(0.5, 0.5, 5);
    const dir = new THREE.Vector3(0, 0, -1);
    expect(castBvhRay(state, origin, dir, 100, 0x0002)).toBeNull();
  });

  it('escreve no objeto out fornecido pelo chamador', () => {
    registerBvhMesh(state, 'tri', makeTriangleGeometry(), { layer: 0x0001 });

    const out: BvhRaycastHit = {
      entity: 0,
      layer: 0,
      distance: 0,
      point: new THREE.Vector3(),
      normal: new THREE.Vector3(),
      key: '',
    };
    const origin = new THREE.Vector3(0.5, 0.5, 5);
    const dir = new THREE.Vector3(0, 0, -1);
    const result = castBvhRay(state, origin, dir, 100, 0xffff, out);

    expect(result).toBe(out);
    expect(out.key).toBe('tri');
    expect(out.distance).toBeGreaterThan(0);
  });
});

describe('getBvhSurfaceHeight', () => {
  it('retorna o Y da superfície ao raycast vertical descendente', () => {
    const state = new State();
    const geo = new THREE.BufferGeometry();
    geo.setAttribute(
      'position',
      new THREE.BufferAttribute(
        new Float32Array([-5, 3, -5, 0, 3, 5, 5, 3, -5]),
        3
      )
    );
    geo.setIndex(new THREE.BufferAttribute(new Uint32Array([0, 1, 2]), 1));
    registerBvhMesh(state, 'floor', geo);

    expect(getBvhSurfaceHeight(state, 0, 100, 0)).toBeCloseTo(3, 5);
  });

  it('retorna null quando o raio não acerta nada', () => {
    const state = new State();
    expect(getBvhSurfaceHeight(state, 0, 100, 0)).toBeNull();
  });
});
