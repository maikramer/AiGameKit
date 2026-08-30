import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import * as path from 'path';
import {
  Parent,
  RpgPlugins,
  State,
  parseXMLToEntities,
} from 'aigamekit-vibegame';
import { DefaultPlugins } from 'aigamekit-vibegame/defaults';
import { loadWorldFromFile } from '../../../src/cli/headless';

const PUBLIC_DIR = path.resolve(
  import.meta.dir,
  '../../../examples/simple-rpg/public'
);

describe('loadWorldFromFile with Includes', () => {
  let state: State;

  beforeEach(() => {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser;
    state = new State();
    // Discordia's districts use RPG recipes (e.g. <DialogueNPC>) — mirror the
    // game's plugin stack: DefaultPlugins + RpgPlugins.
    for (const p of [...DefaultPlugins, ...RpgPlugins]) {
      state.registerPlugin(p);
    }
  });

  it('loads town-demo.xml stub group', async () => {
    const file = path.join(PUBLIC_DIR, 'world/cities/town-demo.xml');
    await loadWorldFromFile(state, file, { publicDir: PUBLIC_DIR });
    expect(state.getEntityByName('town')).not.toBeNull();
  });

  it('expands shell with nested Includes (town + stub)', async () => {
    const { mkdtemp, writeFile, rm } = await import('fs/promises');
    const { tmpdir } = await import('os');
    const dir = await mkdtemp(path.join(tmpdir(), 'vg-include-'));
    try {
      const publicDir = path.join(dir, 'public');
      const { mkdir } = await import('fs/promises');
      await mkdir(path.join(publicDir, 'world'), { recursive: true });
      await writeFile(
        path.join(publicDir, 'world/a.xml'),
        '<Group name="from-a"></Group>\n'
      );
      const shell = path.join(dir, 'shell.xml');
      await writeFile(
        shell,
        `<world>
  <Include src="/world/a.xml"></Include>
  <Group name="local"></Group>
</world>
`
      );
      await loadWorldFromFile(state, shell, { publicDir });
      expect(state.getEntityByName('from-a')).not.toBeNull();
      expect(state.getEntityByName('local')).not.toBeNull();
      void Parent;
      void parseXMLToEntities;
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });

  it('loads discordia shell with nested district Includes', async () => {
    const file = path.join(PUBLIC_DIR, 'world/cities/discordia.xml');
    await loadWorldFromFile(state, file, { publicDir: PUBLIC_DIR });
    expect(state.getEntityByName('city')).not.toBeNull();
    expect(state.getEntityByName('city.houses')).not.toBeNull();
    expect(state.getEntityByName('city.plaza')).not.toBeNull();
    expect(state.getEntityByName('city.landmarks')).not.toBeNull();
    expect(state.getEntityByName('city.walls')).not.toBeNull();
    expect(state.getEntityByName('house_a')).not.toBeNull();
    expect(state.getEntityByName('well')).not.toBeNull();
  });
});
