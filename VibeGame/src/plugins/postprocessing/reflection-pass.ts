import * as THREE from 'three';
import { Pass } from 'postprocessing';

/**
 * Screen-space reflections for the surfaces that actually deserve them.
 *
 * ## Why not `three/addons` SSRPass
 *
 * The stock `SSRPass` re-renders the whole scene three times (beauty, normals,
 * metalness) and then ray-marches at full resolution — on a dressed circuit
 * that is the most expensive thing in the frame by a wide margin, and its
 * output is a hard mirror with no roughness response, so wet tarmac and a
 * polished showroom floor look identical.
 *
 * This pass takes the opposite trade:
 *
 * - **One tiny geometry pass.** Only the meshes flagged as reflective render,
 *   through a layer mask, into a half-resolution normal+roughness buffer. A
 *   circuit has a handful of them (road, water, glass), so this is a few
 *   thousand triangles instead of a million.
 * - **March against the scene depth the composer already has.** Everything on
 *   screen occludes the ray, including geometry that is not itself reflective.
 * - **Roughness drives the result.** The ray is cone-jittered by the surface's
 *   own roughness and the hit is blurred to match, so asphalt gets a soft
 *   grazing sheen and wet asphalt gets something close to a mirror — from the
 *   same code path, with no per-scene tuning.
 * - **Reflection is additive over a Fresnel weight**, never a replacement, so
 *   a failed ray degrades to "no reflection" rather than to a black hole.
 *
 * ## Coordinate space
 *
 * The march runs in **view space**: depth is linearised from the composer's
 * depth texture, the ray steps in metres, and each step is projected back to
 * UV. This keeps step lengths physically meaningful (thickness is metres, not
 * depth units) and makes the thickness test independent of the projection.
 */

/** Layer the reflective meshes are rendered on for the normal pass. */
const REFLECTION_LAYER = 11;

/** Scratch for reading back the renderer's clear colour. */
const _clearColor = new THREE.Color();

export interface ReflectionPassOptions {
  renderer: THREE.WebGLRenderer;
  scene: THREE.Scene;
  camera: THREE.Camera;
  /** Meshes that receive reflections. Mutated in place by the caller. */
  selects: THREE.Mesh[];
  /** Reflection buffer scale (0.25–1). Lower = cheaper and softer. */
  resolutionScale?: number;
}

interface NormalUniforms {
  [key: string]: THREE.IUniform;
  uRoughness: THREE.IUniform<number>;
}

/**
 * Override material for the reflective-mesh pass: view-space normal in RGB,
 * the mesh's own roughness in A.
 *
 * `onBeforeRender` runs per object, which is what lets one override material
 * carry a different roughness for each mesh — the road and the lake do not
 * blur their reflections by the same amount.
 */
function createNormalMaterial(): THREE.ShaderMaterial {
  const material = new THREE.ShaderMaterial({
    uniforms: { uRoughness: { value: 0.5 } } satisfies NormalUniforms,
    vertexShader: /* glsl */ `
      varying vec3 vViewNormal;
      void main() {
        // normalMatrix already takes the mesh into view space; instanced
        // meshes need the per-instance matrix folded in first.
        #ifdef USE_INSTANCING
          mat3 im = mat3(instanceMatrix);
          vViewNormal = normalize(normalMatrix * (im * normal));
          vec4 mv = modelViewMatrix * instanceMatrix * vec4(position, 1.0);
        #else
          vViewNormal = normalize(normalMatrix * normal);
          vec4 mv = modelViewMatrix * vec4(position, 1.0);
        #endif
        gl_Position = projectionMatrix * mv;
      }
    `,
    fragmentShader: /* glsl */ `
      uniform float uRoughness;
      varying vec3 vViewNormal;
      void main() {
        vec3 n = normalize(vViewNormal);
        gl_FragColor = vec4(n * 0.5 + 0.5, uRoughness);
      }
    `,
  });
  material.onBeforeRender = (_renderer, _scene, _camera, _geometry, object) => {
    const mesh = object as THREE.Mesh;
    const source = Array.isArray(mesh.material)
      ? mesh.material[0]
      : mesh.material;
    const std = source as THREE.MeshStandardMaterial | undefined;
    const uniforms = material.uniforms as NormalUniforms;
    uniforms.uRoughness.value =
      std && typeof std.roughness === 'number' ? std.roughness : 0.5;
  };
  return material;
}

