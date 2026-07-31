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
  const root = await mkdtemp(path.join(tmpdir(), 'vg-analyze-scripts-'));
  dirs.push(root);
  const publicDir = path.join(root, 'public');
  for (const [rel, body] of Object.entries(files)) {
    const full = path.join(root, rel);
    await mkdir(path.dirname(full), { recursive: true });
    await writeFile(full, body);
  }
  return { root, publicDir };
}

describe('analyze scripts', () => {
  it('errors when script file missing under scripts-dir', async () => {
    const { root, publicDir } = await makeFixture({
      'world.xml': `<world>
  <GameObject name="npc" place="at: 0 0" script="missing_brain.ts" collider="none"></GameObject>
</world>
`,
      'src/scripts/present.ts': 'export default {};\n',
    });
    const result = await analyzeWorld({
      entry: path.join(root, 'world.xml'),
      publicDir,
      scriptsDir: path.join(root, 'src', 'scripts'),
    });
    expect(
      result.issues.some(
        (i) =>
          i.code === 'script' &&
          i.severity === 'error' &&
          i.message.includes('missing_brain.ts')
      )
    ).toBe(true);
  });

  it('accepts existing script by basename', async () => {
    const { root, publicDir } = await makeFixture({
      'world.xml': `<world>
  <GameObject name="npc" place="at: 0 0" script="present.ts" collider="none"></GameObject>
</world>
`,
      'src/scripts/present.ts': 'export default {};\n',
    });
    const result = await analyzeWorld({
      entry: path.join(root, 'world.xml'),
      publicDir,
      scriptsDir: path.join(root, 'src', 'scripts'),
    });
    expect(result.issues.filter((i) => i.code === 'script')).toEqual([]);
  });
});
