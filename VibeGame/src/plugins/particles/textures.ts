import * as THREE from 'three';

/** Default Vite/public path for game particle sprites. */
let textureBaseUrl = '/assets/particles/';

const textureCache = new Map<string, THREE.Texture>();
let softFallback: THREE.Texture | null = null;

/** Semantic sprite file per preset (hosted under textureBaseUrl). */
export const PRESET_TEXTURE_FILE = {
  fire: 'flame.png',
  rain: 'rain.png',
  snow: 'snow.png',
  smoke: 'smoke.png',
  dust: 'dust.png',
  explosion: 'explosion.png',
  sparks: 'spark.png',
  magic: 'magic.png',
  fireflies: 'firefly.png',
  splash: 'splash.png',
  woodchips: 'woodchip.png',
  rockshards: 'rockshard.png',
  leaves: 'leaf.png',
  // 'ground-dust' foi acrescentado aos PRESET_NAMES sem entrada aqui, e o
  // lençol de poeira do deserto ficava sem sprite (o teste "maps every preset
  // to a sprite filename" apanhava-o). Partilha o sprite do 'dust'.
  'ground-dust': 'dust.png',
} as const;

export type ParticleTexturePreset = keyof typeof PRESET_TEXTURE_FILE;

export function setParticleTextureBaseUrl(url: string): void {
  textureBaseUrl = url.endsWith('/') ? url : `${url}/`;
  textureCache.clear();
}

export function getParticleTextureBaseUrl(): string {
  return textureBaseUrl;
}

function canUseTextureLoader(): boolean {
  return typeof document !== 'undefined' && typeof Image !== 'undefined';
}

/** Soft radial sprite so particles never render as hard squares. */
export function createSoftCircleTexture(size = 64): THREE.DataTexture {
  const data = new Uint8Array(size * size * 4);
  const cx = (size - 1) * 0.5;
  const cy = (size - 1) * 0.5;
  const maxR = cx;

  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const dx = x - cx;
      const dy = y - cy;
      const t = Math.min(1, Math.sqrt(dx * dx + dy * dy) / maxR);
      // Smooth falloff; opaque core, transparent rim.
      const a = Math.round((1 - t) ** 2 * 255);
      const i = (y * size + x) * 4;
      data[i] = 255;
      data[i + 1] = 255;
      data[i + 2] = 255;
      data[i + 3] = a;
    }
  }

  const tex = new THREE.DataTexture(data, size, size, THREE.RGBAFormat);
  tex.needsUpdate = true;
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.premultiplyAlpha = false;
  return tex;
}

function getSoftFallback(): THREE.Texture {
  if (!softFallback) softFallback = createSoftCircleTexture();
  return softFallback;
}

function configureMap(tex: THREE.Texture): void {
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.wrapS = THREE.ClampToEdgeWrapping;
  tex.wrapT = THREE.ClampToEdgeWrapping;
  tex.generateMipmaps = true;
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  tex.magFilter = THREE.LinearFilter;
}

/**
 * Resolve the sprite for a preset. Uses TextureLoader in the browser (async
 * image fill on the returned Texture) and a soft DataTexture fallback in
 * headless / when the image path is unavailable.
 */
export function getParticleTexture(
  preset: ParticleTexturePreset | string
): THREE.Texture {
  const file =
    PRESET_TEXTURE_FILE[preset as ParticleTexturePreset] ?? 'soft.png';
  const cached = textureCache.get(file);
  if (cached) return cached;

  if (!canUseTextureLoader()) {
    const soft = getSoftFallback();
    textureCache.set(file, soft);
    return soft;
  }

  const loader = new THREE.TextureLoader();
  const url = `${textureBaseUrl}${file}`;
  const tex = loader.load(url, undefined, undefined, () => {
    // Future lookups use soft circle if the file 404s.
    textureCache.set(file, getSoftFallback());
  });
  configureMap(tex);
  textureCache.set(file, tex);
  return tex;
}

export function particleMaterial(options: {
  preset: ParticleTexturePreset | string;
  additive?: boolean;
  opacity?: number;
}): THREE.MeshBasicMaterial {
  const map = getParticleTexture(options.preset);
  return new THREE.MeshBasicMaterial({
    map,
    transparent: true,
    opacity: options.opacity ?? 1,
    blending: options.additive ? THREE.AdditiveBlending : THREE.NormalBlending,
    depthWrite: false,
    // Discard empty texels; keep low so soft smoke edges stay visible.
    alphaTest: 0.01,
  });
}

/** Warm the TextureLoader cache for every preset (browser only). */
export function preloadParticleTextures(): void {
  if (!canUseTextureLoader()) return;
  for (const name of Object.keys(
    PRESET_TEXTURE_FILE
  ) as ParticleTexturePreset[]) {
    getParticleTexture(name);
  }
}
