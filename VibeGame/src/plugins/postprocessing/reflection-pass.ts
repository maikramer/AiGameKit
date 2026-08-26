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

/** Scratch for the "is anything reflective on screen?" test. */
const _frustum = new THREE.Frustum();
const _viewProjection = new THREE.Matrix4();
const _sphere = new THREE.Sphere();

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
  uniform float uNear;
  uniform float uFar;
  uniform float uIntensity;
  uniform float uMaxDistance;
  uniform float uThickness;
  uniform float uFresnelPower;
  uniform float uJitter;

  varying vec2 vUv;

  const int MARCH_STEPS = 32;
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

  /**
   * Interleaved gradient noise. Unlike a sin-hash it is *structured*: the
   * values form a fine repeating lattice, so neighbouring pixels take
   * neighbouring offsets and the composite blur averages them back into a
   * smooth gradient. A white-noise hash here is what turned the road into
   * salt-and-pepper — a 4-tap blur cannot integrate uncorrelated samples.
   */
  float dither(vec2 pixel) {
    return fract(52.9829189 * fract(dot(pixel, vec2(0.06711056, 0.00583715))));
  }

  void main() {
    vec4 normalSample = texture2D(tNormal, vUv);
    // Alpha 0 = nothing reflective rendered here. Bail before any texture
    // fetch that would cost us on the 95% of the screen that is not road.
    if (normalSample.a <= 0.0 && dot(normalSample.rgb, normalSample.rgb) < 1e-6) {
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

    float noise = dither(gl_FragCoord.xy);

    // A rough surface scatters the reflected lobe: jitter the ray inside a
    // cone whose width follows roughness, and let the blur in the composite
    // average the neighbours back together. Both the angle and the radius come
    // from the same structured dither, so the cone is sampled evenly across a
    // pixel neighbourhood instead of randomly per pixel.
    float jitterAmount = roughness * roughness * uJitter;
    if (jitterAmount > 0.0) {
      vec3 tangent = normalize(cross(normal, vec3(0.0, 0.0, 1.0) + 0.001));
      vec3 bitangent = cross(normal, tangent);
      float a = noise * 6.2831853;
      float r = sqrt(dither(gl_FragCoord.yx + 23.7)) * jitterAmount;
      rayDir = normalize(rayDir + (tangent * cos(a) + bitangent * sin(a)) * r);
    }

    // Rays pointing back into the surface never hit anything on screen.
    if (dot(rayDir, normal) <= 0.0) {
      gl_FragColor = vec4(0.0);
      return;
    }

    // Schlick, hoisted ahead of the march. Every term in it comes from the
    // surface and the view, none from the hit, and a ray whose reflection
    // could not survive its own Fresnel weight is not worth 37 depth fetches.
    // (Grazing angles, where reflections live, keep the full march.)
    float fresnel = pow(1.0 - max(dot(-viewDir, normal), 0.0), uFresnelPower);
    // A mirror reflects at any angle; a matte surface only at the very edge.
    fresnel = mix(1.0, fresnel, clamp(roughness * 1.4, 0.0, 1.0));
    // Rays pointing back at the camera mirror the near plane.
    float backFade = clamp(-rayDir.z * 2.0 + 1.0, 0.0, 1.0);
    // The hit-dependent fades below only ever scale this down, so anything
    // under a thousandth of a tone-mapped step here is already invisible.
    float preStrength = uIntensity * fresnel * backFade;
    if (preStrength < 1e-4) {
      gl_FragColor = vec4(0.0);
      return;
    }

    // Lift the ray off its own surface before marching. The bias grows with
    // distance because the depth buffer's own precision does: a fixed 1 cm
    // offset that clears the road under the car is inside the noise floor
    // 200 m out, and the ray starts self-intersecting.
    float originDepth = -origin.z;
    float bias = max(0.02, originDepth * 0.004);
    vec3 rayStart = origin + normal * bias;

    // The march needs uProjection * (rayStart + rayDir * d) at every sample,
    // and that expression is linear in d. Projecting the origin and the
    // direction once turns a mat4 product per step into a single mad — same
    // arithmetic, 37 times cheaper across the march and the refine.
    vec4 clipStart = uProjection * vec4(rayStart, 1.0);
    vec4 clipDir = uProjection * vec4(rayDir, 0.0);

    bool hit = false;
    float hitDistance = 0.0;
    float hitDelta = 0.0;
    float previousDistance = 0.0;
    float previousStep = 0.0;
    vec2 hitUv = vUv;

    // Quadratic distribution: dense near the surface (where the reflection is
    // sharp and the geometry it hits is small on screen), coarse far away
    // (where a hit is a smear anyway). A uniform 2 m stride skipped everything
    // close to the car and made the hit test a coin flip between neighbours.
    for (int i = 1; i <= MARCH_STEPS; i++) {
      float t = (float(i) + noise - 0.5) / float(MARCH_STEPS);
      float distance = uMaxDistance * t * t;
      float stepLength = distance - previousDistance;
      vec4 clip = clipStart + clipDir * distance;
      vec2 uv = (clip.xy / clip.w) * 0.5 + 0.5;
      if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) break;

      float sceneDepth = linearDepth(readDepth(uv));
      // View space looks down -Z, so the ray's distance is -position.z.
      float rayDepth = -(rayStart.z + rayDir.z * distance);
      float delta = rayDepth - sceneDepth;

      if (delta > 0.0) {
        // The ray went behind something. Close behind = a surface it actually
        // hit; far behind = it dived under a foreground occluder, and anything
        // found past that point is geometry the reflection cannot see. Both
        // end the march — continuing was the other half of the speckle, since
        // it let occluded rays keep hunting until they found a stray hit.
        hit = delta < uThickness + stepLength;
        hitDistance = distance;
        hitDelta = delta;
        previousStep = stepLength;
        hitUv = uv;
        break;
      }
      previousDistance = distance;
    }

    if (!hit) {
      gl_FragColor = vec4(0.0);
      return;
    }

    // Binary refine: halve back and forth around the crossing so the hit lands
    // on the surface instead of up to one whole step past it.
    float lo = hitDistance - previousStep;
    float hi = hitDistance;
    for (int i = 0; i < REFINE_STEPS; i++) {
      float mid = (lo + hi) * 0.5;
      vec4 midClip = clipStart + clipDir * mid;
      vec2 midUv = (midClip.xy / midClip.w) * 0.5 + 0.5;
      float sceneDepth = linearDepth(readDepth(midUv));
      if (-(rayStart.z + rayDir.z * mid) > sceneDepth) hi = mid; else lo = mid;
    }
    vec4 hitClip = clipStart + clipDir * hi;
    hitUv = (hitClip.xy / hitClip.w) * 0.5 + 0.5;

    vec3 reflected = texture2D(tColor, hitUv).rgb;

    // Fade wherever the reflection stops being trustworthy:
    //  - screen edges, because the ray left the only data we have;
    //  - long rays, whose hit is increasingly likely to be wrong geometry.
    // (Fresnel and the back-facing fade are already in preStrength.)
    vec2 edge = smoothstep(vec2(0.0), vec2(0.12), hitUv) *
                (1.0 - smoothstep(vec2(0.88), vec2(1.0), hitUv));
    float edgeFade = edge.x * edge.y;
    float distanceFade = 1.0 - clamp(hi / uMaxDistance, 0.0, 1.0);
    // A hit found several metres behind the surface it was tested against is a
    // guess, not a reflection — fade it out rather than committing to it. The
    // depth at the crossing sample is the honest measure here; the refined
    // interval has converged by construction and says nothing about confidence.
    float thicknessFade =
      1.0 - smoothstep(uThickness * 0.5, uThickness + previousStep, hitDelta);

    float strength = preStrength * edgeFade * distanceFade * thicknessFade;
    strength = clamp(strength, 0.0, 1.0);
    // PREMULTIPLIED. The composite blurs this buffer, and blurring colour and
    // coverage separately is wrong: a tap that missed contributes rgb 0 with
    // weight, so a 4-tap average over a half-hit neighbourhood dragged the
    // reflection toward black and painted the road with dark specks. With the
    // colour already weighted, a miss contributes nothing to either channel.
    gl_FragColor = vec4(reflected * strength, strength);
  }