const REFLECTION_FRAGMENT = /* glsl */ `
  precision highp float;

  uniform sampler2D tColor;
  uniform sampler2D tDepth;
  uniform sampler2D tNormal;
  uniform mat4 uProjection;
  uniform mat4 uInverseProjection;
  uniform vec2 uResolution;
  uniform float uNear;
  uniform float uFar;
  uniform float uIntensity;
  uniform float uMaxDistance;
  uniform float uThickness;
  uniform float uFresnelPower;
  uniform float uJitter;

  varying vec2 vUv;

  const int MARCH_STEPS = 28;
  const int REFINE_STEPS = 5;

  float readDepth(vec2 uv) {
    return texture2D(tDepth, uv).x;
  }

  /** Depth buffer value -> positive distance in front of the camera (metres). */
  float linearDepth(float depth) {
    float z = depth * 2.0 - 1.0;
    return (2.0 * uNear * uFar) / (uFar + uNear - z * (uFar - uNear));
  }

  /** Rebuild the view-space position of a pixel from its depth. */
  vec3 viewPositionAt(vec2 uv, float depth) {
    vec4 clip = vec4(uv * 2.0 - 1.0, depth * 2.0 - 1.0, 1.0);
    vec4 view = uInverseProjection * clip;
    return view.xyz / view.w;
  }

  vec2 projectToUv(vec3 viewPos) {
    vec4 clip = uProjection * vec4(viewPos, 1.0);
    return (clip.xy / clip.w) * 0.5 + 0.5;
  }

  /** Cheap per-pixel hash, used to decorrelate the cone jitter. */
  float hash(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
  }

  void main() {
    vec4 normalSample = texture2D(tNormal, vUv);
    // Alpha 0 = nothing reflective rendered here. Bail before any texture
    // fetch that would cost us on the 95% of the screen that is not road.
    if (normalSample.a <= 0.0 && length(normalSample.rgb) < 0.001) {
      gl_FragColor = vec4(0.0);
      return;
    }

    float depth = readDepth(vUv);
    if (depth >= 1.0) {
      gl_FragColor = vec4(0.0);
      return;
    }

    vec3 normal = normalize(normalSample.rgb * 2.0 - 1.0);
    float roughness = normalSample.a;
    vec3 origin = viewPositionAt(vUv, depth);
    vec3 viewDir = normalize(origin);
    vec3 rayDir = normalize(reflect(viewDir, normal));

    // A rough surface scatters the reflected lobe: jitter the ray inside a
    // cone whose width follows roughness, and let the blur in the composite
    // average the neighbours back together.
    float jitterAmount = roughness * roughness * uJitter;
    if (jitterAmount > 0.0) {
      vec3 tangent = normalize(cross(normal, vec3(0.0, 0.0, 1.0) + 0.001));
      vec3 bitangent = cross(normal, tangent);
      float a = hash(vUv * uResolution) * 6.2831853;
      float r = hash(vUv * uResolution + 17.0) * jitterAmount;
      rayDir = normalize(rayDir + (tangent * cos(a) + bitangent * sin(a)) * r);
    }

    // Rays pointing back into the surface never hit anything on screen.
    if (dot(rayDir, normal) <= 0.0) {
      gl_FragColor = vec4(0.0);
      return;
    }

    float stepSize = uMaxDistance / float(MARCH_STEPS);
    // Offset the first sample off the surface, or every ray self-intersects
    // the pixel it started from.
    vec3 position = origin + rayDir * stepSize * (0.5 + hash(vUv * 91.7) * 0.5);

    bool hit = false;
    vec3 hitPosition = position;
    vec2 hitUv = vUv;

    for (int i = 0; i < MARCH_STEPS; i++) {
      position += rayDir * stepSize;
      vec2 uv = projectToUv(position);
      if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) break;

      float sceneDepth = linearDepth(readDepth(uv));
      // View space looks down -Z, so the ray's distance is -position.z.
      float rayDepth = -position.z;
      float delta = rayDepth - sceneDepth;

      if (delta > 0.0 && delta < uThickness + stepSize) {
        hit = true;
        hitPosition = position;
        hitUv = uv;
        break;
      }
    }

    if (!hit) {
      gl_FragColor = vec4(0.0);
      return;
    }

    // Binary refine: halve back and forth around the crossing so the hit lands
    // on the surface instead of up to one whole step past it.
    vec3 lo = hitPosition - rayDir * stepSize;
    vec3 hi = hitPosition;
    for (int i = 0; i < REFINE_STEPS; i++) {
      vec3 mid = (lo + hi) * 0.5;
      vec2 uv = projectToUv(mid);
      float sceneDepth = linearDepth(readDepth(uv));
      if (-mid.z > sceneDepth) hi = mid; else lo = mid;
    }
    hitUv = projectToUv(hi);

    vec3 reflected = texture2D(tColor, hitUv).rgb;

    // Fade wherever the reflection stops being trustworthy:
    //  - screen edges, because the ray left the only data we have;
    //  - long rays, whose hit is increasingly likely to be wrong geometry;
    //  - rays pointing back at the camera, which mirror the near plane.
    vec2 edge = smoothstep(vec2(0.0), vec2(0.12), hitUv) *
                (1.0 - smoothstep(vec2(0.88), vec2(1.0), hitUv));
    float edgeFade = edge.x * edge.y;
    float distanceFade = 1.0 - clamp(length(hi - origin) / uMaxDistance, 0.0, 1.0);
    float backFade = clamp(-rayDir.z * 2.0 + 1.0, 0.0, 1.0);

    // Schlick: reflections belong at grazing angles, not head-on.
    float fresnel = pow(1.0 - max(dot(-viewDir, normal), 0.0), uFresnelPower);
    // A mirror reflects at any angle; a matte surface only at the very edge.
    fresnel = mix(1.0, fresnel, clamp(roughness * 1.4, 0.0, 1.0));

    float strength = uIntensity * fresnel * edgeFade * distanceFade * backFade;
    gl_FragColor = vec4(reflected, clamp(strength, 0.0, 1.0));
  }
`;

