import { Color, Matrix4, Uniform } from 'three';
import type { Camera, WebGLRenderer, WebGLRenderTarget } from 'three';
import { BlendFunction, Effect, EffectAttribute } from 'postprocessing';

/**
 * Screen-space volumetric height fog. Reconstructs each fragment's world
 * position from the depth buffer, then integrates an exponential
 * height-density function along the view ray (denser the lower you go) so the
 * world drowns in fog near the ground and clears with altitude. A cheap fbm
 * noise field scrolls over time to break the fog into volumetric wisps.
 *
 * Output is `vec4(fogColor, fogAmount)` blended with {@link BlendFunction.ALPHA}
 * (`mix(scene, fogColor, fogAmount)`), so it reads as true distance/height fog
 * rather than a flat color wash.
 */
const fragmentShader = /* glsl */ `
uniform mat4 uProj;
uniform mat4 uProjInv;
uniform mat4 uCamMatrixWorld;
uniform vec3 uFogColor;
uniform float uFogDensity;
uniform float uFogHeight;
uniform float uFogFalloff;
uniform float uFogNoise;

float hash(vec2 p) {
  return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
}

float vnoise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(hash(i + vec2(0.0, 0.0)), hash(i + vec2(1.0, 0.0)), u.x),
    mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x),
    u.y
  );
}

float fbm(vec2 p) {
  float v = 0.0;
  float a = 0.5;
  for (int i = 0; i < 3; i++) {
    v += a * vnoise(p);
    p *= 2.0;
    a *= 0.5;
  }
  return v;
}

// View-space position from non-linear depth (mirrors postprocessing's helper,
// but with our own projection uniforms since the effect template omits them).
vec3 viewPosFromDepth(const in vec2 uv, const in float depth) {
  float viewZ = getViewZ(depth);
  vec4 clip = vec4(vec3(uv, depth) * 2.0 - 1.0, 1.0);
  float clipW = uProj[2][3] * viewZ + uProj[3][3];
  clip *= clipW;
  return (uProjInv * clip).xyz;
}

void mainImage(const in vec4 inputColor, const in vec2 uv, const in float depth, out vec4 outputColor) {
  // Sky/far-plane pixels sit at "infinite" distance and would saturate to solid
  // fog, erasing the skybox. Leave them untouched — only world geometry fogs.
  if (depth >= 0.9999) { outputColor = vec4(uFogColor, 0.0); return; }

  vec3 viewPos = viewPosFromDepth(uv, depth);
  vec3 worldPos = (uCamMatrixWorld * vec4(viewPos, 1.0)).xyz;
  vec3 camPos = uCamMatrixWorld[3].xyz;

  vec3 ray = worldPos - camPos;
  float dist = length(ray);
  vec3 dir = dist > 1e-4 ? ray / dist : vec3(0.0, 0.0, -1.0);

  // Analytic integral of density(y) = D * exp(-k * (y - height)) along the ray.
  float k = max(uFogFalloff, 1e-4);
  float baseDensity = uFogDensity * exp(-k * (camPos.y - uFogHeight));
  float dy = dir.y * k;
  float integral = abs(dy) > 1e-4
    ? baseDensity * (1.0 - exp(-dy * dist)) / dy
    : baseDensity * dist;
  float fogAmount = 1.0 - exp(-max(integral, 0.0));

  // Wispy volumetric break-up sampled at the ray midpoint (range-clamped so
  // distant geometry doesn't smear the noise into aliasing).
  vec3 mid = camPos + dir * min(dist, 60.0) * 0.5;
  float n = fbm(mid.xz * 0.06 + vec2(time * 0.02, time * 0.013));
  fogAmount *= mix(1.0, n + 0.5, clamp(uFogNoise, 0.0, 1.0));

  outputColor = vec4(uFogColor, clamp(fogAmount, 0.0, 1.0));
}
`;

export interface HeightFogOptions {
  color?: number;
  density?: number;
  height?: number;
  falloff?: number;
  noise?: number;
}

export class HeightFogEffect extends Effect {
  private readonly cam: Camera;

  constructor(camera: Camera, options: HeightFogOptions = {}) {
    super('HeightFogEffect', fragmentShader, {
      attributes: EffectAttribute.DEPTH,
      blendFunction: BlendFunction.ALPHA,
      uniforms: new Map<string, Uniform>([
        ['uProj', new Uniform(new Matrix4())],
        ['uProjInv', new Uniform(new Matrix4())],
        ['uCamMatrixWorld', new Uniform(new Matrix4())],
        ['uFogColor', new Uniform(new Color(options.color ?? 0x10131a))],
        ['uFogDensity', new Uniform(options.density ?? 0.06)],
        ['uFogHeight', new Uniform(options.height ?? 2)],
        ['uFogFalloff', new Uniform(options.falloff ?? 0.15)],
        ['uFogNoise', new Uniform(options.noise ?? 0.5)],
      ]),
    });
    this.cam = camera;
  }

  // Pull the camera's projection + world matrices each frame so the world-pos
  // reconstruction tracks the moving third-person camera.
  override update(
    _renderer: WebGLRenderer,
    _inputBuffer: WebGLRenderTarget,
    _deltaTime?: number
  ): void {
    const u = this.uniforms;
    (u.get('uProj')!.value as Matrix4).copy(this.cam.projectionMatrix);
    (u.get('uProjInv')!.value as Matrix4).copy(
      this.cam.projectionMatrixInverse
    );
    (u.get('uCamMatrixWorld')!.value as Matrix4).copy(this.cam.matrixWorld);
  }
}
