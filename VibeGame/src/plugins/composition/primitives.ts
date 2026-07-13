import * as RAPIER from '@dimforge/rapier3d-compat';
import * as THREE from 'three';
import type { State, XMLValue } from '../../core';

export type PrimitiveKind = 'box' | 'sphere' | 'cylinder' | 'plane' | 'pad';

export interface PrimitiveSpec {
  readonly kind: PrimitiveKind;
  readonly posX: number;
  readonly posY: number;
  readonly posZ: number;
  readonly rotX: number;
  readonly rotY: number;
  readonly rotZ: number;
  readonly sizeX: number;
  readonly sizeY: number;
  readonly sizeZ: number;
  readonly colorR: number;
  readonly colorG: number;
  readonly colorB: number;
  // Textura opcional (URL site-root, ex.: "/assets/textures/foo.png").
  // Quando ausente, a primitiva usa cor flat (colorR/G/B).
  readonly textureUrl: string | null;
  // Repetição da textura (UV repeat em X/Y). Default 1 1.
  readonly textureRepeatX: number;
  readonly textureRepeatY: number;
  // Rotação da textura em radianos (UV rotation). Default 0.
  readonly textureRotation: number;
  // Normal/roughness/AO maps opcionais (PBR), mesmas UVs do map.
  readonly normalMapUrl: string | null;
  readonly roughnessMapUrl: string | null;
  // Roughness/metalness fixos (defaults conservadores para materiais têxteis).
  readonly roughness: number;
  readonly metalness: number;
  // Opacidade do material (0=transparente, 1=opaco). Default 1.
  // Permite blend progressivo de estradas/calçadas sobre o terreno.
  readonly opacity: number;
  // --- Campos específicos do kind 'pad' (decal de chão) ---
  // Largura em metros do fade de alpha da borda para dentro (0 = borda dura).
  readonly edgeFeather: number;
  // Feather por lado [oeste(-x), este(+x), norte(-z), sul(+z)] quando o XML
  // declara edge-feather com 2 ("fx fz") ou 4 ("w e s n") valores; null =
  // uniforme (edgeFeather). Lados com 0 ficam sólidos até à orla — usado para
  // enterrar a ponta de uma estrada sob o núcleo opaco de uma praça sem
  // costura semi-transparente na junção.
  readonly edgeFeathers: readonly [number, number, number, number] | null;
  // Raio dos cantos arredondados em metros (0 = cantos retos).
  readonly cornerRadius: number;
  // Amplitude em metros de ruído orgânico que corrói a borda para dentro
  // (0 = borda geométrica limpa). Dá aspeto de calçada gasta/irregular.
  readonly edgeNoise: number;
}

export type ColliderMode = 'auto' | 'none';

export interface CompositionData {
  readonly specs: PrimitiveSpec[];
  readonly colliderMode: ColliderMode;
}

const stateToData = new WeakMap<State, Map<number, CompositionData>>();

export function getCompositionData(
  state: State,
  entity: number
): CompositionData | undefined {
  return stateToData.get(state)?.get(entity);
}

export function setCompositionData(
  state: State,
  entity: number,
  data: CompositionData
): void {
  let m = stateToData.get(state);
  if (!m) {
    m = new Map();
    stateToData.set(state, m);
  }
  m.set(entity, data);
}

export function deleteCompositionData(state: State, entity: number): void {
  stateToData.get(state)?.delete(entity);
}

const PRIMITIVE_TAGS = new Set<string>([
  'box',
  'sphere',
  'cylinder',
  'plane',
  'pad',
]);

export function isPrimitiveTag(tagName: string): boolean {
  return PRIMITIVE_TAGS.has(tagName.toLowerCase());
}

function toFloat(value: XMLValue | undefined, fallback: number): number {
  if (value === undefined || value === null) return fallback;
  if (typeof value === 'number') return value;
  if (typeof value === 'boolean') return value ? 1 : 0;
  const n = parseFloat(String(value));
  return Number.isNaN(n) ? fallback : n;
}