const COMPOSITE_FRAGMENT = /* glsl */ `
  precision highp float;

  uniform sampler2D tColor;
  uniform sampler2D tReflection;
  uniform sampler2D tNormal;
  uniform vec2 uReflectionTexel;

  varying vec2 vUv;

  void main() {
    vec4 base = texture2D(tColor, vUv);
    float roughness = texture2D(tNormal, vUv).a;

    // Blur radius follows roughness: a mirror keeps the marched detail, a
    // rough surface averages its jittered neighbours into a soft sheen.
    vec2 radius = uReflectionTexel * (1.0 + roughness * 6.0);
    vec4 reflection = texture2D(tReflection, vUv) * 0.4;
    reflection += texture2D(tReflection, vUv + vec2(radius.x, 0.0)) * 0.15;
    reflection += texture2D(tReflection, vUv - vec2(radius.x, 0.0)) * 0.15;
    reflection += texture2D(tReflection, vUv + vec2(0.0, radius.y)) * 0.15;
    reflection += texture2D(tReflection, vUv - vec2(0.0, radius.y)) * 0.15;

    gl_FragColor = vec4(mix(base.rgb, reflection.rgb, reflection.a), base.a);
  }
`;

const FULLSCREEN_VERTEX = /* glsl */ `
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = vec4(position.xy, 0.0, 1.0);
  }
`;

