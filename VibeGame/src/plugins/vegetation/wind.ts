import * as THREE from 'three';
import type { State, System } from '../../core';
import { defineQuery } from '../../core';
import { getWindVector } from '../weather/state';
import { Vegetation } from './components';

/** URLs whose InstancedMesh2 pools should receive wind sway. */
const windUrlsByState = new WeakMap<State, Set<string>>();

interface WindUniforms {
  uVegWind: { value: THREE.Vector2 };
  uVegWindTime: { value: number };
  uVegWindAmp: { value: number };
}

const windUniformsByMat = new WeakMap<THREE.Material, WindUniforms>();
/** Live materials we drive each frame (WeakSet is not iterable). */
const windMatsByState = new WeakMap<State, Set<THREE.Material>>();

export function registerVegetationWindUrl(state: State, url: string): void {
  const u = url.trim();
  if (!u) return;
  let set = windUrlsByState.get(state);
  if (!set) {
    set = new Set();
    windUrlsByState.set(state, set);
  }
  set.add(u);
}

export function getVegetationWindUrls(state: State): ReadonlySet<string> {
  return windUrlsByState.get(state) ?? new Set();
}

/** Test helper: clear wind URL registry for a state. */
export function _resetVegetationWindUrls(state: State): void {
  windUrlsByState.delete(state);
  windMatsByState.delete(state);
}

const WIND_VERTEX = /* glsl */ `
uniform vec2 uVegWind;
uniform float uVegWindTime;
uniform float uVegWindAmp;
`;

const WIND_VERTEX_MAIN = /* glsl */ `
  float h = max(transformed.y, 0.0);
  float phase = uVegWindTime * 1.7
    + transformed.x * 0.35
    + transformed.z * 0.41
    + position.x * 0.08;
  float sway = sin(phase) * h * uVegWindAmp;
  transformed.x += uVegWind.x * sway;
  transformed.z += uVegWind.y * sway;
`;

/**
 * Patch a material for wind sway. Must run BEFORE the material is handed to
 * InstancedMesh2 (which snapshots/wraps `onBeforeCompile`).
 */
export function maybePatchVegetationWindMaterial(
  state: State,
  url: string,
  mat: THREE.Material | null | undefined
): void {
  if (!mat) return;
  const urls = windUrlsByState.get(state);
  if (!urls || !urls.has(url)) return;
  if (windUniformsByMat.has(mat)) return;
  // CustomShaderMaterial owns onBeforeCompile — skip.
  if ('__csm' in mat) return;

  const uniforms: WindUniforms = {
    uVegWind: { value: new THREE.Vector2(0, 0) },
    uVegWindTime: { value: 0 },
    uVegWindAmp: { value: 0.12 },
  };
  const prev = mat.onBeforeCompile;
  mat.onBeforeCompile = (shader, renderer) => {
    if (typeof prev === 'function') prev.call(mat, shader, renderer);
    shader.uniforms.uVegWind = uniforms.uVegWind;
    shader.uniforms.uVegWindTime = uniforms.uVegWindTime;
    shader.uniforms.uVegWindAmp = uniforms.uVegWindAmp;
    if (!shader.vertexShader.includes('uVegWind')) {
      shader.vertexShader = shader.vertexShader.replace(
        'void main() {',
        `${WIND_VERTEX}\nvoid main() {`
      );
      shader.vertexShader = shader.vertexShader.replace(
        '#include <begin_vertex>',
        `#include <begin_vertex>\n${WIND_VERTEX_MAIN}`
      );
    }
  };
  mat.needsUpdate = true;
  windUniformsByMat.set(mat, uniforms);
  let live = windMatsByState.get(state);
  if (!live) {
    live = new Set();
    windMatsByState.set(state, live);
  }
  live.add(mat);
}

const vegetationQuery = defineQuery([Vegetation]);

let windTime = 0;

/**
 * Drives wind uniforms on materials patched via
 * {@link maybePatchVegetationWindMaterial}.
 */
export const VegetationWindSystem: System = {
  group: 'draw',
  update(state: State): void {
    windTime += state.time.deltaTime;

    for (const eid of vegetationQuery(state.world)) {
      if (Vegetation.wind[eid] === 1 && Vegetation.windRegistered[eid] !== 1) {
        Vegetation.windRegistered[eid] = 1;
      }
    }

    const live = windMatsByState.get(state);
    if (!live || live.size === 0) return;

    const wind = getWindVector(state);
    const strength = Math.hypot(wind.x, wind.z);
    const dirX = strength > 1e-6 ? wind.x / strength : 0;
    const dirZ = strength > 1e-6 ? wind.z / strength : 0;
    const amp = Math.min(0.28, 0.06 + strength * 0.04);

    for (const mat of live) {
      const u = windUniformsByMat.get(mat);
      if (!u) continue;
      u.uVegWind.value.set(dirX, dirZ);
      u.uVegWindTime.value = windTime;
      u.uVegWindAmp.value = amp;
    }
  },
};
