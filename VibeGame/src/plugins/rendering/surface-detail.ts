import * as THREE from 'three';

/**
 * Procedural micro-detail maps for large flat surfaces (roads, aprons, walls,
 * terrain).
 *
 * Why this exists: a `MeshStandardMaterial` with a flat colour (or a painted
 * albedo) and a single scalar roughness has **no** sub-metre variation, so a
 * road lit by a sun and an environment map returns exactly the same shading
 * over hundreds of square metres. That reads as plastic no matter how good the
 * lighting is — and it is the single biggest reason a screenshot looks like a
 * toy instead of a photograph. Real asphalt varies in both normal (aggregate
 * grain) and roughness (polished wheel tracks vs. matte shoulder), and it is
 * that *variation* the eye reads as "material".
 *
 * Everything here is generated once per kind into a small tiling texture and
 * shared, so the runtime cost is one extra texture fetch per map — no assets to
 * ship, no download, and no per-frame work.
 */

/** Detail profiles the engine knows how to synthesise. */
export type SurfaceDetailKind =
  'asphalt' | 'gravel' | 'dirt' | 'concrete' | 'metal';

export interface SurfaceDetailOptions {
  /** How many times the detail tile repeats over one UV unit (both axes). */
  repeat?: number;
  /** Per-axis override of {@link SurfaceDetailOptions.repeat}. Ribbon meshes
   * need it: a track's U runs 0..1 across the road while its V counts metres
   * down the circuit, so one scalar cannot tile both without stretching. */
  repeatX?: number;
  repeatY?: number;
  /** Strength of the normal perturbation (both axes). */
  normalScale?: number;
  /** Base roughness the kind's variance modulates around. */
  roughness?: number;
  /** Metalness to set alongside; omitted leaves the material's own value. */
  metalness?: number;
}

interface KindProfile {
  /** fBm octave count — more octaves = finer grain on top of the base shape. */
  octaves: number;
  /** Lattice cells across the tile for the first octave. */
  baseFrequency: number;
  /** Height amplitude falloff per octave. */
  persistence: number;
  /** Default normal strength for the kind. */
  normalScale: number;
  /** Default base roughness. */
  roughness: number;
  /** Default peak-to-peak roughness swing. */
  roughnessVariance: number;
  /** Extra sharp specks (aggregate stones, rust pits); 0 = none. */
  speckle: number;
}

const PROFILES: Record<SurfaceDetailKind, KindProfile> = {
  // Dense fine grain, mostly flat, with polished patches — tarmac.
  asphalt: {
    octaves: 4,
    baseFrequency: 8,
    persistence: 0.55,
    normalScale: 0.45,
    roughness: 0.62,
    roughnessVariance: 0.22,
    speckle: 0.35,
  },
  // Loose stones: big amplitude, coarse lattice, uniformly matte.
  gravel: {
    octaves: 3,
    baseFrequency: 14,
    persistence: 0.7,
    normalScale: 1.1,
    roughness: 0.95,
    roughnessVariance: 0.08,
    speckle: 0.6,
  },
  // Soft undulation, no specks — packed earth.
  dirt: {
    octaves: 4,
    baseFrequency: 5,
    persistence: 0.6,
    normalScale: 0.6,
    roughness: 0.9,
    roughnessVariance: 0.12,
    speckle: 0.08,
  },
  // Broad low-frequency mottling, slightly glossy where worn.
  concrete: {
    octaves: 4,
    baseFrequency: 4,
    persistence: 0.5,
    normalScale: 0.3,
    roughness: 0.78,
    roughnessVariance: 0.16,
    speckle: 0.12,
  },
  // Brushed sheen: very low bump, strong roughness banding.
  metal: {
    octaves: 3,
    baseFrequency: 3,
    persistence: 0.45,
    normalScale: 0.12,
    roughness: 0.42,
    roughnessVariance: 0.2,
    speckle: 0,
  },
};

/** Tile resolution. 256² tiles at a metre-ish repeat and stays under 256 KB. */
const TILE_SIZE = 256;

interface DetailTextures {
  normal: THREE.Texture;
  roughness: THREE.Texture;
}

const cache = new Map<SurfaceDetailKind, DetailTextures>();

