import { describe, expect, it } from 'bun:test';
import { Bone, BoxGeometry, Group, Mesh } from 'three';
import { computePlayerFootAnchor } from '../../../src/plugins/player/player-foot-anchor';

describe('computePlayerFootAnchor', () => {
  it('uses ball bone Y as sole when present (not pelvis)', () => {
    const root = new Group();
    const pelvis = new Bone();
    pelvis.name = 'pelvis';
    pelvis.position.y = 0.82;
    const ballL = new Bone();
    ballL.name = 'ball_l';
    ballL.position.y = 0.01;
    const ballR = new Bone();
    ballR.name = 'ball_r';
    ballR.position.y = 0.01;
    root.add(pelvis, ballL, ballR);

    // Mesh AABB starts at pelvis height — without bones we'd plant the waist.
    const mesh = new Mesh(new BoxGeometry(0.5, 0.8, 0.3));
    mesh.position.y = 0.82 + 0.4;
    root.add(mesh);
    root.updateMatrixWorld(true);

    const anchor = computePlayerFootAnchor(root);
    expect(anchor.soleY).toBeCloseTo(0.01, 3);
    // yOffset = -soleY (no plant sink fudge)
    expect(anchor.yOffset).toBeCloseTo(-0.01, 3);
  });

  it('falls back to mesh minY when no foot bones', () => {
    const root = new Group();
    const mesh = new Mesh(new BoxGeometry(1, 2, 1));
    mesh.position.y = 1; // geometry ±1 → world minY=0
    root.add(mesh);
    root.updateMatrixWorld(true);

    const anchor = computePlayerFootAnchor(root);
    expect(anchor.soleY).toBeCloseTo(0, 2);
    expect(anchor.yOffset).toBeCloseTo(0, 2);
  });
});
