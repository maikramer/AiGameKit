import {
  fetchJsonResilient,
  isPermanentFetchError,
} from '../../core/utils/resilient-net';
import { logger } from '../../core/utils/logger';

/**
 * Manifest de pré-cálculo do GameAssets (`gameassets_handoff.json`).
 *
 * Cada row carrega um bloco ``precompute`` com o colisor primitivo ideal
 * (cápsula do tronco / cilindro da pedra), o AABB mundo e o hint de
 * coletável. A engine usa isto para montar colisores baratos sem baixar
 * ``*_collision.glb`` e para semear o AABB (``ground-align``) sem
 * ``Box3.setFromObject``.
 *
 * Falhas transitórias (rede, 5xx, timeout) não são memorizadas: o manifest
 * volta a `idle` e a próxima chamada tenta de novo após um backoff
 * escalonado — um 502 no boot não desliga o precompute da sessão inteira.
 * Só resultados definitivos ficam cacheados: sucesso (`loaded`) e
 * 404/manifest inválido (`absent`).
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
let transientRetryAtMs = 0;
let transientAttempts = 0;

const RETRY_BASE_MS = 1_000;
const RETRY_MAX_MS = 30_000;
/** Ciclos transitórios antes de degradar para `absent` (fallback AABB-fit). */
const MAX_TRANSIENT_CYCLES = 6;

function transientRetryDelay(attempts: number): number {
  return Math.min(RETRY_MAX_MS, RETRY_BASE_MS * 2 ** attempts);
}

function finite(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value)
    ? value
    : undefined;
}

function finiteVec3(value: unknown): [number, number, number] | null {
  if (!Array.isArray(value) || value.length !== 3) return null;
  const x = finite(value[0]);
  const y = finite(value[1]);
  const z = finite(value[2]);
  return x === undefined || y === undefined || z === undefined
    ? null
    : [x, y, z];
}

/**
 * Valida um bloco `precompute` — um collider com `radius: "big"` produziria
 * NaN silencioso no Rapier. Retorna null quando a row deve ser descartada.
 */
function sanitizePrecompute(raw: unknown): AssetPrecompute | null {
  if (!raw || typeof raw !== 'object') return null;
  const pre = raw as Record<string, unknown>;
  const collider = pre.collider;
  if (!collider || typeof collider !== 'object') return null;
  const spec = collider as Record<string, unknown>;
  if (spec.shape !== 'capsule' && spec.shape !== 'cylinder') return null;
  const radius = finite(spec.radius);
  const height = finite(spec.height);
  const baseY = finite(spec.base_y);
  if (radius === undefined || radius <= 0) return null;
  if (height === undefined || height <= 0) return null;

  const aabbMin = finiteVec3((pre.aabb as { min?: unknown } | undefined)?.min);
  const aabbMax = finiteVec3((pre.aabb as { max?: unknown } | undefined)?.max);
  const hasAabb =
    pre.aabb === undefined || (aabbMin !== null && aabbMax !== null);

  const hint = pre.collectible_hint as { kind?: unknown } | undefined;
  const hintKind =
    hint?.kind === 'wood' || hint?.kind === 'stone' ? hint.kind : null;

  if (!hasAabb) return null;

  return {
    ...(typeof pre.version === 'number' ? { version: pre.version } : {}),
    ...(typeof pre.asset_id === 'string' ? { asset_id: pre.asset_id } : {}),
    ...(typeof pre.category === 'string' ? { category: pre.category } : {}),
    ...(aabbMin && aabbMax ? { aabb: { min: aabbMin, max: aabbMax } } : {}),
    collider: {
      shape: spec.shape,
      radius,
      height,
      base_y: baseY ?? 0,
    },
    ...(typeof pre.source === 'string' ? { source: pre.source } : {}),
    ...(hint ? { collectible_hint: { kind: hintKind } } : {}),
  };
}