// XMLValueParser (core/xml/values) already converts vector strings like
// "0 1.6 -3" into `{x,y,z}` objects and hex colors like "#6b4a2b" / "0x..."
// into numbers, so accept every shape here instead of assuming strings.
function parseVec3(
  value: XMLValue | undefined,
  fallback: [number, number, number]
): [number, number, number] {
  if (typeof value === 'string') {
    const parts = value
      .trim()
      .split(/\s+/)
      .map((p) => parseFloat(p));
    if (parts.length >= 3 && parts.every((n) => !Number.isNaN(n))) {
      return [parts[0]!, parts[1]!, parts[2]!];
    }
    if (parts.length === 1 && !Number.isNaN(parts[0])) {
      return [parts[0]!, parts[0]!, parts[0]!];
    }
    return fallback;
  }
  if (typeof value === 'number' && !Number.isNaN(value)) {
    return [value, value, value];
  }
  if (Array.isArray(value) && value.length >= 3) {
    return [Number(value[0]), Number(value[1]), Number(value[2])];
  }
  if (value && typeof value === 'object') {
    const v = value as Record<string, unknown>;
    const x = Number(v.x);
    const y = Number(v.y);
    const z = Number(v.z ?? v.w);
    if (!Number.isNaN(x) && !Number.isNaN(y) && !Number.isNaN(z)) {
      return [x, y, z];
    }
  }
  return fallback;
}

export function parseColorHex(
  value: XMLValue | undefined
): [number, number, number] {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return [
      ((value >> 16) & 0xff) / 255,
      ((value >> 8) & 0xff) / 255,
      (value & 0xff) / 255,
    ];
  }
  if (typeof value !== 'string' || value.trim() === '') {
    return [0.8, 0.8, 0.8];
  }
  let hex = value.trim();
  if (hex[0] === '#') hex = hex.slice(1);
  if (hex.length === 3) {
    hex = hex
      .split('')
      .map((c) => c + c)
      .join('');
  }
  const num = parseInt(hex, 16);
  if (Number.isNaN(num)) return [0.8, 0.8, 0.8];
  return [
    ((num >> 16) & 0xff) / 255,
    ((num >> 8) & 0xff) / 255,
    (num & 0xff) / 255,
  ];
}

const ZERO_VEC: [number, number, number] = [0, 0, 0];

function parseTextureRepeat(value: XMLValue | undefined): [number, number] {
  if (value === undefined || value === null) return [1, 1];
  if (typeof value === 'number') return [value, value];
  // O XMLValueParser converte "2 1" em {x:2, y:1} antes de chegarmos aqui —
  // sem este ramo, qualquer repeat de 2 componentes vindo do XML caía no
  // fallback [1,1] (String({x,y}) = "[object Object]").
  if (typeof value === 'object' && !Array.isArray(value)) {
    const v = value as Record<string, unknown>;
    const x = Number(v.x);
    const y = Number(v.y);
    if (!Number.isNaN(x) && !Number.isNaN(y)) return [x, y];
    return [1, 1];
  }
  const parts = String(value)
    .trim()
    .split(/\s+/)
    .map((p) => parseFloat(p));
  if (parts.length >= 2 && parts.every((n) => !Number.isNaN(n))) {
    return [parts[0]!, parts[1]!];
  }
  if (parts.length === 1 && !Number.isNaN(parts[0])) {
    return [parts[0]!, parts[0]!];
  }
  return [1, 1];
}

