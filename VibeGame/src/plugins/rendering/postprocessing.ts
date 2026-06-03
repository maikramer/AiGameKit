import * as THREE from 'three/webgpu';
import {
  pass,
  mrt,
  output,
  normalView,
  metalness,
  roughness,
  vec2,
  vec3,
  float,
  int,
  velocity,
} from 'three/tsl';
/* eslint-disable import/no-unresolved */
import { bloom } from 'three/addons/tsl/display/BloomNode.js';
import { fxaa } from 'three/addons/tsl/display/FXAANode.js';
import { smaa } from 'three/addons/tsl/display/SMAANode.js';
import { traa } from 'three/addons/tsl/display/TRAANode.js';
import { ao } from 'three/addons/tsl/display/GTAONode.js';
import { ssr } from 'three/addons/tsl/display/SSRNode.js';
import { ssgi } from 'three/addons/tsl/display/SSGINode.js';
import { sss } from 'three/addons/tsl/display/SSSNode.js';
import { godrays } from 'three/addons/tsl/display/GodraysNode.js';
import { dof } from 'three/addons/tsl/display/DepthOfFieldNode.js';
import { chromaticAberration } from 'three/addons/tsl/display/ChromaticAberrationNode.js';
import { anamorphic } from 'three/addons/tsl/display/AnamorphicNode.js';
import { vignette as tslVignette } from 'three/addons/tsl/display/CRT.js';
import { boxBlur } from 'three/addons/tsl/display/boxBlur.js';
/* eslint-enable import/no-unresolved */

export const AAMode = { OFF: 0, FXAA: 1, SMAA: 2, TRAA: 3 } as const;

export interface PostFxConfig {
  bloom: boolean;
  bloomStrength: number;
  bloomRadius: number;
  bloomThreshold: number;
  gtao: boolean;
  gtaoRadius: number;
  gtaoScale: number;
  ssgi: boolean;
  ssgiSliceCount: number;
  ssgiStepCount: number;
  ssr: boolean;
  ssrMaxDistance: number;
  ssrOpacity: number;
  ssrThickness: number;
  sss: boolean;
  sssDistance: number;
  sssQuality: number;
  dof: boolean;
  dofFocus: number;
  dofFocalLength: number;
  dofBokeh: number;
  godrays: boolean;
  godraysSteps: number;
  godraysIntensity: number;
  chromaticAberration: boolean;
  caStrength: number;
  anamorphic: boolean;
  anamorphicThreshold: number;
  anamorphicScale: number;
  vignette: boolean;
  vignetteStrength: number;
  vignetteRadius: number;
  aa: number;
}

type N = any; // eslint-disable-line @typescript-eslint/no-explicit-any
const n = (x: unknown): N => x as N;