/** Deterministic PRNG so a given kind always synthesises the same tile. */
function makeRandom(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

function smoothstep(t: number): number {
  return t * t * (3 - 2 * t);
}

/**
 * Value noise on a wrapping lattice, so the tile repeats seamlessly. Perlin
 * would be smoother, but a road wants grain, not smoothness, and value noise
 * is a third of the code with no visible penalty once fBm stacks it.
 */
function valueNoise(
  size: number,
  cells: number,
  random: () => number
): Float32Array {
  const lattice = new Float32Array(cells * cells);
  for (let i = 0; i < lattice.length; i++) lattice[i] = random();

  const out = new Float32Array(size * size);
  const scale = cells / size;
  for (let y = 0; y < size; y++) {
    const fy = y * scale;
    const y0 = Math.floor(fy) % cells;
    const y1 = (y0 + 1) % cells;
    const ty = smoothstep(fy - Math.floor(fy));
    for (let x = 0; x < size; x++) {
      const fx = x * scale;
      const x0 = Math.floor(fx) % cells;
      const x1 = (x0 + 1) % cells;
      const tx = smoothstep(fx - Math.floor(fx));

      const a = lattice[y0 * cells + x0]!;
      const b = lattice[y0 * cells + x1]!;
      const c = lattice[y1 * cells + x0]!;
      const d = lattice[y1 * cells + x1]!;
      const top = a + (b - a) * tx;
      const bottom = c + (d - c) * tx;
      out[y * size + x] = top + (bottom - top) * ty;
    }
  }
  return out;
}

/** Stacked octaves of {@link valueNoise}, normalised to 0..1. */
function fbm(size: number, profile: KindProfile, seed: number): Float32Array {
  const random = makeRandom(seed);
  const out = new Float32Array(size * size);
  let amplitude = 1;
  let total = 0;
  let cells = profile.baseFrequency;
  for (let o = 0; o < profile.octaves; o++) {
    const layer = valueNoise(size, cells, random);
    for (let i = 0; i < out.length; i++) out[i] += layer[i]! * amplitude;
    total += amplitude;
    amplitude *= profile.persistence;
    // Double the lattice each octave, but never finer than one cell per texel.
    cells = Math.min(size, cells * 2);
  }
  for (let i = 0; i < out.length; i++) out[i] /= total;

  if (profile.speckle > 0) {
    // Individual bright/dark texels: the aggregate stones a smooth fBm never
    // produces, and what sells asphalt and gravel at close range.
    const speckRandom = makeRandom(seed ^ 0x9e3779b9);
    const count = Math.floor(size * size * 0.06 * profile.speckle);
    for (let i = 0; i < count; i++) {
      const idx = Math.floor(speckRandom() * out.length);
      const delta = (speckRandom() - 0.5) * profile.speckle;
      out[idx] = Math.min(1, Math.max(0, out[idx]! + delta));
    }
  }
  return out;
}

function wrapIndex(v: number, size: number): number {
  return (v + size) % size;
}

/**
 * Height field → tangent-space normal map via central differences. The
 * neighbour lookups wrap, so the normal map tiles as seamlessly as the height
 * it came from.
 */
function heightToNormalTexture(
  height: Float32Array,
  size: number,
  strength: number
): THREE.Texture {
  const data = new Uint8Array(size * size * 4);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const left = height[y * size + wrapIndex(x - 1, size)]!;
      const right = height[y * size + wrapIndex(x + 1, size)]!;
      const up = height[wrapIndex(y - 1, size) * size + x]!;
      const down = height[wrapIndex(y + 1, size) * size + x]!;

      const dx = (left - right) * strength;
      const dy = (up - down) * strength;
      const len = Math.hypot(dx, dy, 1);
      const i = (y * size + x) * 4;
      data[i] = Math.round(((dx / len) * 0.5 + 0.5) * 255);
      data[i + 1] = Math.round(((dy / len) * 0.5 + 0.5) * 255);
      data[i + 2] = Math.round((1 / len) * 0.5 * 255 + 127.5);
      data[i + 3] = 255;
    }
  }
  const tex = new THREE.DataTexture(data, size, size, THREE.RGBAFormat);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.colorSpace = THREE.NoColorSpace;
  tex.generateMipmaps = true;
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.needsUpdate = true;
  return tex;
}

/**
 * Roughness variation from the same height field, offset so the pattern does
 * not line up with the bumps texel-for-texel (real wear does not follow the
 * grain exactly).
 *
 * three multiplies the material's scalar `roughness` by this map's **green**
 * channel, so the map is written as a *multiplier* in `[1 - variance, 1]`
 * rather than as raw roughness. That keeps the caller's `roughness` the mean
 * of the surface instead of double what it asked for.
 */
