import { describe, expect, it } from 'bun:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  collectSharedFiles,
  DEFAULT_SHARED_EXCLUDE,
  isSharedAssetUrl,
  resolveSharedAsset,
} from '../../../src/vite/shared-assets';

function pool(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vg-shared-'));
  fs.mkdirSync(path.join(dir, 'meshes', 'village'), { recursive: true });
  fs.mkdirSync(path.join(dir, 'meshes', 'village', '_intermediate'), {
    recursive: true,
  });
  fs.mkdirSync(path.join(dir, 'images', 'village'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'meshes/village/chapel_lod0.glb'), 'glTF');
  fs.writeFileSync(
    path.join(dir, 'meshes/village/_intermediate/chapel_painted.glb'),
    'huge'
  );
  fs.writeFileSync(path.join(dir, 'images/village/chapel.png'), 'png');
  return dir;
}

describe('isSharedAssetUrl', () => {
  it('claims only the configured prefixes', () => {
    expect(isSharedAssetUrl('/assets/meshes/village/chapel_lod0.glb')).toBe(
      true
    );
    expect(isSharedAssetUrl('/assets/images/village/chapel.png?v=2')).toBe(
      true
    );
    expect(isSharedAssetUrl('/world/cities/discordia/chapel.xml')).toBe(false);
    expect(isSharedAssetUrl('/src/main.ts')).toBe(false);
  });

  it('honours custom prefixes', () => {
    expect(isSharedAssetUrl('/pool/a.glb', ['/pool/'])).toBe(true);
    expect(isSharedAssetUrl('/assets/a.glb', ['/pool/'])).toBe(false);
  });
});

describe('resolveSharedAsset', () => {
  it('maps /assets/** onto the pool root', () => {
    const dir = pool();
    expect(
      resolveSharedAsset(dir, '/assets/meshes/village/chapel_lod0.glb')
    ).toBe(path.join(dir, 'meshes/village/chapel_lod0.glb'));
    expect(resolveSharedAsset(dir, '/assets/images/village/chapel.png')).toBe(
      path.join(dir, 'images/village/chapel.png')
    );
    fs.rmSync(dir, { recursive: true, force: true });
  });

  it('ignores URLs outside the prefix, so world XML still hits the example', () => {
    const dir = pool();
    expect(
      resolveSharedAsset(dir, '/world/cities/discordia/chapel.xml')
    ).toBeNull();
    fs.rmSync(dir, { recursive: true, force: true });
  });

  it('returns null for a file the pool does not have', () => {
    const dir = pool();
    expect(
      resolveSharedAsset(dir, '/assets/meshes/village/ghost.glb')
    ).toBeNull();
    fs.rmSync(dir, { recursive: true, force: true });
  });

  it('rejects path traversal out of the pool', () => {
    const dir = pool();
    const outside = path.join(os.tmpdir(), `vg-secret-${Date.now()}.txt`);
    fs.writeFileSync(outside, 'secret');
    const rel = path.relative(dir, outside).replace(/\\/g, '/');
    expect(resolveSharedAsset(dir, `/assets/${rel}`)).toBeNull();
    fs.rmSync(dir, { recursive: true, force: true });
    fs.rmSync(outside, { force: true });
  });
});

describe('collectSharedFiles', () => {
  it('walks the pool and skips pipeline intermediates', () => {
    const dir = pool();
    const rels = collectSharedFiles(dir)
      .map(([, rel]) => rel)
      .sort();
    expect(rels).toEqual([
      'images/village/chapel.png',
      'meshes/village/chapel_lod0.glb',
    ]);
    fs.rmSync(dir, { recursive: true, force: true });
  });

  it('_intermediate is excluded by default', () => {
    expect(DEFAULT_SHARED_EXCLUDE).toContain('_intermediate');
  });

  it('returns absolute sources usable for copying', () => {
    const dir = pool();
    for (const [src] of collectSharedFiles(dir)) {
      expect(path.isAbsolute(src)).toBe(true);
      expect(fs.existsSync(src)).toBe(true);
    }
    fs.rmSync(dir, { recursive: true, force: true });
  });

  it('is empty for a pool that does not exist', () => {
    expect(collectSharedFiles(path.join(os.tmpdir(), 'vg-nope-xyz'))).toEqual(
      []
    );
  });
});