export function parsePrimitiveSpec(
  tagName: string,
  attributes: Record<string, XMLValue>
): PrimitiveSpec {
  const kind = tagName.toLowerCase() as PrimitiveKind;
  const [posX, posY, posZ] = parseVec3(attributes.pos, ZERO_VEC);
  const [rotX, rotY, rotZ] = parseVec3(attributes.rotation, ZERO_VEC);
  let [sizeX, sizeY, sizeZ] = parseVec3(attributes.size, [1, 1, 1]);
  // Pad aceita size="W D" (2 componentes: largura X × profundidade Z).
  // O XMLValueParser já converte "16 12" em {x:16, y:12}, por isso o caso
  // objeto-sem-z é o caminho normal; string cobre chamadas programáticas.
  if (kind === 'pad') {
    const raw = attributes.size;
    let two: [number, number] | null = null;
    if (typeof raw === 'string') {
      const parts = raw
        .trim()
        .split(/\s+/)
        .map((p) => parseFloat(p));
      if (parts.length === 2 && parts.every((n) => !Number.isNaN(n))) {
        two = [parts[0]!, parts[1]!];
      }
    } else if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
      const v = raw as Record<string, unknown>;
      if (v.z === undefined && v.w === undefined) {
        const x = Number(v.x);
        const y = Number(v.y);
        if (!Number.isNaN(x) && !Number.isNaN(y)) two = [x, y];
      }
    }
    if (two) {
      sizeX = two[0];
      sizeY = 1;
      sizeZ = two[1];
    }
  }
  const [colorR, colorG, colorB] = parseColorHex(attributes.color);
  // Textura opcional: aceitar tanto texture-url como map-url (alias).
  const textureUrlRaw =
    attributes['texture-url'] ?? attributes['map-url'] ?? attributes.texture;
  const textureUrl =
    typeof textureUrlRaw === 'string' && textureUrlRaw.trim() !== ''
      ? textureUrlRaw.trim()
      : null;
  let [textureRepeatX, textureRepeatY] = parseTextureRepeat(
    attributes['texture-repeat']
  );
  // texture-scale: metros de mundo por tile de textura. Deriva o repeat do
  // tamanho real da primitiva — pads/planos vizinhos de tamanhos diferentes
  // ficam com densidade idêntica sem contas manuais nem textura espremida.
  // Tem precedência sobre texture-repeat quando ambos são declarados.
  const textureScale = toFloat(attributes['texture-scale'], 0);
  if (textureScale > 0 && (kind === 'pad' || kind === 'plane')) {
    const spanY = kind === 'pad' ? sizeZ : sizeY;
    textureRepeatX = Math.max(sizeX, 1e-4) / textureScale;
    textureRepeatY = Math.max(spanY, 1e-4) / textureScale;
  }
  const textureRotation = toFloat(attributes['texture-rotation'], 0);
  const normalMapUrlRaw =
    attributes['normal-map-url'] ?? attributes['normal-url'];
  const normalMapUrl =
    typeof normalMapUrlRaw === 'string' && normalMapUrlRaw.trim() !== ''
      ? normalMapUrlRaw.trim()
      : null;
  const roughnessMapUrlRaw =
    attributes['roughness-map-url'] ?? attributes['roughness-url'];
  const roughnessMapUrl =
    typeof roughnessMapUrlRaw === 'string' && roughnessMapUrlRaw.trim() !== ''
      ? roughnessMapUrlRaw.trim()
      : null;
  // Roughness/metalness ajustáveis (defaults conservadores).
  const roughness = Math.max(0, Math.min(1, toFloat(attributes.roughness, 1)));
  const metalness = Math.max(0, Math.min(1, toFloat(attributes.metalness, 0)));
  const opacity = Math.max(0, Math.min(1, toFloat(attributes.opacity, 1)));
  // Bordas suaves do pad: feather default 0.8 m (o ponto do pad é ser
  // seamless com o terreno); explicitar edge-feather="0" devolve borda dura.
  // Aceita 1 valor (uniforme), 2 ("fx fz" — lados X e lados Z) ou 4
  // ("w e n s" — por lado). O XMLValueParser entrega 2/4 valores como
  // objetos {x,y}/{x,y,z,w}.
  const featherRaw = attributes['edge-feather'] ?? attributes.feather;
  let edgeFeathers: [number, number, number, number] | null = null;
  if (
    featherRaw &&
    typeof featherRaw === 'object' &&
    !Array.isArray(featherRaw)
  ) {
    const v = featherRaw as Record<string, unknown>;
    const fx = Math.max(0, toFloat(v.x as XMLValue, 0));
    const fy = Math.max(0, toFloat(v.y as XMLValue, 0));
    if (v.z !== undefined && v.w !== undefined) {
      const fz = Math.max(0, toFloat(v.z as XMLValue, 0));
      const fw = Math.max(0, toFloat(v.w as XMLValue, 0));
      edgeFeathers = [fx, fy, fz, fw]; // w e n s
    } else {
      edgeFeathers = [fx, fx, fy, fy]; // fx nos lados X, fy nos lados Z
    }
  }
  const edgeFeather = edgeFeathers
    ? Math.max(...edgeFeathers)
    : Math.max(0, toFloat(featherRaw, kind === 'pad' ? 0.8 : 0));
  const cornerRadius = Math.max(0, toFloat(attributes['corner-radius'], 0));
  const edgeNoise = Math.max(0, toFloat(attributes['edge-noise'], 0));
  return {
    kind,
    posX,
    posY,
    posZ,
    rotX,
    rotY,
    rotZ,
    sizeX,
    sizeY,
    sizeZ,
    colorR,
    colorG,
    colorB,
    textureUrl,
    textureRepeatX,
    textureRepeatY,
    textureRotation,
    normalMapUrl,
    roughnessMapUrl,
    roughness,
    metalness,
    opacity,
    edgeFeather,
    edgeFeathers,
    cornerRadius,
    edgeNoise,
  };
}

