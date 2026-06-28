import {
  Color,
  Matrix4,
  OrthographicCamera,
  PerspectiveCamera,
  ShaderMaterial,
} from 'three';
import type { Camera, IUniform, WebGLRenderer, WebGLRenderTarget } from 'three';
import {
  FullScreenQuad,
  Pass,
} from 'three/examples/jsm/postprocessing/Pass.js';

/**
 * Screen-space volumetric height fog. Reconstructs each fragment's world
 * position from the depth buffer, then integrates an exponential
 * height-density function along the view ray (denser the lower you go) so the
 * world drowns in fog near the ground and clears with altitude. A cheap fbm
 * noise field scrolls over time to break the fog into volumetric wisps.
 *
 * Implemented as a three.js r185 `Pass` (drop-in for `EffectComposer.addPass`).
 * Reads the scene color from `tDiffuse` and reconstructs view/world position
 * from the composer-provided `tDepth` (`needsDepthTexture = true`), writing the
 * fogged result to `gl_FragColor` = `mix(sceneColor, fogColor, fogAmount)`.
 */
const vertexShader = /* glsl */ `
varying vec2 vUv;

void main() {
  vUv = uv;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

const fragmentShader = /* glsl */ `
#include <packing>

uniform sampler2D tDiffuse;
uniform sampler2D tDepth;
uniform float cameraNear;
uniform float cameraFar;
uniform float uTime;

uniform mat4 uProj;
uniform mat4 uProjInv;
uniform mat4 uCamMatrixWorld;
uniform vec3 uFogColor;
uniform float uFogDensity;
uniform float uFogHeight;
uniform float uFogFalloff;
uniform float uFogNoise;

varying vec2 vUv;

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

// View-space position from non-linear depth. Uses the three.js packing chunk
// (perspectiveDepthToViewZ / orthographicDepthToViewZ) selected at material
// build time via the PERSPECTIVE_CAMERA define.
vec3 viewPosFromDepth(const in vec2 uv, const in float depth) {
  #if defined( PERSPECTIVE_CAMERA )
    float viewZ = perspectiveDepthToViewZ(depth, cameraNear, cameraFar);
  #else
    float viewZ = orthographicDepthToViewZ(depth, cameraNear, cameraFar);
  #endif
  vec4 clip = vec4(vec3(uv, depth) * 2.0 - 1.0, 1.0);
  float clipW = uProj[2][3] * viewZ + uProj[3][3];
  clip *= clipW;
  return (uProjInv * clip).xyz;
}

void main() {
  vec2 uv = vUv;
  float depth = texture2D(tDepth, uv).r;
  vec3 sceneColor = texture2D(tDiffuse, uv).rgb;

  // Sky/far-plane pixels sit at "infinite" distance and would saturate to solid
  // fog, erasing the skybox. Leave them untouched — only world geometry fogs.
  if (depth >= 0.9999) {
    gl_FragColor = vec4(sceneColor, 1.0);
    return;
  }

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
  float n = fbm(mid.xz * 0.06 + vec2(uTime * 0.02, uTime * 0.013));
  fogAmount *= mix(1.0, n + 0.5, clamp(uFogNoise, 0.0, 1.0));

  float a = clamp(fogAmount, 0.0, 1.0);
  gl_FragColor = vec4(mix(sceneColor, uFogColor, a), 1.0);
}
`;

export interface HeightFogOptions {
  color?: number;
  density?: number;
  height?: number;
  falloff?: number;
  noise?: number;
}

export class HeightFogPass extends Pass {
  // Tells EffectComposer to attach a depth texture to its render targets so
  // `readBuffer.depthTexture` is populated for this pass's `tDepth` sampler.
  public needsDepthTexture = true;

  private readonly cam: Camera;
  private readonly material: ShaderMaterial;
  private readonly uniforms: Record<string, IUniform>;
  private readonly fsQuad: FullScreenQuad;
  private time = 0;

  constructor(camera: Camera, options: HeightFogOptions = {}) {
    super();
    this.cam = camera;

    this.uniforms = {
      tDiffuse: { value: null },
      tDepth: { value: null },
      uProj: { value: new Matrix4() },
      uProjInv: { value: new Matrix4() },
      uCamMatrixWorld: { value: new Matrix4() },
      uFogColor: { value: new Color(options.color ?? 0x10131a) },
      uFogDensity: { value: options.density ?? 0.06 },
      uFogHeight: { value: options.height ?? 2 },
      uFogFalloff: { value: options.falloff ?? 0.15 },
      uFogNoise: { value: options.noise ?? 0.5 },
      uTime: { value: 0 },
      cameraNear: { value: 0.1 },
      cameraFar: { value: 1000 },
    };

    const isPerspective =
      (camera as PerspectiveCamera).isPerspectiveCamera === true;
    this.material = new ShaderMaterial({
      uniforms: this.uniforms,
      vertexShader,
      fragmentShader,
      defines: isPerspective ? { PERSPECTIVE_CAMERA: '' } : {},
      depthWrite: false,
      depthTest: false,
    });

    this.fsQuad = new FullScreenQuad(this.material);
  }

  // Pull the camera's projection + world matrices each frame so the world-pos
  // reconstruction tracks the moving camera, and advance the noise time.
  override render(
    renderer: WebGLRenderer,
    writeBuffer: WebGLRenderTarget,
    readBuffer: WebGLRenderTarget,
    deltaTime?: number
  ): void {
    const u = this.uniforms;
    u.uProj.value.copy(this.cam.projectionMatrix);
    u.uProjInv.value.copy(this.cam.projectionMatrixInverse);
    u.uCamMatrixWorld.value.copy(this.cam.matrixWorld);
    u.tDiffuse.value = readBuffer.texture;
    u.tDepth.value = readBuffer.depthTexture;

    if (deltaTime !== undefined) {
      this.time += deltaTime;
      u.uTime.value = this.time;
    }

    const persp = this.cam as PerspectiveCamera;
    const ortho = this.cam as OrthographicCamera;
    if (persp.isPerspectiveCamera) {
      u.cameraNear.value = persp.near;
      u.cameraFar.value = persp.far;
    } else if (ortho.isOrthographicCamera) {
      u.cameraNear.value = ortho.near;
      u.cameraFar.value = ortho.far;
    }

    renderer.setRenderTarget(this.renderToScreen ? null : writeBuffer);
    this.fsQuad.render(renderer);
  }

  override dispose(): void {
    this.material.dispose();
    this.fsQuad.dispose();
  }
}