export class ReflectionPass extends Pass {
  private readonly targetScene: THREE.Scene;

  private readonly sceneCamera: THREE.Camera;

  private readonly selects: THREE.Mesh[];

  private readonly normalMaterial: THREE.ShaderMaterial;

  private readonly normalCamera: THREE.PerspectiveCamera;

  private readonly normalTarget: THREE.WebGLRenderTarget;

  private readonly reflectionTarget: THREE.WebGLRenderTarget;

  private readonly reflectionMaterial: THREE.ShaderMaterial;

  private readonly compositeMaterial: THREE.ShaderMaterial;

  private readonly fullscreenScene: THREE.Scene;

  private readonly fullscreenCamera: THREE.OrthographicCamera;

  private readonly fullscreenQuad: THREE.Mesh;

  private readonly layeredMeshes = new Set<THREE.Mesh>();

  private resolutionScale: number;

  /** True while the reflection buffer is known to be empty (see `copy`). */
  private reflectionCleared = false;

  private width = 1;

  private height = 1;

  constructor(options: ReflectionPassOptions) {
    super('ReflectionPass');
    this.needsSwap = true;
    this.needsDepthTexture = true;

    this.targetScene = options.scene;
    this.sceneCamera = options.camera;
    this.selects = options.selects;
    this.resolutionScale = Math.min(
      1,
      Math.max(0.25, options.resolutionScale ?? 0.5)
    );

    this.normalMaterial = createNormalMaterial();
    this.normalCamera = new THREE.PerspectiveCamera();
    this.normalCamera.layers.set(REFLECTION_LAYER);
    // Its matrices are copied wholesale from the game camera every frame; let
    // three recompute either of them and the normal pass drifts off-camera.
    this.normalCamera.matrixAutoUpdate = false;
    this.normalCamera.matrixWorldAutoUpdate = false;

    this.normalTarget = new THREE.WebGLRenderTarget(1, 1, {
      minFilter: THREE.LinearFilter,
      magFilter: THREE.LinearFilter,
      type: THREE.HalfFloatType,
      depthBuffer: true,
    });
    this.reflectionTarget = new THREE.WebGLRenderTarget(1, 1, {
      minFilter: THREE.LinearFilter,
      magFilter: THREE.LinearFilter,
      type: THREE.HalfFloatType,
      depthBuffer: false,
    });

    this.reflectionMaterial = new THREE.ShaderMaterial({
      vertexShader: FULLSCREEN_VERTEX,
      fragmentShader: REFLECTION_FRAGMENT,
      depthTest: false,
      depthWrite: false,
      uniforms: {
        tColor: { value: null },
        tDepth: { value: null },
        tNormal: { value: this.normalTarget.texture },
        uProjection: { value: new THREE.Matrix4() },
        uInverseProjection: { value: new THREE.Matrix4() },
        uResolution: { value: new THREE.Vector2(1, 1) },
        uNear: { value: 0.1 },
        uFar: { value: 1000 },
        uIntensity: { value: 0.6 },
        uMaxDistance: { value: 60 },
        uThickness: { value: 0.5 },
        // 2.0 rather than the textbook 5.0: a chase camera looks at the road
        // from ~10° above it, and a strict Schlick curve puts the reflection
        // just outside that band — physically defensible, visually absent.
        uFresnelPower: { value: 2 },
        // Cone jitter is what turns a mirror into a rough surface, but every
        // bit of it is noise the 4-tap composite blur has to average away.
        // 0.22 is where asphalt stops looking wet and before the road starts
        // to sparkle.
        uJitter: { value: 0.22 },
      },
    });

    this.compositeMaterial = new THREE.ShaderMaterial({
      vertexShader: FULLSCREEN_VERTEX,
      fragmentShader: COMPOSITE_FRAGMENT,
      depthTest: false,
      depthWrite: false,
      uniforms: {
        tColor: { value: null },
        tReflection: { value: this.reflectionTarget.texture },
        tNormal: { value: this.normalTarget.texture },
        uReflectionTexel: { value: new THREE.Vector2(1, 1) },
      },
    });

    this.fullscreenScene = new THREE.Scene();
    this.fullscreenCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
    this.fullscreenQuad = new THREE.Mesh(
      new THREE.PlaneGeometry(2, 2),
      this.reflectionMaterial
    );
    this.fullscreenQuad.frustumCulled = false;
    this.fullscreenScene.add(this.fullscreenQuad);
  }