`;

/**
 * Roughness-scaled blur of the marched reflection, run at the reflection
 * buffer's own resolution.
 *
 * The taps are spaced in reflection texels either way, so doing this here
 * instead of during the composite is the same filter over the same data — but
 * a quarter of the pixels, and it leaves the full-resolution pass with a single
 * fetch instead of thirteen.
 */
const BLUR_FRAGMENT = /* glsl */ `
  precision highp float;

  uniform sampler2D tReflection;
  uniform sampler2D tNormal;
  uniform vec2 uReflectionTexel;

  varying vec2 vUv;

  /** One tap of the disc. Written as a function because GLSL ES 1.00 has no
   *  array initialisers, and the pass has to compile on the same profile as
   *  the rest of the engine's effects. */
  vec4 tap(vec2 uv, vec2 offset, vec2 radius, float weight) {
    return texture2D(tReflection, uv + offset * radius) * weight;
  }

  void main() {
    vec4 mask = texture2D(tNormal, vUv);

    // Nothing reflective here, so nothing to filter. Writing the zero keeps the
    // buffer's coverage channel authoritative for the composite, which reads
    // only this texture.
    if (mask.a <= 0.0 && dot(mask.rgb, mask.rgb) < 1e-6) {
      gl_FragColor = vec4(0.0);
      return;
    }

    float roughness = mask.a;

    // Blur radius follows roughness: a mirror keeps the marched detail, a
    // rough surface averages its jittered neighbours into a soft sheen.
    vec2 radius = uReflectionTexel * (1.0 + roughness * 6.0);

    // Two hexagonal rings around the centre tap — 13 samples instead of 5, so
    // a half-resolution buffer reads as a surface finish rather than as a dot
    // screen. The old 4-tap cross at a 4-texel radius sampled four isolated
    // pixels and left every gap between them visible.
    const float W0 = 0.34;
    const float W1 = 0.075;
    const float W2 = 0.035;
    vec4 reflection = texture2D(tReflection, vUv) * W0;
    reflection += tap(vUv, vec2( 0.500,  0.000), radius, W1);
    reflection += tap(vUv, vec2( 0.250,  0.433), radius, W1);
    reflection += tap(vUv, vec2(-0.250,  0.433), radius, W1);
    reflection += tap(vUv, vec2(-0.500,  0.000), radius, W1);
    reflection += tap(vUv, vec2(-0.250, -0.433), radius, W1);
    reflection += tap(vUv, vec2( 0.250, -0.433), radius, W1);
    reflection += tap(vUv, vec2( 1.000,  0.000), radius, W2);
    reflection += tap(vUv, vec2( 0.500,  0.866), radius, W2);
    reflection += tap(vUv, vec2(-0.500,  0.866), radius, W2);
    reflection += tap(vUv, vec2(-1.000,  0.000), radius, W2);
    reflection += tap(vUv, vec2(-0.500, -0.866), radius, W2);
    reflection += tap(vUv, vec2( 0.500, -0.866), radius, W2);
    gl_FragColor = reflection / (W0 + 6.0 * W1 + 6.0 * W2);
  }
