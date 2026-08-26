import { describe, expect, it } from 'bun:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  resolveAssetPath,
  sharedAssetRoots,
} from '../../../src/cli/analyze/assets';

/** examples/{shared-assets,my-game}/public — o layout real do monorepo. */
function layout() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'vg-pool-'));
  const examples = path.join(root, 'examples');
  const pool = path.join(
    examples,
    'shared-assets',
    'public',
    'assets',
    'meshes'
  );
  const game = path.join(examples, 'my-game', 'public');
  fs.mkdirSync(pool, { recursive: true });
  fs.mkdirSync(path.join(game, 'assets', 'meshes'), { recursive: true });
  fs.mkdirSync(path.join(game, 'world'), { recursive: true });
  fs.writeFileSync(path.join(pool, 'chapel_lod0.glb'), 'pool');
  fs.writeFileSync(path.join(game, 'world', 'city.xml'), '<Scene/>');
  return { root, game, pool };
}

describe('sharedAssetRoots', () => {
  it('finds the sibling pool from an example public dir', () => {
    const { root, game } = layout();
    const roots = sharedAssetRoots(game);
    expect(roots.length).toBeGreaterThan(0);
    expect(roots[0]).toBe(
      path.join(root, 'examples', 'shared-assets', 'public')
    );
    fs.rmSync(root, { recursive: true, force: true });
  });

  it('is empty when there is no pool beside the example', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vg-nopool-'));
    fs.mkdirSync(path.join(dir, 'public'), { recursive: true });
    expect(sharedAssetRoots(path.join(dir, 'public'))).toEqual([]);
    fs.rmSync(dir, { recursive: true, force: true });
  });
});

describe('resolveAssetPath with the pool', () => {
  it('falls back to the pool for an asset the example does not ship', () => {
    const { root, game, pool } = layout();
    const got = resolveAssetPath(
      game,
      '/assets/meshes/chapel_lod0.glb',
      sharedAssetRoots(game)
    );
    expect(got).toBe(path.join(pool, 'chapel_lod0.glb'));
    fs.rmSync(root, { recursive: true, force: true });
  });

  it('the example wins when it ships the same path', () => {
    const { root, game } = layout();
    const local = path.join(game, 'assets', 'meshes', 'chapel_lod0.glb');
    fs.writeFileSync(local, 'local');
    expect(
      resolveAssetPath(
        game,
        '/assets/meshes/chapel_lod0.glb',
        sharedAssetRoots(game)
      )
    ).toBe(local);
    fs.rmSync(root, { recursive: true, force: true });
  });

  it('reports the example path when nobody has the file', () => {
    const { root, game } = layout();
    expect(
      resolveAssetPath(game, '/assets/meshes/ghost.glb', sharedAssetRoots(game))
    ).toBe(path.join(game, 'assets/meshes/ghost.glb'));
    fs.rmSync(root, { recursive: true, force: true });
  });

  it('still ignores remote URLs', () => {
    const { root, game } = layout();
    expect(resolveAssetPath(game, 'https://cdn.example/a.glb')).toBeNull();
    fs.rmSync(root, { recursive: true, force: true });
  });
});