function primitiveGeometry(spec: PrimitiveSpec): THREE.BufferGeometry {
  switch (spec.kind) {
    case 'box':
      return new THREE.BoxGeometry(spec.sizeX, spec.sizeY, spec.sizeZ);
    case 'sphere': {
      const radius = Math.max(spec.sizeX, 1e-4);
      return new THREE.SphereGeometry(radius, 16, 12);
    }
    case 'cylinder': {
      const radiusTop = Math.max(spec.sizeX, 1e-4);
      const radiusBottom = Math.max(spec.sizeY, 1e-4);
      const height = Math.max(spec.sizeZ, 1e-4);
      return new THREE.CylinderGeometry(radiusTop, radiusBottom, height, 16);
    }
    case 'plane': {
      const width = Math.max(spec.sizeX, 1e-4);
      const height = Math.max(spec.sizeY, 1e-4);
      return new THREE.PlaneGeometry(width, height);
    }
    case 'pad': {
      // Decal de chão: plano deitado no XZ (normal +Y), largura X ×
      // profundidade Z. UVs 0..1 em toda a extensão — o alphaMap de borda
      // mapeia 1:1 sobre o pad.
      const width = Math.max(spec.sizeX, 1e-4);
      const depth = Math.max(spec.sizeZ, 1e-4);
      const geo = new THREE.PlaneGeometry(width, depth);
      geo.rotateX(-Math.PI / 2);
      return geo;
    }
    default:
      return new THREE.BoxGeometry(1, 1, 1);
  }
}

// --- Alpha de borda do pad (SDF rounded-rect + feather + ruído orgânico) ---

// Hash determinístico → [0,1). Mesmo seed ⇒ mesma borda entre reloads.
function hash2(ix: number, iz: number, seed: number): number {
  const s = Math.sin(ix * 127.1 + iz * 311.7 + seed * 74.7) * 43758.5453;
  return s - Math.floor(s);
}

// Value noise bilinear em coordenadas de metros.
function valueNoise2(x: number, z: number, seed: number): number {
  const ix = Math.floor(x);
  const iz = Math.floor(z);
  const fx = x - ix;
  const fz = z - iz;
  const ux = fx * fx * (3 - 2 * fx);
  const uz = fz * fz * (3 - 2 * fz);
  const a = hash2(ix, iz, seed);
  const b = hash2(ix + 1, iz, seed);
  const c = hash2(ix, iz + 1, seed);
  const d = hash2(ix + 1, iz + 1, seed);
  return a + (b - a) * ux + (c - a) * uz + (a - b - c + d) * ux * uz;
}

/** Resolução do alphaMap: ~8 px/m, clamped para não explodir em pads grandes. */
const PAD_ALPHA_PX_PER_METER = 8;
const PAD_ALPHA_MIN_PX = 16;
const PAD_ALPHA_MAX_PX = 512;

/**
 * Calcula o canal de alpha da borda de um pad como grelha width×height de
 * bytes (0..255). Puro e determinístico (seed derivado da posição) — testável
 * sem GPU/DOM. Interior opaco; borda desvanece ao longo de `edgeFeather`
 * metros seguindo um SDF de retângulo com cantos `cornerRadius`; `edgeNoise`
 * corrói a borda para DENTRO (nunca para fora — o alpha na orla da geometria
 * é sempre 0, sem cortes duros).
 *
 * Com `edgeFeathers` (por lado, [w,e,n,s]) o alpha é o mínimo dos 4 fades
 * axiais; lados com feather 0 ficam sólidos até à orla e sem ruído — é assim
 * que a ponta de uma estrada mergulha sob uma praça sem costura translúcida.
 * Per-side ignora `cornerRadius` (usar um ou outro).
 */
