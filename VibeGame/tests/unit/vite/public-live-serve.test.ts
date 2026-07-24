import { describe, expect, it } from 'bun:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { resolvePublicFile } from '../../../src/vite/public-live-serve';

describe('resolvePublicFile', () => {
  it('resolves a file under publicDir', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vg-pub-'));
    const assets = path.join(dir, 'assets', 'meshes');
    fs.mkdirSync(assets, { recursive: true });
    const file = path.join(assets, 'new_hull.glb');
    fs.writeFileSync(file, 'glTF');

    expect(resolvePublicFile(dir, '/assets/meshes/new_hull.glb')).toBe(file);
    expect(resolvePublicFile(dir, '/assets/meshes/missing.glb')).toBeNull();

    fs.rmSync(dir, { recursive: true, force: true });
  });

  it('rejects path traversal outside publicDir', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vg-pub-'));
    const outside = path.join(os.tmpdir(), `vg-secret-${Date.now()}.txt`);
    fs.writeFileSync(outside, 'secret');
    const rel = path.relative(dir, outside).replace(/\\/g, '/');
    expect(resolvePublicFile(dir, '/' + rel)).toBeNull();
    fs.rmSync(dir, { recursive: true, force: true });
    fs.rmSync(outside, { force: true });
  });

  it('ignores vite internals', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vg-pub-'));
    expect(resolvePublicFile(dir, '/@vite/client')).toBeNull();
    expect(resolvePublicFile(dir, '/node_modules/foo')).toBeNull();
    fs.rmSync(dir, { recursive: true, force: true });
  });
});
