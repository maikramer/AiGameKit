import * as THREE from 'three';
import { defineQuery, type State, type System } from '../../core';
import { getScene, getRenderingContext } from '../rendering/utils';
import { CameraSyncSystem } from '../rendering/systems';
import { Sky } from './components';

const skyQuery = defineQuery([Sky]);

let nextUrlIndex = 1;
const urlByIndex = new Map<number, string>();

export function assignSkyUrl(url: string): number {
  const idx = nextUrlIndex++;
  urlByIndex.set(idx, url.trim());
  return idx;
}

function getUrl(index: number): string | undefined {
  return urlByIndex.get(index);
}

function rotateEquirectBitmap(
  tex: THREE.Texture,
  degrees: number
): THREE.Texture {
  const shift = ((degrees % 360) + 360) % 360;
  if (shift === 0) return tex;
  const img = tex.image as HTMLImageElement | undefined;
  if (!img || !img.width) return tex;
  if (typeof document === 'undefined') return tex;

  const w = img.width;
  const h = img.height;
  const sx = Math.round((shift / 360) * w) % w;
  if (sx === 0) return tex;

  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d')!;
  ctx.drawImage(img, sx, 0, w - sx, h, 0, 0, w - sx, h);
  ctx.drawImage(img, 0, 0, sx, h, w - sx, 0, sx, h);

  const out = new THREE.CanvasTexture(canvas);
  out.mapping = tex.mapping;
  out.colorSpace = tex.colorSpace;
  out.needsUpdate = true;
  tex.dispose();
  return out;
}

const pending = new WeakMap<State, Promise<void>>();

export const SkySystem: System = {
  group: 'draw',
  after: [CameraSyncSystem],
  update(state: State) {
    if (state.headless) return;
    if (pending.has(state)) return;

    const scene = getScene(state);
    const ctx = getRenderingContext(state);
    if (!scene || !ctx?.renderer) return;

    const entities = skyQuery(state.world);
    if (entities.length === 0) return;

    const eid = entities[0];
    if (Sky.loaded[eid] === 1) return;

    const url = getUrl(Sky.urlIndex[eid]);
    if (!url) return;

    const p = (async () => {
      const renderer = ctx!.renderer!;

      const loader = new THREE.TextureLoader();
      const loaded = await loader.loadAsync(url);
      loaded.mapping = THREE.EquirectangularReflectionMapping;
      loaded.colorSpace = THREE.SRGBColorSpace;

      const img = loaded.image as HTMLImageElement | undefined;
      if (img && img.width && img.height) {
        const ratio = img.width / img.height;
        if (Math.abs(ratio - 2.0) > 0.15) {
          console.warn(
            `[sky] Texture "${url}" aspect ratio ${ratio.toFixed(2)}:1 (expected 2:1). May look distorted.`
          );
        }
      }

      const tex = rotateEquirectBitmap(loaded, Sky.rotationDeg[eid]);

      const pmrem = new THREE.PMREMGenerator(renderer);
      pmrem.compileEquirectangularShader();
      const rt = pmrem.fromEquirectangular(tex);
      const envMap = rt.texture;
      tex.dispose();
      pmrem.dispose();

      scene.environment = envMap;
      scene.environmentIntensity = 0.22;
      if (Sky.setBackground[eid] === 1) {
        scene.background = envMap;
      }

      Sky.loaded[eid] = 1;
      pending.delete(state);
    })().catch((err) => {
      console.error(`[sky] Failed to load "${url}":`, err);
      pending.delete(state);
    });

    pending.set(state, p);
  },
};
