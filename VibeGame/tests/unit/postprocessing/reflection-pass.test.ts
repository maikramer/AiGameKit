import { describe, expect, it } from 'bun:test';
import * as THREE from 'three';
import { ReflectionPass } from '../../../src/plugins/postprocessing/reflection-pass';

/**
 * Screen-space reflections. The pass is mostly GLSL — what is testable off-GPU
 * is the bookkeeping around it, and every item here is something that broke a
 * frame while it was being written:
 *
 *  - the reflective set is driven by a live array the effect mutates, so the
 *    pass must read it each frame instead of snapshotting it;
 *  - meshes are marked with a private layer, which has to be *removed* again
 *    when they leave the set (or a mesh that stopped being reflective keeps
 *    rendering into the normal buffer forever);
 *  - the reflection buffer is scaled, and the composite's blur radius is
 *    derived from that scale.
 */

function makeMesh(name: string, roughness: number): THREE.Mesh {
  const mesh = new THREE.Mesh(
    new THREE.PlaneGeometry(1, 1),
    new THREE.MeshStandardMaterial({ roughness })
  );
  mesh.name = name;
  return mesh;
}

function makePass(selects: THREE.Mesh[], resolutionScale = 0.5) {
  const scene = new THREE.Scene();
  for (const mesh of selects) scene.add(mesh);
  const camera = new THREE.PerspectiveCamera(70, 16 / 9, 0.1, 1000);
  const renderer = {} as unknown as THREE.WebGLRenderer;
  return new ReflectionPass({
    renderer,
    scene,
    camera,
    selects,
    resolutionScale,
  });
}

/** The pass keeps `syncLayers` private; drive it the way `render` would. */
function syncLayers(pass: ReflectionPass): void {
  (pass as unknown as { syncLayers(): void }).syncLayers();
}

describe('ReflectionPass', () => {
  it('asks the composer for a depth texture', () => {
    const pass = makePass([]);
    expect(pass.needsDepthTexture).toBe(true);
    expect(pass.needsSwap).toBe(true);
    pass.dispose();
  });

  it('sizes its buffers by the resolution scale', () => {
    const pass = makePass([], 0.5);
    pass.setSize(1600, 900);
    const target = (
      pass as unknown as { reflectionTarget: THREE.WebGLRenderTarget }
    ).reflectionTarget;
    expect(target.width).toBe(800);
    expect(target.height).toBe(450);
    pass.dispose();
  });

  it('clamps an absurd resolution scale instead of allocating nothing', () => {
    const pass = makePass([], 0.01);
    pass.setSize(1600, 900);
    const target = (
      pass as unknown as { reflectionTarget: THREE.WebGLRenderTarget }
    ).reflectionTarget;
    // Floor is 0.25 — a 4x4 reflection buffer is not a quality setting.
    expect(target.width).toBe(400);
    pass.dispose();
  });

  it('derives the composite blur texel from the reflection buffer, not the screen', () => {
    const pass = makePass([], 0.5);
    pass.setSize(1600, 900);
    const composite = (
      pass as unknown as { compositeMaterial: THREE.ShaderMaterial }
    ).compositeMaterial;
    const texel = composite.uniforms.uReflectionTexel.value as THREE.Vector2;
    expect(texel.x).toBeCloseTo(1 / 800, 6);
    expect(texel.y).toBeCloseTo(1 / 450, 6);
    pass.dispose();
  });

  it('marks reflective meshes with its private layer', () => {
    const road = makeMesh('road', 0.6);
    const pass = makePass([road]);
    syncLayers(pass);
    // Layer 11, and the default layer 0 must survive — the mesh still has to
    // render in the beauty pass.
    expect(road.layers.isEnabled(11)).toBe(true);
    expect(road.layers.isEnabled(0)).toBe(true);
    pass.dispose();
  });

  it('un-marks a mesh that dropped out of the reflective set', () => {
    const road = makeMesh('road', 0.6);
    const glass = makeMesh('glass', 0.05);
    const selects = [road, glass];
    const pass = makePass(selects);
    syncLayers(pass);
    expect(glass.layers.isEnabled(11)).toBe(true);

    selects.splice(1, 1);
    syncLayers(pass);
    expect(glass.layers.isEnabled(11)).toBe(false);
    expect(road.layers.isEnabled(11)).toBe(true);
    pass.dispose();
  });

  it('clears every layer mark on dispose', () => {
    const road = makeMesh('road', 0.6);
    const pass = makePass([road]);
    syncLayers(pass);
    pass.dispose();
    expect(road.layers.isEnabled(11)).toBe(false);
  });

  it('applies live settings to the march uniforms', () => {
    const pass = makePass([]);
    pass.configure({
      intensity: 0.7,
      maxDistance: 70,
      thickness: 0.6,
      fresnelPower: 1.5,
      jitter: 0.1,
    });
    const material = (
      pass as unknown as { reflectionMaterial: THREE.ShaderMaterial }
    ).reflectionMaterial;
    expect(material.uniforms.uIntensity.value).toBe(0.7);
    expect(material.uniforms.uMaxDistance.value).toBe(70);
    expect(material.uniforms.uThickness.value).toBe(0.6);
    expect(material.uniforms.uFresnelPower.value).toBe(1.5);
    expect(material.uniforms.uJitter.value).toBe(0.1);
    pass.dispose();
  });

  it('leaves optional settings alone when they are omitted', () => {
    const pass = makePass([]);
    const material = (
      pass as unknown as { reflectionMaterial: THREE.ShaderMaterial }
    ).reflectionMaterial;
    const fresnel = material.uniforms.uFresnelPower.value;
    pass.configure({ intensity: 0.5, maxDistance: 40, thickness: 0.4 });
    expect(material.uniforms.uFresnelPower.value).toBe(fresnel);
    pass.dispose();
  });

  it('binds the composer depth texture to the march', () => {
    const pass = makePass([]);
    const depth = new THREE.DepthTexture(4, 4);
    pass.setDepthTexture(depth);
    const material = (
      pass as unknown as { reflectionMaterial: THREE.ShaderMaterial }
    ).reflectionMaterial;
    expect(material.uniforms.tDepth.value).toBe(depth);
    pass.dispose();
  });
});
