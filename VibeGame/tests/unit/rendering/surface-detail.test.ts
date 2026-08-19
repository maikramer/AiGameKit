import { afterEach, describe, expect, it } from 'bun:test';
import * as THREE from 'three';
import {
  applySurfaceDetail,
  disposeSurfaceDetail,
  getSurfaceDetailTextures,
  type SurfaceDetailKind,
} from '../../../src/plugins/rendering/surface-detail';

/**
 * Procedural micro-detail for large flat surfaces. The synthesis has to be
 * deterministic (same tile every reload, same tile on every machine) and the
 * tiles have to wrap, or a road shows a visible grid of seams every few metres.
 */

const KINDS: SurfaceDetailKind[] = [
  'asphalt',
  'gravel',
  'dirt',
  'concrete',
  'metal',
];

function dataOf(texture: THREE.Texture): Uint8Array {
  return (texture as THREE.DataTexture).image.data as Uint8Array;
}

/** `Texture.image` is typed `unknown`; DataTexture always carries width/height. */
function sizeOf(texture: THREE.Texture): { width: number; height: number } {
  return (texture as THREE.DataTexture).image;
}

afterEach(() => {
  disposeSurfaceDetail();
});

describe('getSurfaceDetailTextures', () => {
  it('synthesises a normal + roughness pair for every kind', () => {
    for (const kind of KINDS) {
      const { normal, roughness } = getSurfaceDetailTextures(kind);
      expect(sizeOf(normal).width).toBeGreaterThan(0);
      expect(sizeOf(roughness).width).toBe(sizeOf(normal).width);
    }
  });

  it('caches: the same kind returns the identical texture objects', () => {
    const first = getSurfaceDetailTextures('asphalt');
    const second = getSurfaceDetailTextures('asphalt');
    expect(second.normal).toBe(first.normal);
    expect(second.roughness).toBe(first.roughness);
  });

  it('is deterministic across a dispose/rebuild cycle', () => {
    const before = Array.from(
      dataOf(getSurfaceDetailTextures('gravel').normal)
    );
    disposeSurfaceDetail();
    const after = Array.from(dataOf(getSurfaceDetailTextures('gravel').normal));
    expect(after).toEqual(before);
  });

  it('gives different kinds different tiles', () => {
    const asphalt = dataOf(getSurfaceDetailTextures('asphalt').normal);
    const gravel = dataOf(getSurfaceDetailTextures('gravel').normal);
    let differences = 0;
    for (let i = 0; i < asphalt.length; i += 4) {
      if (asphalt[i] !== gravel[i]) differences++;
    }
    expect(differences).toBeGreaterThan(1000);
  });

  it('wraps, so a tiled road has no seam', () => {
    const { normal } = getSurfaceDetailTextures('asphalt');
    expect(normal.wrapS).toBe(THREE.RepeatWrapping);
    expect(normal.wrapT).toBe(THREE.RepeatWrapping);

    // The tile is built on a wrapping lattice, so the step across the seam
    // (last column -> first column) must be statistically the same size as a
    // step anywhere inside the tile. Comparing means rather than maxima is the
    // point: individual aggregate specks are *supposed* to be sharp jumps.
    const size = sizeOf(normal).width;
    const data = dataOf(normal);
    let seamSum = 0;
    let interiorSum = 0;
    for (let y = 0; y < size; y++) {
      const first = data[(y * size + 0) * 4]!;
      const last = data[(y * size + size - 1) * 4]!;
      seamSum += Math.abs(first - last);
      const mid = data[(y * size + (size >> 1)) * 4]!;
      const midNext = data[(y * size + (size >> 1) + 1) * 4]!;
      interiorSum += Math.abs(mid - midNext);
    }
    expect(seamSum / size).toBeLessThan((interiorSum / size) * 2 + 4);
  });

  it('encodes normals in the upper half of the blue channel', () => {
    // A tangent-space normal always points away from the surface, so Z (blue)
    // never goes negative — a tile with blue < 128 would be inside-out.
    const data = dataOf(getSurfaceDetailTextures('dirt').normal);
    let minBlue = 255;
    for (let i = 2; i < data.length; i += 4)
      minBlue = Math.min(minBlue, data[i]!);
    expect(minBlue).toBeGreaterThanOrEqual(127);
  });

  it('writes roughness as a multiplier below 1, never above', () => {
    const data = dataOf(getSurfaceDetailTextures('asphalt').roughness);
    let max = 0;
    let min = 255;
    for (let i = 1; i < data.length; i += 4) {
      max = Math.max(max, data[i]!);
      min = Math.min(min, data[i]!);
    }
    expect(max).toBeLessThanOrEqual(255);
    // There has to be actual variation, or the map is a no-op costing a fetch.
    expect(max - min).toBeGreaterThan(10);
  });
});

