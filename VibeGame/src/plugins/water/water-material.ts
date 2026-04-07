import * as THREE from 'three';

export interface WaterMaterialOptions {
  waterLevel: number;
  opacity: number;
  tint: THREE.Color;
  waveSpeed: number;
  waveScale: number;
  wireframe: boolean;
  terrainWorldSize: number;
  terrainMaxHeight: number;
  reflectionTexture: THREE.Texture;
  heightmapTexture: THREE.Texture | null;
  // Underwater rendering parameters
  underwaterFogColor: THREE.Color;
  underwaterFogDensity: number;
}

export function createWaterMaterial(
  options: WaterMaterialOptions
): THREE.ShaderMaterial {
  const material = new THREE.ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uWaterLevel: { value: options.waterLevel },
      uOpacity: { value: options.opacity },
      uTint: { value: options.tint },
      uWaveSpeed: { value: options.waveSpeed },
      uWaveScale: { value: options.waveScale },
      uTerrainWorldSize: { value: options.terrainWorldSize },
      uTerrainMaxHeight: { value: options.terrainMaxHeight },
      uShallowColor: { value: new THREE.Color(0x2ec4b6) },
      uDeepColor: { value: new THREE.Color(0x0a1628) },
      uFoamColor: { value: new THREE.Color(0xffffff) },
      uFresnelPower: { value: 3.0 },
      uMaxDepth: { value: 15.0 },
      uFoamThreshold: { value: 1.5 },
      uFoamFeather: { value: 0.8 },
      // Underwater uniforms
      uUnderwaterFade: { value: 0.0 },
      uUnderwaterFogColor: { value: options.underwaterFogColor },
      uUnderwaterFogDensity: { value: options.underwaterFogDensity },
      tReflection: { value: options.reflectionTexture },
      tHeightMap: { value: options.heightmapTexture },
      uHasHeightmap: { value: options.heightmapTexture ? 1.0 : 0.0 },
      uCameraPosition: { value: new THREE.Vector3() },
    },
    vertexShader: waterVertexShader,
    fragmentShader: waterFragmentShader,
    transparent: true,
    side: THREE.DoubleSide,
    depthWrite: false,
    wireframe: options.wireframe,
  });

  return material;
}

const waterVertexShader = /* glsl */ `
uniform float uTime;
uniform float uWaveSpeed;
uniform float uWaveScale;
uniform float uWaterLevel;

varying vec2 vWorldXZ;
varying vec3 vWorldPos;
varying vec3 vNormal;
varying float vWaveHeight;

void main() {
  vec4 worldPos = modelMatrix * vec4(position, 1.0);
  vec2 p = worldPos.xz;
  float t = uTime;

  float w1 = sin(p.x * 0.8 + t * uWaveSpeed) * 0.5;
  float w2 = sin(p.y * 0.6 + t * uWaveSpeed * 0.7) * 0.4;
  float w3 = sin((p.x + p.y) * 0.4 + t * uWaveSpeed * 1.3) * 0.3;
  float w4 = sin(p.x * 2.1 - p.y * 1.7 + t * uWaveSpeed * 0.5) * 0.15;
  float waveHeight = (w1 + w2 + w3 + w4) * uWaveScale;

  float dx = cos(p.x * 0.8 + t * uWaveSpeed) * 0.8 * 0.5
            + cos((p.x + p.y) * 0.4 + t * uWaveSpeed * 1.3) * 0.4 * 0.3
            + cos(p.x * 2.1 - p.y * 1.7 + t * uWaveSpeed * 0.5) * 2.1 * 0.15;
  float dz = cos(p.y * 0.6 + t * uWaveSpeed * 0.7) * 0.6 * 0.4
            + cos((p.x + p.y) * 0.4 + t * uWaveSpeed * 1.3) * 0.4 * 0.3
            - cos(p.x * 2.1 - p.y * 1.7 + t * uWaveSpeed * 0.5) * 1.7 * 0.15;

  vec3 waveNormal = normalize(vec3(-dx * uWaveScale, 1.0, -dz * uWaveScale));

  worldPos.y += waveHeight;

  vWaveHeight = waveHeight;
  vNormal = normalize((modelMatrix * vec4(waveNormal, 0.0)).xyz);
  vWorldPos = worldPos.xyz;
  vWorldXZ = worldPos.xz;

  gl_Position = projectionMatrix * viewMatrix * worldPos;
}
`;

