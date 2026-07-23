import { describe, expect, it } from 'bun:test';
import * as THREE from 'three';
import { maybePatchInstanceVariationMaterial } from '../../../src/plugins/spawn-variation/material-patch';

describe('maybePatchInstanceVariationMaterial', () => {
  it('injects brightness/contrast after map_fragment', () => {
    const mat = new THREE.MeshStandardMaterial();
    maybePatchInstanceVariationMaterial(mat);
    const shader = {
      vertexShader: 'void main() {}',
      fragmentShader: `
        void main() {
          #include <color_fragment>
          #include <map_fragment>
          #include <opaque_fragment>
        }
      `,
      uniforms: {},
    };
    mat.onBeforeCompile!(shader as never, null as never);
    expect(shader.fragmentShader).toContain('#include <map_fragment>');
    expect(
      shader.fragmentShader.indexOf('#include <map_fragment>')
    ).toBeLessThan(shader.fragmentShader.indexOf('uVarContrast'));
    expect(shader.fragmentShader).toContain('uVarBrightness');
    expect(shader.fragmentShader).toContain('USE_INSTANCING_INDIRECT');
  });
});