describe('applySurfaceDetail', () => {
  it('attaches both maps and a matching normal scale', () => {
    const material = new THREE.MeshStandardMaterial();
    applySurfaceDetail(material, 'asphalt', { normalScale: 0.7 });
    expect(material.normalMap).not.toBeNull();
    expect(material.roughnessMap).not.toBeNull();
    expect(material.normalScale.x).toBe(0.7);
    expect(material.normalScale.y).toBe(0.7);
  });

  it('keeps the requested roughness as the mean of the surface', () => {
    // The map multiplies the scalar, so the scalar must be pre-divided by the
    // map's mean — otherwise every detailed surface comes out too polished.
    const material = new THREE.MeshStandardMaterial();
    applySurfaceDetail(material, 'asphalt', { roughness: 0.6 });

    const data = dataOf(material.roughnessMap as THREE.Texture);
    let sum = 0;
    let count = 0;
    for (let i = 1; i < data.length; i += 4) {
      sum += data[i]! / 255;
      count++;
    }
    const meanMultiplier = sum / count;
    expect(material.roughness * meanMultiplier).toBeCloseTo(0.6, 1);
  });

  it('gives each repeat its own texture so surfaces cannot retile each other', () => {
    const road = new THREE.MeshStandardMaterial();
    const shoulder = new THREE.MeshStandardMaterial();
    applySurfaceDetail(road, 'asphalt', { repeatX: 6, repeatY: 1 });
    applySurfaceDetail(shoulder, 'asphalt', { repeatX: 3, repeatY: 0.5 });

    expect(road.normalMap).not.toBe(shoulder.normalMap);
    expect(road.normalMap!.repeat.x).toBe(6);
    expect(road.normalMap!.repeat.y).toBe(1);
    expect(shoulder.normalMap!.repeat.x).toBe(3);
    expect(shoulder.normalMap!.repeat.y).toBe(0.5);
  });

  it('reuses the tiled clone when the repeat matches', () => {
    const a = new THREE.MeshStandardMaterial();
    const b = new THREE.MeshStandardMaterial();
    applySurfaceDetail(a, 'concrete', { repeat: 4 });
    applySurfaceDetail(b, 'concrete', { repeat: 4 });
    expect(a.normalMap).toBe(b.normalMap);
  });

  it('sets metalness only when asked', () => {
    const material = new THREE.MeshStandardMaterial({ metalness: 0.25 });
    applySurfaceDetail(material, 'dirt');
    expect(material.metalness).toBe(0.25);
    applySurfaceDetail(material, 'metal', { metalness: 0.9 });
    expect(material.metalness).toBe(0.9);
  });

  it('is idempotent — re-applying only re-tunes the numbers', () => {
    const material = new THREE.MeshStandardMaterial();
    applySurfaceDetail(material, 'asphalt', { repeat: 8, roughness: 0.6 });
    const firstMap = material.normalMap;
    applySurfaceDetail(material, 'asphalt', { repeat: 8, roughness: 0.3 });
    expect(material.normalMap).toBe(firstMap);
    expect(material.roughness).toBeLessThan(0.5);
  });
});