export function computePadAlphaData(
  spec: PrimitiveSpec,
  width: number,
  height: number
): Uint8Array {
  const data = new Uint8Array(width * height);
  const sizeX = Math.max(spec.sizeX, 1e-4);
  const sizeZ = Math.max(spec.sizeZ, 1e-4);
  const noiseAmp = spec.edgeNoise;
  const seed = spec.posX * 13.13 + spec.posZ * 7.77;
  const perSide = spec.edgeFeathers;

  const noiseAt = (px: number, pz: number): number => {
    if (noiseAmp <= 0) return 0;
    return (
      noiseAmp *
      (0.65 * valueNoise2(px / 1.6, pz / 1.6, seed) +
        0.35 * valueNoise2(px / 0.55, pz / 0.55, seed + 91.3))
    );
  };

  const hermite = (a: number): number => a * a * (3 - 2 * a);

  // Fade axial de um lado: dist = distância (m) da orla desse lado para
  // dentro; feather 0 ⇒ degrau sólido na orla, sem erosão de ruído.
  const sideAlpha = (dist: number, feather: number, noise: number): number => {
    if (feather <= 0) return dist >= 0 ? 1 : 0;
    return hermite(Math.min(Math.max((dist - noise) / feather, 0), 1));
  };

  if (perSide) {
    const [fW, fE, fN, fS] = perSide;
    const hx = sizeX / 2;
    const hz = sizeZ / 2;
    for (let j = 0; j < height; j++) {
      const pz = ((j + 0.5) / height - 0.5) * sizeZ;
      for (let i = 0; i < width; i++) {
        const px = ((i + 0.5) / width - 0.5) * sizeX;
        const noise = noiseAt(px, pz);
        const a = Math.min(
          sideAlpha(px + hx, fW, noise),
          sideAlpha(hx - px, fE, noise),
          sideAlpha(pz + hz, fN, noise),
          sideAlpha(hz - pz, fS, noise)
        );
        data[j * width + i] = Math.round(a * 255);
      }
    }
    return data;
  }

  const maxRadius = Math.min(sizeX, sizeZ) / 2;
  const radius = Math.min(spec.cornerRadius, maxRadius);
  const halfX = sizeX / 2 - radius;
  const halfZ = sizeZ / 2 - radius;
  const feather = spec.edgeFeather;

  for (let j = 0; j < height; j++) {
    const v = (j + 0.5) / height;
    const pz = (v - 0.5) * sizeZ;
    for (let i = 0; i < width; i++) {
      const u = (i + 0.5) / width;
      const px = (u - 0.5) * sizeX;

      // SDF de retângulo arredondado (negativo dentro, 0 na orla).
      const qx = Math.abs(px) - halfX;
      const qz = Math.abs(pz) - halfZ;
      const outX = Math.max(qx, 0);
      const outZ = Math.max(qz, 0);
      let d =
        Math.sqrt(outX * outX + outZ * outZ) +
        Math.min(Math.max(qx, qz), 0) -
        radius;

      // Ruído só corrói para dentro (n∈[0,1] ⇒ d cresce), garantindo alpha 0
      // na orla da geometria mesmo com amplitudes grandes.
      d += noiseAt(px, pz);

      let a: number;
      if (feather > 0) {
        a = hermite(Math.min(Math.max(-d / feather, 0), 1));
      } else {
        a = d < 0 ? 1 : 0;
      }
      data[j * width + i] = Math.round(a * 255);
    }
  }
  return data;
}

function buildPadAlphaTexture(spec: PrimitiveSpec): THREE.DataTexture {
  const w = Math.min(
    PAD_ALPHA_MAX_PX,
    Math.max(PAD_ALPHA_MIN_PX, Math.round(spec.sizeX * PAD_ALPHA_PX_PER_METER))
  );
  const h = Math.min(
    PAD_ALPHA_MAX_PX,
    Math.max(PAD_ALPHA_MIN_PX, Math.round(spec.sizeZ * PAD_ALPHA_PX_PER_METER))
  );
  const alpha = computePadAlphaData(spec, w, h);
  // alphaMap lê o canal G; RGBA com todos os canais = alpha é o mais seguro.
  const rgba = new Uint8Array(w * h * 4);
  for (let i = 0; i < alpha.length; i++) {
    const a = alpha[i]!;
    rgba[i * 4] = a;
    rgba[i * 4 + 1] = a;
    rgba[i * 4 + 2] = a;
    rgba[i * 4 + 3] = a;
  }
  const tex = new THREE.DataTexture(rgba, w, h, THREE.RGBAFormat);
  tex.wrapS = THREE.ClampToEdgeWrapping;
  tex.wrapT = THREE.ClampToEdgeWrapping;
  tex.magFilter = THREE.LinearFilter;
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  tex.generateMipmaps = true;
  tex.needsUpdate = true;
  return tex;
}