export function buildPostProcessing(
  renderer: THREE.WebGPURenderer,
  scene: THREE.Scene,
  camera: THREE.Camera,
  cfg: PostFxConfig
): THREE.RenderPipeline {
  const post = new THREE.RenderPipeline(renderer);

  const isWebGPU = Boolean(
    (renderer.backend as unknown as { isWebGPUBackend?: boolean })
      ?.isWebGPUBackend
  );

  const useGtao = cfg.gtao && isWebGPU;
  const useSsr = cfg.ssr && isWebGPU;
  const useSsgi = cfg.ssgi && isWebGPU;
  const useSss = cfg.sss && isWebGPU;
  const useDof = cfg.dof && isWebGPU;
  const useGodrays = cfg.godrays && isWebGPU;
  const useTraa = cfg.aa === AAMode.TRAA && isWebGPU;

  const needsMrt = useGtao || useSsr || useSsgi || useSss;

  if (!isWebGPU && (cfg.gtao || cfg.ssr || cfg.ssgi || cfg.sss || cfg.dof || cfg.godrays)) {
    console.info(
      '[VibeGame] GTAO/SSR/SSGI/SSS/DoF/Godrays need the WebGPU backend — skipped on the WebGL2 fallback.'
    );
  }

  const scenePass = pass(scene, camera);
  if (needsMrt) {
    scenePass.setMRT(
      mrt({
        output,
        normal: normalView,
        metalrough: vec2(metalness, roughness),
      })
    );
  }

  const color = scenePass.getTextureNode('output');
  const depth = scenePass.getTextureNode('depth');
  const viewZ = scenePass.getViewZNode();

  let node: N = color;

  if (useGtao) {
    const normal = scenePass.getTextureNode('normal');
    const aoPass = n(ao(depth, normal, camera));
    aoPass.radius.value = cfg.gtaoRadius;
    aoPass.scale.value = cfg.gtaoScale;
    node = node.mul(aoPass.getTextureNode().r);
  }

  if (useSsgi) {
    const normal = scenePass.getTextureNode('normal');
    const ssgiPass = n(ssgi(color, depth, normal, camera as THREE.PerspectiveCamera));
    n(ssgiPass).sliceCount.value = cfg.ssgiSliceCount;
    n(ssgiPass).stepCount.value = cfg.ssgiStepCount;
    node = node.add(ssgiPass.getTextureNode());
  }

  if (useSsr) {
    const normal = scenePass.getTextureNode('normal');
    const metalRough = n(scenePass.getTextureNode('metalrough'));
    const ssrPass = n(
      ssr(color, depth, normal, metalRough.r, metalRough.g, camera)
    );
    ssrPass.maxDistance.value = cfg.ssrMaxDistance;
    ssrPass.opacity.value = cfg.ssrOpacity;
    ssrPass.thickness.value = cfg.ssrThickness;
    node = node.add(ssrPass.getTextureNode());
  }

  if (useSss) {
    const mainLight = scene.children.find(
      (c): c is THREE.DirectionalLight =>
        c instanceof THREE.DirectionalLight && c.castShadow
    );
    if (mainLight) {
      const sssPass = n(sss(depth, camera, mainLight));
      sssPass.distance.value = cfg.sssDistance;
      sssPass.quality.value = cfg.sssQuality;
      const sssBlurred = n(boxBlur(sssPass.getTextureNode().r, { size: int(2), separation: int(1) }));
      node = node.mul(n(sssBlurred).add(0.5));
    }
  }

  if (useGodrays) {
    const godrayLight = scene.children.find(
      (c): c is THREE.DirectionalLight =>
        c instanceof THREE.DirectionalLight && c.castShadow
    ) || scene.children.find(
      (c): c is THREE.PointLight => c instanceof THREE.PointLight
    );
    if (godrayLight) {
      const godraysPass = n(godrays(depth, camera, godrayLight));
      godraysPass.steps.value = cfg.godraysSteps;
      godraysPass.intensity.value = cfg.godraysIntensity;
      node = node.add(godraysPass.getTextureNode());
    }
  }

  if (cfg.bloom) {
    node = node.add(
      n(bloom(node, cfg.bloomStrength, cfg.bloomRadius, cfg.bloomThreshold))
    );
  }

  if (cfg.anamorphic) {
    const anamorphicPass = n(anamorphic(color, float(cfg.anamorphicThreshold), float(cfg.anamorphicScale)));
    node = node.add(anamorphicPass.getTextureNode());
  }

  if (useDof) {
    node = n(dof(node, viewZ, cfg.dofFocus, cfg.dofFocalLength, cfg.dofBokeh));
  }

  if (cfg.chromaticAberration) {
    node = n(chromaticAberration)(node, cfg.caStrength, vec2(0.5, 0.5), 1.1);
  }

  if (cfg.vignette) {
    const strength = float(cfg.vignetteStrength);
    const radius = float(1.0 - cfg.vignetteRadius);
    node = n(tslVignette)(n(vec3(node)), strength, radius);
  }

  post.outputNode = node;

  if (useTraa) {
    const vel = scenePass.getTextureNode('velocity') ?? velocity;
    post.outputNode = n(traa(post.outputNode, depth, vel, camera));
  } else if (cfg.aa === AAMode.SMAA) {
    post.outputNode = n(smaa(post.outputNode));
  } else if (cfg.aa === AAMode.FXAA) {
    post.outputNode = n(fxaa(post.outputNode));
  }

  return post;
}
