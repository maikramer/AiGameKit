import * as THREE from 'three';

interface TerrainMaterialContext {
  heightMap: THREE.Texture;
  diffuseTexture: THREE.Texture | null;
  maxHeight: number;
  worldSize: number;
  resolution: number;
  wireframe: boolean;
  showChunkBorders: boolean;
  skirtDepth: number;
  skirtWidth: number;
  normalStrength: number;
  heightSmoothing: number;
  heightSmoothingSpread: number;
}

export class WebGLTerrainMaterialProvider {
  private material: THREE.MeshStandardMaterial | null = null;
  private heightMapUniform = { value: null as THREE.Texture | null };
  private maxHeightUniform = { value: 250 };
  private normalStrengthUniform = { value: 1.0 };
  private skirtDepthUniform = { value: 1.0 };
  private skirtWidthUniform = { value: 0.015625 };
  private worldSizeUniform = { value: 2048 };
  private heightMapSizeUniform = { value: 2048 };

  createMaterial(context: TerrainMaterialContext): THREE.Material {
    const hmImage = context.heightMap.image as
      | HTMLImageElement
      | HTMLCanvasElement
      | OffscreenCanvas
      | undefined;
    const hmSize =
      hmImage && typeof hmImage.width === 'number' && hmImage.width > 0
        ? hmImage.width
        : 2048;

    this.heightMapUniform.value = context.heightMap;
    this.maxHeightUniform.value = context.maxHeight;
    this.normalStrengthUniform.value = Math.max(0, context.normalStrength);
    this.skirtDepthUniform.value = Math.max(0, context.skirtDepth);
    this.skirtWidthUniform.value = Math.max(
      0.0001,
      Math.min(0.49, context.skirtWidth)
    );
    this.worldSizeUniform.value = context.worldSize;
    this.heightMapSizeUniform.value = hmSize;

    const material = new THREE.MeshStandardMaterial({
      color: context.diffuseTexture ? 0xffffff : 0x4a7a3a,
      map: context.diffuseTexture ?? undefined,
      roughness: 0.85,
      metalness: 0.0,
      side: THREE.DoubleSide,
      wireframe: context.wireframe,
    });

    material.onBeforeCompile = (shader) => {
      shader.uniforms.tHeightMap = this.heightMapUniform;
      shader.uniforms.uMaxHeight = this.maxHeightUniform;
      shader.uniforms.uNormalStrength = this.normalStrengthUniform;
      shader.uniforms.uSkirtDepth = this.skirtDepthUniform;
      shader.uniforms.uSkirtWidth = this.skirtWidthUniform;
      shader.uniforms.uWorldSize = this.worldSizeUniform;
      shader.uniforms.uHeightMapSize = this.heightMapSizeUniform;

      shader.vertexShader = shader.vertexShader.replace(
        '#include <common>',
        `#include <common>
        attribute vec3 instanceUVTransform;
        attribute vec4 instanceEdgeSkirt;
        uniform sampler2D tHeightMap;
        uniform float uMaxHeight;
        uniform float uNormalStrength;
        uniform float uSkirtDepth;
        uniform float uSkirtWidth;
        uniform float uWorldSize;
        uniform float uHeightMapSize;
        `
      );

      shader.vertexShader = shader.vertexShader.replace(
        '#include <beginnormal_vertex>',
        `#include <beginnormal_vertex>
        vec2 _nUV = vec2(uv.x, 1.0 - uv.y);
        vec2 _nSUV = _nUV * instanceUVTransform.x;
        vec2 _nGUV = _nSUV + vec2(instanceUVTransform.y, instanceUVTransform.z);
        float _nTS = 1.0 / uHeightMapSize;
        float _nH00 = texture2D(tHeightMap, _nGUV + vec2(-_nTS, -_nTS)).r;
        float _nH01 = texture2D(tHeightMap, _nGUV + vec2(0.0, -_nTS)).r;
        float _nH02 = texture2D(tHeightMap, _nGUV + vec2(_nTS, -_nTS)).r;
        float _nH10 = texture2D(tHeightMap, _nGUV + vec2(-_nTS, 0.0)).r;
        float _nH12 = texture2D(tHeightMap, _nGUV + vec2(_nTS, 0.0)).r;
        float _nH20 = texture2D(tHeightMap, _nGUV + vec2(-_nTS, _nTS)).r;
        float _nH21 = texture2D(tHeightMap, _nGUV + vec2(0.0, _nTS)).r;
        float _nH22 = texture2D(tHeightMap, _nGUV + vec2(_nTS, _nTS)).r;
        float _sX = _nH02 + 2.0 * _nH12 + _nH22 - _nH00 - 2.0 * _nH10 - _nH20;
        float _sZ = _nH20 + 2.0 * _nH21 + _nH22 - _nH00 - 2.0 * _nH01 - _nH02;
        float _wS = uWorldSize / uHeightMapSize;
        float _nS = uMaxHeight * uNormalStrength / _wS;
        objectNormal = normalize(vec3(-_sX * _nS, 1.0, -_sZ * _nS));
        `
      );

      shader.vertexShader = shader.vertexShader.replace(
        '#include <displacementmap_vertex>',
        `#include <displacementmap_vertex>
        vec2 _dUV = vec2(uv.x, 1.0 - uv.y);
        vec2 _dSUV = _dUV * instanceUVTransform.x;
        vec2 _dGUV = _dSUV + vec2(instanceUVTransform.y, instanceUVTransform.z);
        float _dH = texture2D(tHeightMap, _dGUV).r;
        transformed.y += _dH * uMaxHeight;
        float _skI = uSkirtWidth * 0.65;
        float _skL = (1.0 - smoothstep(_skI, uSkirtWidth, _dUV.x)) * instanceEdgeSkirt.x;
        float _skR = (1.0 - smoothstep(_skI, uSkirtWidth, 1.0 - _dUV.x)) * instanceEdgeSkirt.y;
        float _skB = (1.0 - smoothstep(_skI, uSkirtWidth, _dUV.y)) * instanceEdgeSkirt.z;
        float _skT = (1.0 - smoothstep(_skI, uSkirtWidth, 1.0 - _dUV.y)) * instanceEdgeSkirt.w;
        float _skM = clamp(_skL + _skR + _skB + _skT, 0.0, 4.0);
        transformed.y -= uSkirtDepth * _skM;
        `
      );
    };

    this.material = material;
    return material;
  }

  /** Update MeshStandardMaterial roughness/metalness (runtime-safe, no recompile). */
  setRoughness(value: number): void {
    if (this.material) {
      this.material.roughness = Math.max(0, Math.min(1, value));
    }
  }

  setMetalness(value: number): void {
    if (this.material) {
      this.material.metalness = Math.max(0, Math.min(1, value));
    }
  }

  setWireframe(enabled: boolean): void {
    if (this.material) {
      this.material.wireframe = enabled;
      this.material.needsUpdate = true;
    }
  }

  setMaxHeight(height: number): void {
    this.maxHeightUniform.value = height;
  }

  setNormalStrength(strength: number): void {
    this.normalStrengthUniform.value = Math.max(0, strength);
  }

  setSkirtDepth(depth: number): void {
    this.skirtDepthUniform.value = Math.max(0, depth);
  }

  setHeightSmoothing(_amount: number): void {}

  setHeightSmoothingSpread(_spread: number): void {}

  setShowChunkBorders(_enabled: boolean): void {}

  onHeightMapUpdate(heightMap: THREE.Texture): void {
    this.heightMapUniform.value = heightMap;
  }

  dispose(): void {
    this.material?.dispose();
    this.material = null;
  }
}
