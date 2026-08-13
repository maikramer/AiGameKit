import { describe, expect, it } from 'bun:test';
import * as THREE from 'three';
import { TrackSpline } from '../../../src/plugins/racing/spline';
import { buildTrackMeshes } from '../../../src/plugins/racing/track-geometry';

/** Straight track along +X at a constant height. */
function straightTrack(deckY: number): TrackSpline {
  return new TrackSpline(
    [
      { x: -200, y: deckY, z: 0, width: 12 },
      { x: 0, y: deckY, z: 0, width: 12 },
      { x: 200, y: deckY, z: 0, width: 12 },
    ],
    { closed: false, step: 4 }
  );
}

/** Ground that drops into a valley between x = -100 and x = 100. */
const valleyGround = (x: number): number => (x > -100 && x < 100 ? 0 : 20);

function bounds(mesh: THREE.Mesh): THREE.Box3 {
  mesh.geometry.computeBoundingBox();
  return mesh.geometry.boundingBox!;
}

describe('buildTrackMeshes — viaduct', () => {
  it('builds nothing when no viaduct options are given', () => {
    const meshes = buildTrackMeshes(straightTrack(25), 3);
    expect(meshes.viaduct).toBeNull();
    meshes.dispose();
  });

  it('builds nothing when the track never leaves the ground', () => {
    const meshes = buildTrackMeshes(
      straightTrack(21),
      3,
      {},
      {
        groundYAt: () => 20,
        clearance: 6,
      }
    );
    expect(meshes.viaduct).toBeNull();
    meshes.dispose();
  });

  it('builds deck and pylons over the valley only', () => {
    const meshes = buildTrackMeshes(
      straightTrack(25),
      3,
      {},
      {
        groundYAt: (x) => valleyGround(x),
        clearance: 6,
        pylonSpacing: 40,
      }
    );
    expect(meshes.viaduct).not.toBeNull();
    const box = bounds(meshes.viaduct!);
    // Structure stays inside the valley (plus the ramp station either side).
    expect(box.min.x).toBeGreaterThan(-140);
    expect(box.max.x).toBeLessThan(140);
    // Pylons reach the valley floor (and sink a little under it).
    expect(box.min.y).toBeLessThan(0);
    // Nothing pokes above the deck.
    expect(box.max.y).toBeLessThanOrEqual(25);
    meshes.dispose();
  });

  it('adds the viaduct to the track group', () => {
    const meshes = buildTrackMeshes(
      straightTrack(25),
      3,
      {},
      {
        groundYAt: (x) => valleyGround(x),
        clearance: 6,
      }
    );
    expect(meshes.group.getObjectByName('TrackViaduct')).toBe(meshes.viaduct!);
    meshes.dispose();
  });

  it('skips a pylon that would land on another pass of the track', () => {
    // A hairpin whose return arm runs under the outbound span: the outbound
    // arm flies at y=25 over the valley, the return arm is on the ground.
    const spline = new TrackSpline(
      [
        { x: -200, y: 25, z: 0, width: 12 },
        { x: 0, y: 25, z: 0, width: 12 },
        { x: 200, y: 25, z: 0, width: 12 },
        { x: 200, y: 20, z: 60, width: 12 },
        { x: 0, y: 20, z: 0, width: 12 },
        { x: -200, y: 20, z: -60, width: 12 },
      ],
      { closed: false, step: 4 }
    );
    const withGuard = buildTrackMeshes(
      spline,
      3,
      {},
      {
        groundYAt: (x) => valleyGround(x),
        clearance: 6,
        pylonSpacing: 20,
      }
    );
    // Every pylon must clear the road it would otherwise block: nothing in the
    // structure may sit within a lane width of the return arm's centre.
    const pos = withGuard.viaduct!.geometry.getAttribute('position');
    let onRoad = 0;
    for (let i = 0; i < pos.count; i++) {
      const x = pos.getX(i);
      const y = pos.getY(i);
      const z = pos.getZ(i);
      // Below the deck, near the return arm's line (z ≈ 0 at x ≈ 0).
      if (y < 18 && Math.abs(z) < 6 && Math.abs(x) < 30) onRoad++;
    }
    expect(onRoad).toBe(0);
    withGuard.dispose();
  });
});
