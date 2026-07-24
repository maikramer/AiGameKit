import { afterEach, describe, expect, it } from 'bun:test';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import * as path from 'node:path';
import { analyzeWorld } from '../../../src/cli/analyze/run';
import { findSolidOverlaps } from '../../../src/cli/analyze/overlap';
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

describe('findSolidOverlaps', () => {
  it('reports overlapping footprints', () => {
    const a: Footprint = {
      id: 'a',
      label: 'A',
      minX: 0,
      maxX: 4,
      minZ: 0,
      maxZ: 4,
      kind: 'composition',
    };
    const b: Footprint = {
      id: 'b',
      label: 'B',
      minX: 2,
      maxX: 6,
      minZ: 2,
      maxZ: 6,
      kind: 'composition',
    };
    const issues = findSolidOverlaps([a, b]);
    expect(issues.length).toBe(1);
    expect(issues[0]!.code).toBe('overlap');
    expect(issues[0]!.severity).toBe('error');
  });

  it('ignores non-overlapping footprints', () => {
    const a: Footprint = {
      id: 'a',
      label: 'A',
      minX: 0,
      maxX: 1,
      minZ: 0,
      maxZ: 1,
      kind: 'composition',
    };
    const b: Footprint = {
      id: 'b',
      label: 'B',
      minX: 10,
      maxX: 11,
      minZ: 10,
      maxZ: 11,
      kind: 'composition',
    };
    expect(findSolidOverlaps([a, b])).toEqual([]);
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
    expect(result.issues.filter((i) => i.code === 'overlap')).toEqual([]);
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
});