`;

/**
 * The composite, in two builds.
 *
 * `blendInPlace` is the fast one: it never reads the frame, it blends the
 * reflection onto it with the hardware blender and leaves every other pixel
 * physically untouched. `false` produces the read-modify-write build, needed
 * only when this pass is last in the chain and has to write the finished frame
 * to the screen — there is no frame buffer to blend into then.
 */
function compositeFragment(blendInPlace: boolean): string {
  return /* glsl */ `
  precision highp float;

  ${blendInPlace ? '' : 'uniform sampler2D tColor;'}
  uniform sampler2D tReflection;

  varying vec2 vUv;

  void main() {
    // One fetch. The blur already happened at reflection resolution, and the
    // premultiplied coverage in alpha is the only mask this pass needs — zero
    // coverage means "no reflection here", whatever the surface underneath is.
    vec4 reflection = texture2D(tReflection, vUv);

    if (reflection.a <= 0.0) {
      ${
        blendInPlace
          ? // discard alone is not a jump in GLSL ES 1.00; without the return
            // the shader would run on for a fragment it has already thrown away.
            'discard;\n      return;'
          : 'gl_FragColor = texture2D(tColor, vUv);\n      return;'
      }
    }

    // The buffer is premultiplied, so this is a plain source-over: a partial
    // reflection darkens the base by its own coverage and adds exactly the
    // light it carries. No mix(), which would have replaced the surface with
    // an unweighted colour wherever coverage was patchy.
    ${
      blendInPlace
        ? `// Blend factors ONE / ONE_MINUS_SRC_ALPHA do the same arithmetic in
    // the ROP, on the reflective pixels only.
    gl_FragColor = vec4(reflection.rgb, clamp(reflection.a, 0.0, 1.0));`
        : `vec4 base = texture2D(tColor, vUv);
    gl_FragColor = vec4(
      base.rgb * (1.0 - clamp(reflection.a, 0.0, 1.0)) + reflection.rgb,
      base.a
    );`
    }
  }
