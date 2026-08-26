import { describe, expect, it } from 'bun:test';
import * as THREE from 'three';
import { applyDefaultShadowFlags } from '../../../src/extras/gltf-bridge';

/**
 * Frustum culling for animated characters.
 *
 * three tests a SkinnedMesh against its *bind pose* bounds, which is why
 * culling used to be switched off wholesale here: characters blinked out as
 * soon as the bind origin left the view. The cure was worse than the disease —
 * in the RPG village 93 off-screen characters (1.9M triangles) were submitted
 * every frame. The mesh now carries its own inflated bounding sphere instead,
 * which three prefers over the geometry's, so the test stays correct in any
 * pose and still culls what is genuinely off-screen.
 */
function makeSkinned(radius: number): THREE.SkinnedMesh {
  const geometry = new THREE.BufferGeometry();
  const positions = new Float32Array([
    -radius,
    -radius,
    0,
    radius,
    -radius,
    0,
    0,
    radius,
    0,
  ]);
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  const mesh = new THREE.SkinnedMesh(
    geometry,
    new THREE.MeshStandardMaterial()
  );
  return mesh;
}

describe('applyDefaultShadowFlags — skinned meshes', () => {
  it('keeps frustum culling on and carries an inflated sphere', () => {
    const mesh = makeSkinned(1);
    applyDefaultShadowFlags(mesh);

    expect(mesh.frustumCulled).toBe(true);
    expect(mesh.boundingSphere).not.toBeNull();
    const geometryRadius = mesh.geometry.boundingSphere!.radius;
    // Inflated, so a pose that reaches past the bind silhouette is still
    // inside the volume the frustum test uses.
    expect(mesh.boundingSphere!.radius).toBeGreaterThan(geometryRadius);
  });

  it('does not alias the geometry sphere it was derived from', () => {
    const mesh = makeSkinned(1);
    applyDefaultShadowFlags(mesh);
    // Geometries are shared across clones of the same GLB; inflating the
    // shared one would grow without bound as characters are spawned.
    expect(mesh.boundingSphere).not.toBe(mesh.geometry.boundingSphere);
    const fresh = mesh.geometry.clone();
    fresh.computeBoundingSphere();
    expect(mesh.geometry.boundingSphere!.radius).toBeCloseTo(
      fresh.boundingSphere!.radius,
      5
    );
  });

  it('falls back to drawing a mesh with no usable bounds', () => {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute(
      'position',
      new THREE.BufferAttribute(new Float32Array([0, 0, 0]), 3)
    );
    const mesh = new THREE.SkinnedMesh(
      geometry,
      new THREE.MeshStandardMaterial()
    );
    applyDefaultShadowFlags(mesh);
    // A degenerate sphere would cull the mesh from every angle; keep it.
    expect(mesh.frustumCulled).toBe(false);
  });

  it('still turns shadows on for skinned and static meshes alike', () => {
    const root = new THREE.Group();
    const skinned = makeSkinned(1);
    const staticMesh = new THREE.Mesh(
      new THREE.BoxGeometry(1, 1, 1),
      new THREE.MeshStandardMaterial()
    );
    root.add(skinned, staticMesh);
    applyDefaultShadowFlags(root);

    expect(skinned.castShadow).toBe(true);
    expect(skinned.receiveShadow).toBe(true);
    expect(staticMesh.castShadow).toBe(true);
    expect(staticMesh.frustumCulled).toBe(true);
  });
});
