import { afterEach, describe, expect, it } from 'bun:test';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import * as path from 'node:path';
import { analyzeWorld } from '../../../src/cli/analyze/run';
import {
  findGroundOverlaps,
  findSolidOverlaps,
} from '../../../src/cli/analyze/overlap';
import type { Footprint } from '../../../src/cli/analyze/types';

const dirs: string[] = [];

afterEach(async () => {
  for (const d of dirs.splice(0)) {
    await rm(d, { recursive: true, force: true });
  }
});

async function makeFixture(files: Record<string, string>): Promise<{
  root: string;
  publicDir: string;
}> {
  const root = await mkdtemp(path.join(tmpdir(), 'vg-analyze-'));
  dirs.push(root);
  const publicDir = path.join(root, 'public');
  for (const [rel, body] of Object.entries(files)) {
    const full = path.join(root, rel);
    await mkdir(path.dirname(full), { recursive: true });
    await writeFile(full, body);
  }
  return { root, publicDir };
}

function fp(
  partial: Omit<Footprint, 'minY' | 'maxY'> &
    Partial<Pick<Footprint, 'minY' | 'maxY'>>
): Footprint {
  return {
    minY: 0,
    maxY: 0,
    ...partial,
  };
}

describe('findSolidOverlaps', () => {
  it('reports overlapping footprints', () => {
    const a = fp({
      id: 'a',
      label: 'A',
      minX: 0,
      maxX: 4,
      minZ: 0,
      maxZ: 4,
      kind: 'composition',
    });
    const b = fp({
      id: 'b',
      label: 'B',
      minX: 2,
      maxX: 6,
      minZ: 2,
      maxZ: 6,
      kind: 'composition',
    });
    const issues = findSolidOverlaps([a, b]);
    expect(issues.length).toBe(1);
    expect(issues[0]!.code).toBe('overlap');
    expect(issues[0]!.severity).toBe('error');
  });

  it('ignores non-overlapping footprints', () => {
    const a = fp({
      id: 'a',
      label: 'A',
      minX: 0,
      maxX: 1,
      minZ: 0,
      maxZ: 1,
      kind: 'composition',
    });
    const b = fp({
      id: 'b',
      label: 'B',
      minX: 10,
      maxX: 11,
      minZ: 10,
      maxZ: 11,
      kind: 'composition',
    });
    expect(findSolidOverlaps([a, b])).toEqual([]);
  });

  it('skips XZ overlap when Y intervals do not intersect', () => {
    const a = fp({
      id: 'a',
      label: 'low',
      minX: 0,
      maxX: 4,
      minZ: 0,
      maxZ: 4,
      minY: 0,
      maxY: 1,
      kind: 'composition',
    });
    const b = fp({
      id: 'b',
      label: 'high',
      minX: 1,
      maxX: 3,
      minZ: 1,
      maxZ: 3,
      minY: 10,
      maxY: 12,
      kind: 'composition',
    });
    expect(findSolidOverlaps([a, b])).toEqual([]);
  });

  it('tolerates shallow penetration when overlapMax allows', () => {
    // Wall-like: 4×1 boxes overlapping 0.08 m along Z (depth=min(ox,oz)=0.08).
    const a = fp({
      id: 'a',
      label: 'wall-a',
      minX: 0,
      maxX: 4,
      minZ: 0,
      maxZ: 1,
      kind: 'gameobject',
      overlapMax: 0.1,
    });
    const b = fp({
      id: 'b',
      label: 'wall-b',
      minX: 3.5,
      maxX: 7.5,
      minZ: 0.92,
      maxZ: 1.92,
      kind: 'gameobject',
    });
    expect(findSolidOverlaps([a, b])).toEqual([]);
  });

  it('still errors when penetration exceeds overlapMax', () => {
    const a = fp({
      id: 'a',
      label: 'wall-a',
      minX: 0,
      maxX: 4,
      minZ: 0,
      maxZ: 1,
      kind: 'gameobject',
      overlapMax: 0.1,
    });
    const b = fp({
      id: 'b',
      label: 'wall-b',
      minX: 1,
      maxX: 5,
      minZ: 0.2,
      maxZ: 1.2,
      kind: 'gameobject',
    });
    // ox=3, oz=0.8 → depth=0.8 > 0.1
    expect(findSolidOverlaps([a, b]).length).toBe(1);
  });

  it('default overlapMax 0 stays strict', () => {
    // Shallow but area > OVERLAP_EPS_M2 (0.05): ox=0.2 oz=2 → 0.4 m², depth=0.2.
    const a = fp({
      id: 'a',
      label: 'A',
      minX: 0,
      maxX: 2,
      minZ: 0,
      maxZ: 2,
      kind: 'composition',
    });
    const b = fp({
      id: 'b',
      label: 'B',
      minX: 1.8,
      maxX: 3.8,
      minZ: 0,
      maxZ: 2,
      kind: 'composition',
    });
    expect(findSolidOverlaps([a, b]).length).toBe(1);
  });
});

describe('findGroundOverlaps', () => {
  it('warns when solid overlaps pad', () => {
    const solid = fp({
      id: 's',
      label: 'building',
      minX: 0,
      maxX: 4,
      minZ: 0,
      maxZ: 4,
      kind: 'composition',
    });
    const pad = fp({
      id: 'p',
      label: 'plaza',
      minX: 2,
      maxX: 6,
      minZ: 2,
      maxZ: 6,
      kind: 'pad',
    });
    const issues = findGroundOverlaps([solid, pad]);
    expect(issues.length).toBe(1);
    expect(issues[0]!.severity).toBe('warn');
    expect(issues[0]!.message).toContain('solid∩ground');
  });
});

