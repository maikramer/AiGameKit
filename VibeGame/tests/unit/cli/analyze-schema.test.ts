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
  const root = await mkdtemp(path.join(tmpdir(), 'vg-analyze-schema-'));
  dirs.push(root);
  const publicDir = path.join(root, 'public');
  for (const [rel, body] of Object.entries(files)) {
    const full = path.join(root, rel);
    await mkdir(path.dirname(full), { recursive: true });
    await writeFile(full, body);
  }
  return { root, publicDir };
}

describe('analyze schema', () => {
  it('errors on unknown recipe tag with suggestion', async () => {
    const { root, publicDir } = await makeFixture({
      'world.xml': `<world>
  <GameObjct name="typo" place="at: 0 0"></GameObjct>
</world>
`,
    });
    const result = await analyzeWorld({
      entry: path.join(root, 'world.xml'),
      publicDir,
    });
    const issue = result.issues.find((i) => i.code === 'recipe');
    expect(issue).toBeDefined();
    expect(issue!.severity).toBe('error');
    expect(issue!.message).toContain('GameObjct');
    expect(issue!.message.toLowerCase()).toContain('gameobject');
  });

  it('warns on unknown attribute', async () => {
    const { root, publicDir } = await makeFixture({
      'world.xml': `<world>
  <GameObject name="ok" place="at: 0 0" totally-fake-attr="1" collider="none"></GameObject>
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
          i.code === 'attr' &&
          i.severity === 'warn' &&
          i.message.includes('totally-fake-attr')
      )
    ).toBe(true);
  });

  it('accepts transform vector shorthand pos=', async () => {
    const { root, publicDir } = await makeFixture({
      'world.xml': `<world>
  <Group name="g" pos="1 2 3"></Group>
</world>
`,
    });
    const result = await analyzeWorld({
      entry: path.join(root, 'world.xml'),
      publicDir,
    });
    expect(
      result.issues.filter(
        (i) => i.code === 'attr' && i.message.includes('"pos"')
      )
    ).toEqual([]);
  });
});