const waterFragmentShader = /* glsl */ `
uniform float uTime;
uniform float uWaterLevel;
uniform float uOpacity;
uniform vec3 uTint;
uniform float uTerrainWorldSize;
uniform float uTerrainMaxHeight;
uniform vec3 uShallowColor;
uniform vec3 uDeepColor;
uniform vec3 uFoamColor;
uniform float uFresnelPower;
uniform float uMaxDepth;
uniform float uFoamThreshold;
uniform float uFoamFeather;
uniform vec3 uCameraPosition;
// Underwater rendering uniforms
uniform float uUnderwaterFade;
uniform vec3 uUnderwaterFogColor;
uniform float uUnderwaterFogDensity;
uniform float uHasHeightmap;

uniform sampler2D tReflection;
uniform sampler2D tHeightMap;

varying vec2 vWorldXZ;
varying vec3 vWorldPos;
varying vec3 vNormal;
varying float vWaveHeight;

float sampleTerrainHeight(vec2 worldXZ) {
  if (uHasHeightmap < 0.5) return 0.0;

  float halfSize = uTerrainWorldSize * 0.5;
  float u = (worldXZ.x + halfSize) / uTerrainWorldSize;
  float v = (worldXZ.y + halfSize) / uTerrainWorldSize;

  if (u < 0.0 || u > 1.0 || v < 0.0 || v > 1.0) return 0.0;

  vec2 uv = vec2(u, 1.0 - v);
  float h = texture2D(tHeightMap, uv).r;
  return h * uTerrainMaxHeight;
}

float computeDepth(vec2 worldXZ) {
  float terrainH = sampleTerrainHeight(worldXZ);
  return max(0.0, uWaterLevel - terrainH);
}

vec2 computeReflectionUV(vec3 viewDir, vec3 worldPos) {
  vec3 reflected = reflect(viewDir, vNormal);
  // Project reflected direction to screen-like UV for reflection sampling
  // Simple planar reflection UV based on world-space reflected direction
  float t = -(worldPos.y - uWaterLevel) / max(-reflected.y, 0.001);
  vec3 reflectedPos = worldPos + reflected * t;
  return reflectedPos.xz * 0.01 + 0.5;
}

float foamNoise(vec2 p) {
  // Deterministic, time-independent noise to avoid flicker
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}

void main() {
  float depth = computeDepth(vWorldXZ);
  float depthFactor = clamp(depth / uMaxDepth, 0.0, 1.0);

  vec3 viewDir = normalize(uCameraPosition - vWorldPos);

  // Fresnel
  float fresnel = pow(1.0 - max(dot(viewDir, vNormal), 0.0), uFresnelPower);
  fresnel = clamp(fresnel, 0.0, 1.0);

  // Depth-based color
  vec3 waterColor = mix(uShallowColor, uDeepColor, depthFactor);
  waterColor = mix(waterColor, uTint, 0.3);

  // Reflection
  vec2 reflUV = computeReflectionUV(viewDir, vWorldPos);
  reflUV = clamp(reflUV, 0.0, 1.0);
  // Add wave distortion to reflection UV
  reflUV += vNormal.xz * 0.02 * sin(uTime * 0.5);
  reflUV = clamp(reflUV, 0.0, 1.0);
  vec3 reflectionColor = texture2D(tReflection, reflUV).rgb;

  // Combine water color with reflection
  vec3 color = mix(waterColor, reflectionColor, fresnel * 0.6);

  // Foam at shallow edges
  if (uHasHeightmap > 0.5) {
    float foamMask = 1.0 - smoothstep(
      uFoamThreshold - uFoamFeather,
      uFoamThreshold,
      depth
    );
    float foamNoiseVal = foamNoise(vWorldXZ * 2.0);
    foamMask *= smoothstep(0.3, 0.7, foamNoiseVal);
    // Second foam layer for more natural look (time-independent)
    float foamNoiseVal2 = foamNoise(vWorldXZ * 5.0 + 17.3);
    foamMask *= smoothstep(0.2, 0.6, foamNoiseVal2);
    color = mix(color, uFoamColor, foamMask * 0.7);
  }

  // Subtle edge darkening
  float edgeFade = smoothstep(0.0, 2.0, depth);
  color *= mix(0.7, 1.0, edgeFade);

  // Wave crest highlights
  float crestHighlight = smoothstep(0.15, 0.3, vWaveHeight) * 0.15;
  color += vec3(crestHighlight);

  float alpha = uOpacity;
  alpha *= smoothstep(0.0, 0.5, depthFactor + 0.3);

  // Underwater fog (applied when camera is underwater)
  if (uUnderwaterFade > 0.0) {
    float underwaterDepth = max(0.0, uWaterLevel - vWorldPos.y);
    float fogFactor = 1.0 - exp(-uUnderwaterFogDensity * underwaterDepth * 0.5);
    fogFactor = clamp(fogFactor, 0.0, 1.0) * uUnderwaterFade;
    color = mix(color, uUnderwaterFogColor, fogFactor);
    alpha = mix(alpha, 1.0, fogFactor * 0.5);
  }

  gl_FragColor = vec4(color, alpha);
}
`;