  /** Live knobs, driven from the `Postprocessing` component each frame. */
  configure(settings: {
    intensity: number;
    maxDistance: number;
    thickness: number;
    fresnelPower?: number;
    jitter?: number;
  }): void {
    const u = this.reflectionMaterial.uniforms;
    u.uIntensity.value = settings.intensity;
    u.uMaxDistance.value = settings.maxDistance;
    u.uThickness.value = settings.thickness;
    if (settings.fresnelPower !== undefined) {
      u.uFresnelPower.value = settings.fresnelPower;
    }
    if (settings.jitter !== undefined) u.uJitter.value = settings.jitter;
  }

  /**
   * Move the reflective meshes onto the pass's private layer (and everything
   * that dropped out of the list back off it). Enabling a layer is additive in
   * three, so the mesh still renders normally in the beauty pass.
   */
  private syncLayers(): void {
    for (const mesh of this.layeredMeshes) {
      if (!this.selects.includes(mesh)) {
        mesh.layers.disable(REFLECTION_LAYER);
        this.layeredMeshes.delete(mesh);
      }
    }
    for (const mesh of this.selects) {
      if (!this.layeredMeshes.has(mesh)) {
        mesh.layers.enable(REFLECTION_LAYER);
        this.layeredMeshes.add(mesh);
      }
    }
  }

  override setSize(width: number, height: number): void {
    this.width = Math.max(1, width);
    this.height = Math.max(1, height);
    const w = Math.max(1, Math.round(this.width * this.resolutionScale));
    const h = Math.max(1, Math.round(this.height * this.resolutionScale));
    this.normalTarget.setSize(w, h);
    this.reflectionTarget.setSize(w, h);
    this.reflectionMaterial.uniforms.uResolution.value.set(w, h);
    this.compositeMaterial.uniforms.uReflectionTexel.value.set(1 / w, 1 / h);
  }