function indexManifest(payload: unknown): PrecomputeIndex | null {
  if (!payload || typeof payload !== 'object') return null;
  const rows = (payload as { rows?: unknown }).rows;
  if (!Array.isArray(rows)) return null;
  const byUrl = new Map<string, AssetPrecompute>();
  const byBasename = new Map<string, AssetPrecompute>();
  const byId = new Map<string, AssetPrecompute>();
  let dropped = 0;
  for (const raw of rows) {
    if (!raw || typeof raw !== 'object') {
      dropped++;
      continue;
    }
    const row = raw as {
      id?: unknown;
      public_id?: unknown;
      model?: { url?: unknown; lod?: unknown };
      precompute?: unknown;
    };
    const entry = sanitizePrecompute(row.precompute);
    if (!entry) {
      dropped++;
      continue;
    }
    const model = row.model;
    const urls = [
      model?.url,
      ...(Array.isArray(model?.lod) ? (model.lod as unknown[]) : []),
    ].filter((u): u is string => typeof u === 'string' && u.length > 0);
    for (const u of urls) {
      const trimmed = u.trim();
      byUrl.set(trimmed, entry);
      const base = trimmed.slice(trimmed.lastIndexOf('/') + 1);
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
  if (dropped > 0) {
    logger.warn(
      `[precompute] ${dropped} rows com precompute inválido foram descartadas`
    );
  }
  return { byUrl, byBasename, byId };
}

/**
 * Carrega o manifest uma única vez por resultado definitivo (cache inclui a
 * ausência — 404/parse inválido ficam como `absent`). Falhas transitórias
 * agendam uma nova tentativa com backoff; chamadas durante a espera recebem o
 * mesmo promise (`null`) sem disparar fetch.
 */
export function loadPrecomputeManifest(
  url = DEFAULT_PRECOMPUTE_MANIFEST_URL
): Promise<PrecomputeIndex | null> {
  const settled = manifestState === 'loaded' || manifestState === 'absent';
  const waiting =
    manifestState === 'loading' ||
    (manifestState === 'idle' && Date.now() < transientRetryAtMs);
  if (manifestPromise && (settled || waiting)) return manifestPromise;

  manifestState = 'loading';
  manifestPromise = fetchJsonResilient(url)
    .then((payload) => {
      const index = indexManifest(payload);
      if (!index) throw new Error('rows/precompute ausentes no manifest');
      loadedIndex = index;
      manifestState = 'loaded';
      transientAttempts = 0;
      return index;
    })
    .catch((err: unknown) => {
      loadedIndex = null;
      const msg = err instanceof Error ? err.message : String(err);
      if (isPermanentFetchError(err)) {
        manifestState = 'absent';
        logger.warn(
          `[precompute] manifest não disponível (${msg}) — colisores caem para AABB-fit`
        );
        return null;
      }
      manifestState = 'idle';
      if (transientAttempts >= MAX_TRANSIENT_CYCLES) {
        manifestState = 'absent';
        logger.warn(
          `[precompute] manifest irrecuperável após ${transientAttempts} tentativas (${msg}) — colisores caem para AABB-fit`
        );
        return null;
      }
      const delay = transientRetryDelay(transientAttempts);
      transientAttempts += 1;
      transientRetryAtMs = Date.now() + delay;
      logger.warn(
        `[precompute] manifest transitório (${msg}) — nova tentativa em ${Math.round(delay / 100) / 10}s`
      );
      return null;
    });
  return manifestPromise;
}

/** Estado síncrono do manifest para sistemas (sem esperar o fetch). */
export function getPrecomputeManifestState(): ManifestState {
  return manifestState;
}

/** Index já carregado (null enquanto `loading`/`idle`/`absent`). */
export function getPrecomputeIndexSync(): PrecomputeIndex | null {
  return loadedIndex;
}

/** Timestamp da próxima tentativa agendada após falha transitória (0 = n/a). */
export function getPrecomputeRetryAtMs(): number {
  return manifestState === 'idle' ? transientRetryAtMs : 0;
}

const SUFFIX_RE = /_(lod[012]|collision|split|stump|top)$/i;

/** Util para testes: repõe o estado global (cache, pacing e ausência). */
export function resetPrecomputeManifestForTests(): void {
  manifestState = 'idle';
  loadedIndex = null;
  manifestPromise = null;
  transientRetryAtMs = 0;
  transientAttempts = 0;
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
