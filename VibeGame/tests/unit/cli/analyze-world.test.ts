import { afterEach, describe, expect, it } from 'bun:test';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import * as path from 'node:path';
import { analyzeWorld } from '../../../src/cli/analyze/run';

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
  const root = await mkdtemp(path.join(tmpdir(), 'vg-analyze-world-'));
  dirs.push(root);
  const publicDir = path.join(root, 'public');
  for (const [rel, body] of Object.entries(files)) {
    const full = path.join(root, rel);
    await mkdir(path.dirname(full), { recursive: true });
    await writeFile(full, body);
  }
  return { root, publicDir };
}

describe('analyze world checks', () => {
  it('errors on empty StaticSpawner', async () => {
    const { root, publicDir } = await makeFixture({
      'world.xml': `<world>
  <StaticSpawner count="4" region-min="0 0" region-max="10 10"></StaticSpawner>
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
          i.code === 'spawner' &&
          i.severity === 'error' &&
          i.message.includes('no child')
      )
    ).toBe(true);
  });

  it('errors on spawner without count', async () => {
    const { root, publicDir } = await makeFixture({
      'world.xml': `<world>
  <StaticSpawner region-min="0 0" region-max="10 10">
    <GameObject collider="none"></GameObject>
  </StaticSpawner>
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
          i.code === 'spawner' &&
          i.severity === 'error' &&
          i.message.includes('count')
      )
    ).toBe(true);
  });

  it('warns on duplicate names', async () => {
    const { root, publicDir } = await makeFixture({
      'world.xml': `<world>
  <GameObject name="dup" place="at: 0 0" collider="none"></GameObject>
  <GameObject name="dup" place="at: 1 1" collider="none"></GameObject>
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
          i.code === 'name' &&
          i.severity === 'warn' &&
          i.message.includes('dup')
      )
    ).toBe(true);
  });

  it('warns when Terrain has no heightmap', async () => {
    const { root, publicDir } = await makeFixture({
      'world.xml': `<world>
  <Terrain></Terrain>
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
          i.code === 'world' &&
          i.severity === 'warn' &&
          i.message.includes('heightmap')
      )
    ).toBe(true);
  });

  it('warns when player and camera missing', async () => {
    const { root, publicDir } = await makeFixture({
      'world.xml': `<world>
  <GameObject name="prop" place="at: 0 0" collider="none"></GameObject>
</world>
`,
    });
    const result = await analyzeWorld({
      entry: path.join(root, 'world.xml'),
      publicDir,
    });
    expect(
      result.issues.some(
        (i) => i.code === 'world' && i.message.includes('Player')
      )
    ).toBe(true);
    expect(
      result.issues.some(
        (i) => i.code === 'world' && i.message.includes('OrbitCamera')
      )
    ).toBe(true);
  });
});