// Cache de texturas partilhado por todas as composições (mesmo padrão do
// terrain/sky: module-level TextureLoader reutilizado, cache por URL).
// Cada URL é carregado uma única vez; a repetição/rotação UV é aplicada por
// material via texture.clone() quando uma primitiva precisa de UVs diferentes.
const _compositionTextureLoader = new THREE.TextureLoader();
const _compositionTextureCache = new Map<string, THREE.Texture>();

function loadCompositionTexture(url: string): THREE.Texture {
  const cached = _compositionTextureCache.get(url);
  if (cached) return cached;
  const tex = _compositionTextureLoader.load(url);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  tex.needsUpdate = true;
  _compositionTextureCache.set(url, tex);
  return tex;
}

function loadCompositionDataTexture(url: string): THREE.Texture {
  // Texturas de dados (normal/roughness/AO) vivem em espaço linear.
  const cached = _compositionTextureCache.get(url);
  if (cached) return cached;
  const tex = _compositionTextureLoader.load(url);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.colorSpace = THREE.LinearSRGBColorSpace;
  tex.anisotropy = 8;
  tex.needsUpdate = true;
  _compositionTextureCache.set(url, tex);
  return tex;
}

export function buildPrimitiveMesh(spec: PrimitiveSpec): THREE.Mesh {
  const material = new THREE.MeshStandardMaterial({
    color: new THREE.Color(spec.colorR, spec.colorG, spec.colorB),
    roughness: spec.roughness,
    metalness: spec.metalness,
    side: spec.kind === 'plane' ? THREE.DoubleSide : THREE.FrontSide,
  });
  if (spec.opacity < 1) {
    material.transparent = true;
    material.opacity = spec.opacity;
    material.depthWrite = false;
  }

  // Pad (decal de chão): blend suave com o terreno via alphaMap procedural.
  // Sem onBeforeCompile — sobrevive intacto ao patch de CSM (que reatribui
  // onBeforeCompile em setupCsmMaterial).
  if (spec.kind === 'pad') {
    if (
      spec.edgeFeather > 0 ||
      spec.cornerRadius > 0 ||
      spec.edgeNoise > 0 ||
      spec.opacity < 1
    ) {
      material.transparent = true;
      material.depthWrite = false;
      material.alphaMap = buildPadAlphaTexture(spec);
    }
    // Evita z-fighting quando o pad fica quase coplanar com o chão.
    material.polygonOffset = true;
    material.polygonOffsetFactor = -1;
    material.polygonOffsetUnits = -2;
  }

  // Aplicar textura albedo (map) quando definida. Usamos clone() para que cada
  // primitiva possa ter repeat/rotation UV independente sem afetar as irmãs
  // que partilham o mesmo URL (a imagem GPU é partilhada pelo cache).
  if (spec.textureUrl) {
    try {
      const baseTex = loadCompositionTexture(spec.textureUrl);
      const tex = baseTex.clone();
      tex.repeat.set(spec.textureRepeatX, spec.textureRepeatY);
      tex.rotation = spec.textureRotation;
      tex.center.set(0.5, 0.5);
      tex.needsUpdate = true;
      material.map = tex;
    } catch {
      // Se a textura falhar a carregar (URL inválido), cai para cor flat.
    }
  }
  if (spec.normalMapUrl) {
    try {
      const baseTex = loadCompositionDataTexture(spec.normalMapUrl);
      const tex = baseTex.clone();
      tex.repeat.set(spec.textureRepeatX, spec.textureRepeatY);
      tex.rotation = spec.textureRotation;
      tex.center.set(0.5, 0.5);
      tex.needsUpdate = true;
      material.normalMap = tex;
    } catch {
      // ignore
    }
  }
  if (spec.roughnessMapUrl) {
    try {
      const baseTex = loadCompositionDataTexture(spec.roughnessMapUrl);
      const tex = baseTex.clone();
      tex.repeat.set(spec.textureRepeatX, spec.textureRepeatY);
      tex.rotation = spec.textureRotation;
      tex.center.set(0.5, 0.5);
      tex.needsUpdate = true;
      material.roughnessMap = tex;
    } catch {
      // ignore
    }
  }

  const mesh = new THREE.Mesh(primitiveGeometry(spec), material);
  // Pad é decal rente ao chão: projetar sombra própria só gera acne/escurecão.
  mesh.castShadow = spec.kind !== 'pad';
  mesh.receiveShadow = true;
  mesh.position.set(spec.posX, spec.posY, spec.posZ);
  mesh.rotation.set(spec.rotX, spec.rotY, spec.rotZ);
  return mesh;
}