function heightToRoughnessTexture(
  height: Float32Array,
  size: number,
  variance: number
): THREE.Texture {
  const data = new Uint8Array(size * size * 4);
  const shift = Math.floor(size * 0.37);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const src =
        height[wrapIndex(y + shift, size) * size + wrapIndex(x + shift, size)]!;
      const v = Math.round((1 - variance * (1 - src)) * 255);
      const i = (y * size + x) * 4;
      // Three reads roughness from G and metalness from B; keep both channels
      // meaningful so the same texture can serve as a metalnessMap if needed.
      data[i] = v;
      data[i + 1] = v;
      data[i + 2] = v;
      data[i + 3] = 255;
    }
  }
  const tex = new THREE.DataTexture(data, size, size, THREE.RGBAFormat);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.colorSpace = THREE.NoColorSpace;
  tex.generateMipmaps = true;
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.needsUpdate = true;
  return tex;
}

/** Synthesise (once) and cache the detail pair for a kind. */
export function getSurfaceDetailTextures(
  kind: SurfaceDetailKind
): DetailTextures {
  const cached = cache.get(kind);
  if (cached) return cached;

  const profile = PROFILES[kind];
  // Seed from the kind name so tiles are stable across reloads and machines.
  let seed = 0;
  for (let i = 0; i < kind.length; i++)
    seed = (seed * 31 + kind.charCodeAt(i)) | 0;

  const height = fbm(TILE_SIZE, profile, seed);
  const textures: DetailTextures = {
    normal: heightToNormalTexture(height, TILE_SIZE, 6),
    roughness: heightToRoughnessTexture(
      height,
      TILE_SIZE,
      profile.roughnessVariance
    ),
  };
  cache.set(kind, textures);
  return textures;
}

/** Clones keyed by `kind:repeat`. See {@link getTiledDetailTextures}. */
const tiledCache = new Map<string, DetailTextures>();

/**
 * A detail pair whose UV repeat is already baked in.
 *
 * `repeat` lives on the Texture, not on the material slot, so two materials
 * sharing one Texture object cannot ask for different tiling — the last
 * assignment would silently retile every surface using that kind. Cloning per
 * repeat keeps them independent; the clones share the same image data, so the
 * cost is a texture handle, not another synthesis pass.
 */
function getTiledDetailTextures(
  kind: SurfaceDetailKind,
  repeatX: number,
  repeatY: number
): DetailTextures {
  const key = `${kind}:${repeatX}:${repeatY}`;
  const cached = tiledCache.get(key);
  if (cached) return cached;

  const base = getSurfaceDetailTextures(kind);
  const normal = base.normal.clone();
  const roughness = base.roughness.clone();
  for (const tex of [normal, roughness]) {
    tex.repeat.set(repeatX, repeatY);
    tex.wrapS = THREE.RepeatWrapping;
    tex.wrapT = THREE.RepeatWrapping;
    tex.needsUpdate = true;
  }
  const pair = { normal, roughness };
  tiledCache.set(key, pair);
  return pair;
}

/**
 * Give a material the micro-detail of `kind`.
 *
 * The material keeps its own albedo (`map`/`color`) — this only adds the
 * normal and roughness variation that make the albedo read as a surface. Safe
 * to call more than once on the same material: the second call just re-tunes
 * the numbers.
 */
export function applySurfaceDetail(
  material: THREE.MeshStandardMaterial,
  kind: SurfaceDetailKind,
  options: SurfaceDetailOptions = {}
): void {
  const profile = PROFILES[kind];
  const repeat = options.repeat ?? 8;
  const { normal, roughness } = getTiledDetailTextures(
    kind,
    options.repeatX ?? repeat,
    options.repeatY ?? repeat
  );

  material.normalMap = normal;
  const scale = options.normalScale ?? profile.normalScale;
  material.normalScale = new THREE.Vector2(scale, scale);

  material.roughnessMap = roughness;
  // The map is a multiplier averaging `1 - variance/2`; dividing it out keeps
  // the requested value as the *mean* roughness of the surface.
  const base = options.roughness ?? profile.roughness;
  material.roughness = Math.min(
    1,
    base / Math.max(0.05, 1 - profile.roughnessVariance * 0.5)
  );

  if (options.metalness !== undefined) material.metalness = options.metalness;
  material.needsUpdate = true;
}

/** Drop the synthesised tiles (used by tests and by full renderer teardown). */
export function disposeSurfaceDetail(): void {
  for (const { normal, roughness } of tiledCache.values()) {
    normal.dispose();
    roughness.dispose();
  }
  tiledCache.clear();
  for (const { normal, roughness } of cache.values()) {
    normal.dispose();
    roughness.dispose();
  }
  cache.clear();
}