`;
}

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

  /** Blurred copy of `reflectionTarget`; the only thing the composite reads. */
  private readonly blurTarget: THREE.WebGLRenderTarget;

  private readonly reflectionMaterial: THREE.ShaderMaterial;

  private readonly blurMaterial: THREE.ShaderMaterial;

  private readonly compositeMaterial: THREE.ShaderMaterial;

  /** Composite that blends straight into the frame instead of copying it. */
  private readonly blendMaterial: THREE.ShaderMaterial;

  private readonly fullscreenScene: THREE.Scene;

  private readonly fullscreenCamera: THREE.OrthographicCamera;

  private readonly fullscreenQuad: THREE.Mesh;

  private readonly layeredMeshes = new Set<THREE.Mesh>();

  /** Scratch membership set for `syncLayers`, reused to avoid per-frame GC. */
  private readonly selectSet = new Set<THREE.Mesh>();

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
    this.blurTarget = new THREE.WebGLRenderTarget(1, 1, {
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

    this.blurMaterial = new THREE.ShaderMaterial({
      vertexShader: FULLSCREEN_VERTEX,
      fragmentShader: BLUR_FRAGMENT,
      depthTest: false,
      depthWrite: false,
      uniforms: {
        tReflection: { value: this.reflectionTarget.texture },
        tNormal: { value: this.normalTarget.texture },
        uReflectionTexel: { value: new THREE.Vector2(1, 1) },
      },
    });

    this.compositeMaterial = new THREE.ShaderMaterial({
      vertexShader: FULLSCREEN_VERTEX,
      fragmentShader: compositeFragment(false),
      depthTest: false,
      depthWrite: false,
      uniforms: {
        tColor: { value: null },
        tReflection: { value: this.blurTarget.texture },
      },
    });

    this.blendMaterial = new THREE.ShaderMaterial({
      vertexShader: FULLSCREEN_VERTEX,
      fragmentShader: compositeFragment(true),
      depthTest: false,
      depthWrite: false,
      transparent: true,
      blending: THREE.CustomBlending,
      // Source-over for a premultiplied source, in the blend unit.
      blendSrc: THREE.OneFactor,
      blendDst: THREE.OneMinusSrcAlphaFactor,
      // Alpha is left exactly as the frame had it: the composer's buffer alpha
      // belongs to whatever wrote it, and a reflection is not an opacity.
      blendSrcAlpha: THREE.ZeroFactor,
      blendDstAlpha: THREE.OneFactor,
      uniforms: {
        tReflection: { value: this.blurTarget.texture },
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
    // Membership through a Set, not `selects.includes`: the list is rebuilt
    // wholesale twice a second, so the linear scan ran once per already-layered
    // mesh per frame — quadratic in the number of reflective surfaces.
    this.selectSet.clear();
    for (const mesh of this.selects) this.selectSet.add(mesh);
    for (const mesh of this.layeredMeshes) {
      if (!this.selectSet.has(mesh)) {
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

  /**
   * True when at least one reflective mesh is inside the camera frustum.
   *
   * Three culls the same meshes a moment later in the normal pass, but by then
   * the frame has already paid for the render-list build, the buffer clear and
   * — far more expensive — the full-resolution composite. A handful of sphere
   * tests up front lets an indoor scene, or a circuit section with the road
   * off-screen, skip the pass entirely.
   */
  private anythingVisible(camera: THREE.Camera): boolean {
    _viewProjection.multiplyMatrices(
      camera.projectionMatrix,
      camera.matrixWorldInverse
    );
    _frustum.setFromProjectionMatrix(_viewProjection);
    for (const mesh of this.selects) {
      if (mesh.visible === false) continue;
      // An InstancedMesh's geometry sphere covers one instance at the origin,
      // not the spread of the instances; three keeps the real one on the mesh.
      const instanced = mesh as THREE.Mesh & {
        isInstancedMesh?: boolean;
        boundingSphere?: THREE.Sphere | null;
      };
      let source: THREE.Sphere | null | undefined;
      if (instanced.isInstancedMesh === true) {
        source = instanced.boundingSphere;
        // Not computed yet: assume visible rather than skip a real reflection.
        if (!source) return true;
      } else {
        const geometry = mesh.geometry;
        if (!geometry.boundingSphere) geometry.computeBoundingSphere();
        source = geometry.boundingSphere;
        if (!source) return true;
      }
      _sphere.copy(source).applyMatrix4(mesh.matrixWorld);
      if (_frustum.intersectsSphere(_sphere)) return true;
    }
    return false;
  }

  override setSize(width: number, height: number): void {
    this.width = Math.max(1, width);
    this.height = Math.max(1, height);
    const w = Math.max(1, Math.round(this.width * this.resolutionScale));
    const h = Math.max(1, Math.round(this.height * this.resolutionScale));
    this.normalTarget.setSize(w, h);
    this.reflectionTarget.setSize(w, h);
    this.blurTarget.setSize(w, h);
    this.blurMaterial.uniforms.uReflectionTexel.value.set(1 / w, 1 / h);
  }

  override render(
    renderer: THREE.WebGLRenderer,
    inputBuffer: THREE.WebGLRenderTarget,
    outputBuffer: THREE.WebGLRenderTarget | null
  ): void {
    const camera = this.sceneCamera as THREE.PerspectiveCamera;
    if (this.selects.length === 0 || !this.anythingVisible(camera)) {
      // Nothing reflective on screen: forward the frame untouched rather than
      // paying for two passes that would composite a black buffer.
      this.copy(renderer, inputBuffer, outputBuffer);
      return;
    }

    this.syncLayers();

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
    // Every clear in this pass is explicit. Left on, `autoClear` would wipe the
    // frame out of the input buffer right before the blend draws into it.
    const previousAutoClear = renderer.autoClear;
    renderer.autoClear = false;

    // Alpha 0 everywhere = "not reflective"; the shader's early-out depends on
    // the clear, so it has to be explicit and not inherit the scene's sky.
    // The renderer's own clear colour is restored right after: it is the
    // engine's sky colour, and leaving it black would flash the whole frame.
    this.targetScene.background = null;
    this.targetScene.overrideMaterial = this.normalMaterial;
    // The beauty pass walked this graph a few microseconds ago and every world
    // matrix in it is current. `renderer.render` would walk all of it again for
    // the sake of the handful of meshes on the reflection layer — on a dressed
    // circuit that traversal, not the draw calls, is what this pass costs the
    // CPU. Sorting is equally pointless: the buffer stores normals, so draw
    // order cannot change the result.
    const previousMatrixAutoUpdate = this.targetScene.matrixWorldAutoUpdate;
    const previousSortObjects = renderer.sortObjects;
    this.targetScene.matrixWorldAutoUpdate = false;
    renderer.sortObjects = false;
    renderer.setRenderTarget(this.normalTarget);
    renderer.setClearColor(0x000000, 0);
    renderer.clear(true, true, false);
    renderer.render(this.targetScene, this.normalCamera);
    renderer.sortObjects = previousSortObjects;
    this.targetScene.matrixWorldAutoUpdate = previousMatrixAutoUpdate;
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
    // No clear: the quad covers the target and every path through the march
    // shader writes gl_FragColor, so a clear would only be a second full write
    // of the same pixels.
    renderer.render(this.fullscreenScene, this.fullscreenCamera);

    // Filter at reflection resolution, once, instead of at screen resolution
    // for every reflective pixel.
    this.fullscreenQuad.material = this.blurMaterial;
    renderer.setRenderTarget(this.blurTarget);
    renderer.render(this.fullscreenScene, this.fullscreenCamera);
    this.reflectionCleared = false;

    if (this.renderToScreen) {
      // Last pass in the chain: nothing downstream will show the input buffer,
      // so the frame has to be read and written out whole.
      this.needsSwap = true;
      this.compositeMaterial.uniforms.tColor.value = inputBuffer.texture;
      this.fullscreenQuad.material = this.compositeMaterial;
      renderer.setRenderTarget(null);
    } else {
      // Blend the reflection *into* the frame that is already in the input
      // buffer and hand that same buffer on. The old path copied 1600×725
      // pixels into the output buffer so that the ~5% of them carrying a
      // reflection could be modified; now the untouched 95% are never written
      // at all, and the pass costs one blend over the reflective pixels.
      this.needsSwap = false;
      this.fullscreenQuad.material = this.blendMaterial;
      renderer.setRenderTarget(inputBuffer);
    }
    renderer.render(this.fullscreenScene, this.fullscreenCamera);

    renderer.autoClear = previousAutoClear;
    renderer.setRenderTarget(previousTarget);
  }

  /** Nothing to reflect: leave the frame exactly as it arrived. */
  private copy(
    renderer: THREE.WebGLRenderer,
    inputBuffer: THREE.WebGLRenderTarget,
    outputBuffer: THREE.WebGLRenderTarget | null
  ): void {
    if (!this.renderToScreen) {
      // Free: the frame is already in the input buffer, and telling the
      // composer not to swap hands it to the next pass untouched. No clear of
      // the reflection buffers is needed either — nothing samples them on this
      // path. An indoor scene with no reflective surface in view pays nothing.
      this.needsSwap = false;
      return;
    }

    // Rendering to screen, so the frame still has to be moved there. The blur
    // buffer is what the composite reads, and it must be empty first, or the
    // last frame that *did* have a reflective mesh stays smeared over the scene
    // for as long as none is on screen.
    this.needsSwap = true;
    if (!this.reflectionCleared) {
      const target = renderer.getRenderTarget();
      const previousClear = renderer.getClearColor(_clearColor).getHex();
      const previousAlpha = renderer.getClearAlpha();
      renderer.setClearColor(0x000000, 0);
      renderer.setRenderTarget(this.blurTarget);
      renderer.clear(true, false, false);
      renderer.setClearColor(previousClear, previousAlpha);
      renderer.setRenderTarget(target);
      this.reflectionCleared = true;
    }
    void outputBuffer;
    this.compositeMaterial.uniforms.tColor.value = inputBuffer.texture;
    this.fullscreenQuad.material = this.compositeMaterial;
    const previousTarget = renderer.getRenderTarget();
    const previousAutoClear = renderer.autoClear;
    renderer.autoClear = false;
    renderer.setRenderTarget(null);
    renderer.render(this.fullscreenScene, this.fullscreenCamera);
    renderer.autoClear = previousAutoClear;
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
    this.blurTarget.dispose();
    this.normalMaterial.dispose();
    this.reflectionMaterial.dispose();
    this.blurMaterial.dispose();
    this.compositeMaterial.dispose();
    this.blendMaterial.dispose();
    this.fullscreenQuad.geometry.dispose();
    super.dispose();
  }
}