  override render(
    renderer: THREE.WebGLRenderer,
    inputBuffer: THREE.WebGLRenderTarget,
    outputBuffer: THREE.WebGLRenderTarget | null
  ): void {
    if (this.selects.length === 0) {
      // Nothing reflective on screen: forward the frame untouched rather than
      // paying for two passes that would composite a black buffer.
      this.copy(renderer, inputBuffer, outputBuffer);
      return;
    }

    this.syncLayers();

    const camera = this.sceneCamera as THREE.PerspectiveCamera;
    // Copy the *world* matrices rather than re-deriving them: the game camera
    // is usually a child of a rig (chase arm, orbit pivot), so its local
    // position is not where it is in the world, and `updateMatrixWorld()` on a
    // parentless clone would place the normal pass at the wrong point.
    this.normalCamera.projectionMatrix.copy(camera.projectionMatrix);
    this.normalCamera.projectionMatrixInverse.copy(
      camera.projectionMatrixInverse
    );
    this.normalCamera.matrixWorld.copy(camera.matrixWorld);
    this.normalCamera.matrixWorldInverse.copy(camera.matrixWorldInverse);
    this.normalCamera.near = camera.near;
    this.normalCamera.far = camera.far;
    this.normalCamera.layers.set(REFLECTION_LAYER);

    const previousTarget = renderer.getRenderTarget();
    const previousOverride = this.targetScene.overrideMaterial;
    const previousBackground = this.targetScene.background;
    const previousClearColor = renderer.getClearColor(_clearColor).getHex();
    const previousClearAlpha = renderer.getClearAlpha();

    // Alpha 0 everywhere = "not reflective"; the shader's early-out depends on
    // the clear, so it has to be explicit and not inherit the scene's sky.
    // The renderer's own clear colour is restored right after: it is the
    // engine's sky colour, and leaving it black would flash the whole frame.
    this.targetScene.background = null;
    this.targetScene.overrideMaterial = this.normalMaterial;
    renderer.setRenderTarget(this.normalTarget);
    renderer.setClearColor(0x000000, 0);
    renderer.clear(true, true, false);
    renderer.render(this.targetScene, this.normalCamera);
    this.targetScene.overrideMaterial = previousOverride;
    this.targetScene.background = previousBackground;
    renderer.setClearColor(previousClearColor, previousClearAlpha);

    const u = this.reflectionMaterial.uniforms;
    u.tColor.value = inputBuffer.texture;
    u.uProjection.value.copy(camera.projectionMatrix);
    u.uInverseProjection.value.copy(camera.projectionMatrixInverse);
    u.uNear.value = camera.near;
    u.uFar.value = camera.far;

    this.fullscreenQuad.material = this.reflectionMaterial;
    renderer.setRenderTarget(this.reflectionTarget);
    renderer.clear(true, false, false);
    renderer.render(this.fullscreenScene, this.fullscreenCamera);
    this.reflectionCleared = false;

    this.compositeMaterial.uniforms.tColor.value = inputBuffer.texture;
    this.fullscreenQuad.material = this.compositeMaterial;
    renderer.setRenderTarget(this.renderToScreen ? null : outputBuffer);
    renderer.render(this.fullscreenScene, this.fullscreenCamera);

    renderer.setRenderTarget(previousTarget);
  }

  /** Straight blit, used when there is nothing to reflect. */
  private copy(
    renderer: THREE.WebGLRenderer,
    inputBuffer: THREE.WebGLRenderTarget,
    outputBuffer: THREE.WebGLRenderTarget | null
  ): void {
    // The composite still samples the reflection buffer, so it has to be empty
    // — otherwise the last frame that *did* have a reflective mesh stays
    // smeared over the scene for as long as none is on screen.
    if (!this.reflectionCleared) {
      const target = renderer.getRenderTarget();
      renderer.setRenderTarget(this.reflectionTarget);
      renderer.clear(true, false, false);
      renderer.setRenderTarget(target);
      this.reflectionCleared = true;
    }
    this.compositeMaterial.uniforms.tColor.value = inputBuffer.texture;
    this.fullscreenQuad.material = this.compositeMaterial;
    const previousTarget = renderer.getRenderTarget();
    renderer.setRenderTarget(this.renderToScreen ? null : outputBuffer);
    renderer.render(this.fullscreenScene, this.fullscreenCamera);
    renderer.setRenderTarget(previousTarget);
  }

  override setDepthTexture(
    depthTexture: THREE.Texture,
    depthPacking?: number
  ): void {
    void depthPacking;
    this.reflectionMaterial.uniforms.tDepth.value = depthTexture;
  }

  override dispose(): void {
    for (const mesh of this.layeredMeshes)
      mesh.layers.disable(REFLECTION_LAYER);
    this.layeredMeshes.clear();
    this.normalTarget.dispose();
    this.reflectionTarget.dispose();
    this.normalMaterial.dispose();
    this.reflectionMaterial.dispose();
    this.compositeMaterial.dispose();
    this.fullscreenQuad.geometry.dispose();
    super.dispose();
  }
}
