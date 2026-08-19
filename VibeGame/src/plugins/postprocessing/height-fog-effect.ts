import {
  Color,
  Matrix4,
  Uniform,
  Vector3,
  type Camera,
  type Scene,
} from 'three';
import { BlendFunction, Effect, EffectAttribute } from 'postprocessing';
import { findFirstDirectionalLight } from './sun-source';

/**
 * Analytic exponential height fog as a fullscreen post effect — the depth-based
 * aerial perspective a raytracer gets for free. Geometry fogs by optical depth
 * (density decays above `fogHeight`, world-space FBM noise breaks up the wall
 * of haze), sky pixels get a horizon haze that thickens toward (and glows
 * around) the sun, and looking toward the sun adds a forward-scattering lobe
 * so backlit fog reads bright instead of milky.
 *
 * Runs pre-tonemap (order < bloom) so the fog the camera sees is graded and
 * bloomed exactly like the scene behind it.
 */
const fragmentShader = /* glsl */ `
uniform vec3 fogColor;
uniform float fogDensity;
uniform float fogHeight;
uniform float fogFalloff;
uniform float fogNoise;
uniform vec3 sunDirection;
uniform vec3 sunColor;
uniform float sunInfluence;
uniform float skyHaze;
uniform mat4 inverseProjection;
uniform mat4 cameraWorld;
uniform float elapsedTime;

float fogHash(vec2 p) {
  return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
}

float fogValueNoise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  float a = fogHash(i);
  float b = fogHash(i + vec2(1.0, 0.0));
  float c = fogHash(i + vec2(0.0, 1.0));
  float d = fogHash(i + vec2(1.0, 1.0));
  return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}

float fogFbm(vec2 p) {
  float v = 0.0;
  float a = 0.5;
  for (int i = 0; i < 3; ++i) {
    v += a * fogValueNoise(p);
    p = p * 2.03 + vec2(11.7, 9.2);
    a *= 0.5;
  }
  return v;
}

void mainImage(
  const in vec4 inputColor,
  const in vec2 uv,
  const in float depth,
  out vec4 outputColor
) {
  // Reconstruct the world-space point behind this pixel from the depth buffer.
  // Camera position comes from OUR cameraWorld matrix: three's cameraPosition
  // uniform reflects the pass's fullscreen-quad camera (the origin), not the
  // scene camera.
  vec3 camPos = cameraWorld[3].xyz;
  vec4 clip = vec4(uv * 2.0 - 1.0, depth * 2.0 - 1.0, 1.0);
  vec4 view = inverseProjection * clip;
  vec3 viewPos = view.xyz / view.w;
  vec3 worldPos = (cameraWorld * vec4(viewPos, 1.0)).xyz;
  vec3 rayDir = worldPos - camPos;
  float dist = length(rayDir);
  rayDir /= max(dist, 1e-4);

  float cosSun = max(dot(rayDir, sunDirection), 0.0);
  vec3 inscatter = sunColor * pow(cosSun, 8.0) * sunInfluence;
  vec3 fogCol = fogColor + inscatter;

  bool isSky = depth >= 0.99995;
  if (isSky) {
    // Aerial perspective on the sky dome: haze that pools at the horizon and
    // brightens toward the sun, blending the sky into the fogged terrain.
    float haze = skyHaze * exp(-max(rayDir.y, 0.0) * 6.0);
    haze *= 0.4 + 0.6 * pow(cosSun, 2.0);
    outputColor = vec4(mix(inputColor.rgb, fogCol, clamp(haze, 0.0, 1.0)), inputColor.a);
    return;
  }

  // Exponential height fog: density decays above fogHeight; optical depth uses
  // the average ray height (close to the analytic integral for shallow rays).
  float avgY = 0.5 * (camPos.y + worldPos.y);
  float heightFactor = exp(-max(avgY - fogHeight, 0.0) / max(fogFalloff, 0.001));
  float density = fogDensity * heightFactor;
  density *= 1.0 - fogNoise * 0.4 * fogFbm(worldPos.xz * 0.02 + elapsedTime * 0.015);
  float fogAmount = 1.0 - exp(-dist * density);
  outputColor = vec4(mix(inputColor.rgb, fogCol, clamp(fogAmount, 0.0, 1.0)), inputColor.a);
}
`;

export interface HeightFogParams {
  color: number;
  density: number;
  height: number;
  falloff: number;
  noise: number;
  sunInfluence: number;
  skyHaze: number;
}

export class HeightFogEffect extends Effect {
  constructor() {
    super('HeightFogEffect', fragmentShader, {
      blendFunction: BlendFunction.NORMAL,
      attributes: EffectAttribute.DEPTH,
      uniforms: new Map<string, Uniform>([
        ['fogColor', new Uniform(new Color(0xbfd0dd))],
        ['fogDensity', new Uniform(0.004)],
        ['fogHeight', new Uniform(2.0)],
        ['fogFalloff', new Uniform(20.0)],
        ['fogNoise', new Uniform(0.4)],
        ['sunDirection', new Uniform(new Vector3(0, 1, 0))],
        ['sunColor', new Uniform(new Color(0xfff0dd))],
        ['sunInfluence', new Uniform(0.8)],
        ['skyHaze', new Uniform(0.5)],
        ['inverseProjection', new Uniform(new Matrix4())],
        ['cameraWorld', new Uniform(new Matrix4())],
        ['elapsedTime', new Uniform(0)],
      ]),
    });
  }

  /** Fog description from the Postprocessing component fields. */
  setFog(params: HeightFogParams): void {
    const u = this.uniforms;
    (u.get('fogColor')!.value as Color).setHex(params.color);
    (u.get('fogDensity')!.value as number) = params.density;
    (u.get('fogHeight')!.value as number) = params.height;
    (u.get('fogFalloff')!.value as number) = params.falloff;
    (u.get('fogNoise')!.value as number) = params.noise;
    (u.get('sunInfluence')!.value as number) = params.sunInfluence;
    (u.get('skyHaze')!.value as number) = params.skyHaze;
  }

  /** Per-frame camera + sun state (matrices are copied, not referenced). */
  syncCameraSun(camera: Camera, scene: Scene, elapsed: number): void {
    const u = this.uniforms;
    (u.get('inverseProjection')!.value as Matrix4).copy(
      camera.projectionMatrixInverse
    );
    (u.get('cameraWorld')!.value as Matrix4).copy(camera.matrixWorld);
    (u.get('elapsedTime')!.value as number) = elapsed;

    const light = findFirstDirectionalLight(scene);
    if (!light) return;
    const dir = u.get('sunDirection')!.value as Vector3;
    dir
      .copy(light.position)
      .sub(light.target.position)
      .normalize();
    (u.get('sunColor')!.value as Color).copy(light.color);
  }
}