describe('analyzeWorld', () => {
  it('detects solid Composition overlap', async () => {
    const { root, publicDir } = await makeFixture({
      'world.xml': `<world>
  <Composition name="left" place="at: 0 0" body="fixed" collider="auto">
    <Box pos="0 1 0" size="4 2 4" color="#888"></Box>
  </Composition>
  <Composition name="right" place="at: 2 2" body="fixed" collider="auto">
    <Box pos="0 1 0" size="4 2 4" color="#888"></Box>
  </Composition>
</world>
`,
    });
    const result = await analyzeWorld({
      entry: path.join(root, 'world.xml'),
      publicDir,
    });
    expect(result.errorCount).toBeGreaterThanOrEqual(1);
    expect(result.issues.some((i) => i.code === 'overlap')).toBe(true);
    expect(result.footprintCount).toBe(2);
  });

  it('honours overlap-max on Composition for shallow joints', async () => {
    // Two 4×1 wall boxes: centres 3.92 apart → 0.08 m overlap along X.
    const { root, publicDir } = await makeFixture({
      'world.xml': `<world>
  <Composition name="wall-a" place="at: 0 0" body="fixed" collider="auto" overlap-max="0.1">
    <Box pos="0 1 0" size="4 2 1" color="#888"></Box>
  </Composition>
  <Composition name="wall-b" place="at: 3.92 0" body="fixed" collider="auto" overlap-max="0.1">
    <Box pos="0 1 0" size="4 2 1" color="#888"></Box>
  </Composition>
</world>
`,
    });
    const result = await analyzeWorld({
      entry: path.join(root, 'world.xml'),
      publicDir,
    });
    expect(
      result.issues.filter(
        (i) => i.code === 'overlap' && i.severity === 'error'
      )
    ).toEqual([]);
    expect(result.footprintCount).toBe(2);
  });

  it('does not treat Pad-only Composition as solid', async () => {
    const { root, publicDir } = await makeFixture({
      'world.xml': `<world>
  <Composition place="at: 0 0" body="fixed" collider="none">
    <Pad pos="0 0 0" size="16 16" color="#fff"></Pad>
  </Composition>
  <Composition place="at: 1 1" body="fixed" collider="none">
    <Pad pos="0 0 0" size="16 16" color="#fff"></Pad>
  </Composition>
</world>
`,
    });
    const result = await analyzeWorld({
      entry: path.join(root, 'world.xml'),
      publicDir,
    });
    expect(
      result.issues.filter(
        (i) => i.code === 'overlap' && i.severity === 'error'
      )
    ).toEqual([]);
    expect(result.footprintCount).toBe(0);
  });

  it('errors on broken Include', async () => {
    const { root, publicDir } = await makeFixture({
      'world.xml': `<world>
  <Include src="/world/missing.xml"></Include>
</world>
`,
    });
    const result = await analyzeWorld({
      entry: path.join(root, 'world.xml'),
      publicDir,
    });
    expect(result.errorCount).toBeGreaterThanOrEqual(1);
    expect(result.issues.some((i) => i.code === 'include')).toBe(true);
  });

  it('errors on missing primary asset URL', async () => {
    const { root, publicDir } = await makeFixture({
      'world.xml': `<world>
  <GameObject name="ghost" place="at: 0 0" collider="none">
    <GLTFLoader url="/assets/meshes/nope.glb"></GLTFLoader>
  </GameObject>
</world>
`,
    });
    const result = await analyzeWorld({
      entry: path.join(root, 'world.xml'),
      publicDir,
    });
    expect(
      result.issues.some((i) => i.code === 'asset' && i.severity === 'error')
    ).toBe(true);
  });

  it('errors on missing relative asset path', async () => {
    const { root, publicDir } = await makeFixture({
      'world.xml': `<world>
  <GameObject name="ghost" place="at: 0 0" collider="none">
    <GLTFLoader url="assets/meshes/rel_missing.glb"></GLTFLoader>
  </GameObject>
</world>
`,
    });
    const result = await analyzeWorld({
      entry: path.join(root, 'world.xml'),
      publicDir,
    });
    expect(
      result.issues.some(
        (i) =>
          i.code === 'asset' &&
          i.severity === 'error' &&
          i.message.includes('rel_missing.glb')
      )
    ).toBe(true);
  });

  it('errors on comma cell coords', async () => {
    const { root, publicDir } = await makeFixture({
      'world.xml': `<world>
  <CityGrid cell="4" origin="0 0">
    <Building at="2,1" prefab="house"></Building>
  </CityGrid>
</world>
`,
    });
    const result = await analyzeWorld({
      entry: path.join(root, 'world.xml'),
      publicDir,
    });
    expect(
      result.issues.some((i) => i.message.includes('space-separated'))
    ).toBe(true);
  });

  it('warns solid∩pad ground overlap', async () => {
    const { root, publicDir } = await makeFixture({
      'world.xml': `<world>
  <Composition name="pad" place="at: 0 0" body="fixed" collider="none">
    <Pad pos="0 0 0" size="8 8" color="#fff"></Pad>
  </Composition>
  <Composition name="box" place="at: 1 1" body="fixed" collider="auto">
    <Box pos="0 1 0" size="4 2 4" color="#888"></Box>
  </Composition>
</world>
`,
    });
    const result = await analyzeWorld({
      entry: path.join(root, 'world.xml'),
      publicDir,
    });
    expect(
      result.issues.some(
        (i) =>
          i.code === 'overlap' &&
          i.severity === 'warn' &&
          i.message.includes('solid∩ground')
      )
    ).toBe(true);
  });
});