// Plane collider is a thin slab (Rapier has no infinite plane primitive).
const PLANE_COLLIDER_HALF_THICKNESS = 0.02;

// Descriptor is in the body's local space (body origin = entity origin), with
// size/position scaled by the entity transform.
export function buildPrimitiveColliderDesc(
  spec: PrimitiveSpec,
  scaleX: number,
  scaleY: number,
  scaleZ: number
): RAPIER.ColliderDesc {
  let desc: RAPIER.ColliderDesc;
  switch (spec.kind) {
    case 'box':
      desc = RAPIER.ColliderDesc.cuboid(
        (spec.sizeX * scaleX) / 2,
        (spec.sizeY * scaleY) / 2,
        (spec.sizeZ * scaleZ) / 2
      );
      break;
    case 'sphere': {
      const radius =
        Math.max(spec.sizeX, 1e-4) * Math.max(scaleX, scaleY, scaleZ);
      desc = RAPIER.ColliderDesc.ball(radius);
      break;
    }
    case 'cylinder': {
      const radiusTop = Math.max(spec.sizeX, 1e-4) * scaleX;
      const radiusBottom = Math.max(spec.sizeY, 1e-4) * scaleX;
      const radius = (radiusTop + radiusBottom) / 2;
      const height = Math.max(spec.sizeZ, 1e-4) * scaleY;
      desc = RAPIER.ColliderDesc.cylinder(height / 2, radius);
      break;
    }
    case 'plane':
      desc = RAPIER.ColliderDesc.cuboid(
        (Math.max(spec.sizeX, 1e-4) * scaleX) / 2,
        PLANE_COLLIDER_HALF_THICKNESS,
        (Math.max(spec.sizeY, 1e-4) * scaleZ) / 2
      );
      break;
    case 'pad':
      // Decal deitado no XZ: laje fina largura X × profundidade Z.
      desc = RAPIER.ColliderDesc.cuboid(
        (Math.max(spec.sizeX, 1e-4) * scaleX) / 2,
        PLANE_COLLIDER_HALF_THICKNESS,
        (Math.max(spec.sizeZ, 1e-4) * scaleZ) / 2
      );
      break;
    default:
      desc = RAPIER.ColliderDesc.cuboid(0.5, 0.5, 0.5);
  }

  desc.setFriction(0.6);
  desc.setRestitution(0);
  desc.setTranslation(
    spec.posX * scaleX,
    spec.posY * scaleY,
    spec.posZ * scaleZ
  );

  if (spec.rotX !== 0 || spec.rotY !== 0 || spec.rotZ !== 0) {
    const quat = eulerToQuat(spec.rotX, spec.rotY, spec.rotZ);
    desc.setRotation(quat);
  }

  return desc;
}

function eulerToQuat(x: number, y: number, z: number): RAPIER.Quaternion {
  const cy = Math.cos(y * 0.5);
  const sy = Math.sin(y * 0.5);
  const cp = Math.cos(z * 0.5);
  const sp = Math.sin(z * 0.5);
  const cr = Math.cos(x * 0.5);
  const sr = Math.sin(x * 0.5);
  return new RAPIER.Quaternion(
    sr * cp * cy - cr * sp * sy,
    cr * sp * cy + sr * cp * sy,
    cr * cp * sy - sr * sp * cy,
    cr * cp * cy + sr * sp * sy
  );
}

export function parseFloatAttr(
  value: XMLValue | undefined,
  fallback: number
): number {
  return toFloat(value, fallback);
}
