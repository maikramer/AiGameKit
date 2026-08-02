import { logger } from '../../core/utils/logger';

/**
 * Manifest de pré-cálculo do GameAssets (`gameassets_handoff.json`).
 *
 * Cada row carrega um bloco ``precompute`` com o colisor primitivo ideal
 * (cápsula do tronco / cilindro da pedra), o AABB mundo e o hint de
 * coletável. A engine usa isto para montar colisores baratos sem baixar
 * ``*_collision.glb`` e para semear o AABB (``ground-align``) sem
 * ``Box3.setFromObject``.
 */

export interface PrecomputeColliderSpec {
  shape: 'capsule' | 'cylinder';
  radius: number;
  height: number;
  base_y: number;
}

export interface AssetPrecompute {
  version?: number;
  asset_id?: string;
  category?: string;
  /** AABB em espaço do root do GLB (contrato do `glb_extract_meta`). */
  aabb?: { min: [number, number, number]; max: [number, number, number] };
  collider: PrecomputeColliderSpec;
  /** stump | trunk-slice | aabb — origem da decisão no pipeline. */
  source?: string;
  collectible_hint?: { kind: 'wood' | 'stone' | null };
}

export interface PrecomputeIndex {
  /** Chave = URL exacta do GLB (ex.: `/assets/models/pine_dark_lod0.glb`). */
  byUrl: Map<string, AssetPrecompute>;
  /** Basename do ficheiro (ex.: `pine_dark_lod0.glb`) — tolera layouts de path. */
  byBasename: Map<string, AssetPrecompute>;
  /** public_id / id do manifest (ex.: `pine_dark`). */
  byId: Map<string, AssetPrecompute>;
}

export const DEFAULT_PRECOMPUTE_MANIFEST_URL =
  '/assets/gameassets_handoff.json';

type ManifestState = 'idle' | 'loading' | 'loaded' | 'absent';

let manifestState: ManifestState = 'idle';
let loadedIndex: PrecomputeIndex | null = null;
let manifestPromise: Promise<PrecomputeIndex | null> | null = null;

function indexManifest(payload: unknown): PrecomputeIndex | null {
  if (!payload || typeof payload !== 'object') return null;
  const rows = (payload as { rows?: unknown }).rows;
  if (!Array.isArray(rows)) return null;
  const byUrl = new Map<string, AssetPrecompute>();
  const byBasename = new Map<string, AssetPrecompute>();
  const byId = new Map<string, AssetPrecompute>();
  for (const raw of rows) {
    if (!raw || typeof raw !== 'object') continue;
    const row = raw as {
      id?: unknown;
      public_id?: unknown;
      model?: { url?: unknown; lod?: unknown };
      precompute?: unknown;
    };
    const pre = row.precompute;
    if (!pre || typeof pre !== 'object') continue;
    const entry = pre as AssetPrecompute;
    if (!entry.collider || typeof entry.collider !== 'object') continue;
    const model = row.model;
    const urls = [
      model?.url,
      ...(Array.isArray(model?.lod) ? (model.lod as unknown[]) : []),
    ].filter((u): u is string => typeof u === 'string' && u.length > 0);
    for (const u of urls) {
      byUrl.set(u.trim(), entry);
      const base = u.slice(u.lastIndexOf('/') + 1);
      if (base) byBasename.set(base, entry);
    }
    const id =
      typeof row.public_id === 'string'
        ? row.public_id
        : typeof row.id === 'string'
          ? row.id
          : '';
    if (id) byId.set(id, entry);
  }
  if (byUrl.size === 0 && byBasename.size === 0 && byId.size === 0) return null;
  return { byUrl, byBasename, byId };
}

/**
 * Carrega o manifest uma única vez (cache inclui a ausência — 404/parse
 * inválido ficam como `absent`). Resolve para o index quando disponível.
 */
export function loadPrecomputeManifest(
  url = DEFAULT_PRECOMPUTE_MANIFEST_URL
): Promise<PrecomputeIndex | null> {
  if (manifestPromise) return manifestPromise;
  manifestState = 'loading';
  manifestPromise = fetch(url)
    .then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json() as Promise<unknown>;
    })
    .then((payload) => {
      const index = indexManifest(payload);
      if (!index) throw new Error('rows/precompute ausentes no manifest');
      loadedIndex = index;
      manifestState = 'loaded';
      return index;
    })
    .catch((err: unknown) => {
      loadedIndex = null;
      manifestState = 'absent';
      const msg = err instanceof Error ? err.message : String(err);
      logger.warn(
        `[precompute] manifest não disponível (${msg}) — colisores caem para AABB-fit`
      );
      return null;
    });
  return manifestPromise;
}

/** Estado síncrono do manifest para sistemas (sem esperar o fetch). */
export function getPrecomputeManifestState(): ManifestState {
  return manifestState;
}

/** Index já carregado (null enquanto `loading` ou `absent`). */
export function getPrecomputeIndexSync(): PrecomputeIndex | null {
  return loadedIndex;
}

const SUFFIX_RE = /_(lod[012]|collision|split|stump|top)$/i;

/** Util para testes: repõe o estado global (cache incl. a ausência). */
export function resetPrecomputeManifestForTests(): void {
  manifestState = 'idle';
  loadedIndex = null;
  manifestPromise = null;
}

/**
 * Resolve o pré-cálculo para uma chave URL qualquer: match exacto → basename
 * → public_id (com `_lod0`/`_collision` etc. descascados do basename).
 */
export function resolvePrecompute(
  index: PrecomputeIndex | null,
  key: string
): AssetPrecompute | undefined {
  if (!index) return undefined;
  const trimmed = key.trim();
  if (!trimmed) return undefined;
  const exact = index.byUrl.get(trimmed);
  if (exact) return exact;
  const base = trimmed.slice(trimmed.lastIndexOf('/') + 1);
  if (base) {
    const byBase = index.byBasename.get(base);
    if (byBase) return byBase;
    const id = base.replace(/\.glb$/i, '').replace(SUFFIX_RE, '');
    return index.byId.get(id);
  }
  return undefined;
}
