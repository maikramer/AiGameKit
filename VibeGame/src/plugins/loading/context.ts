import { logger } from '../../core/utils/logger';
import { getLoadingProgress, isWorldReady, type State } from '../../core';
import {
  describeGltfAssetsPending,
  releaseStuckPendingEntities,
} from '../gltf-xml/ready-gate';
import { describeSpawnPending } from '../spawner/ready-gate';
import { describeTerrainPending } from '../terrain/ready-gate';

export interface LoadingScreenText {
  title: string;
  subtitle: string;
}

export type LoadingScreenLocale = 'en' | 'pt';

/** Minimum time the screen stays up so fast loads don't flash. */
const MIN_VISIBLE_MS = 350;
/** Fade-out duration before the overlay is removed from the DOM. */
const FADE_MS = 450;

interface LoadingUI {
  root: HTMLDivElement;
  bar: HTMLDivElement;
  status: HTMLDivElement;
  detail: HTMLDivElement;
  titleEl: HTMLDivElement;
  subtitleEl: HTMLDivElement;
  firstShown: number;
  done: boolean;
}

interface LoadingCopy {
  finishing: string;
  ready: string;
  terrainInit: string;
  terrainDecode: string;
  terrainCollision: string;
  terrain: string;
  spawn: (done: number, total: number) => string;
  assets: (done: number, total: number, remaining: number) => string;
  assetsKick: (n: number) => string;
  assetsGeneric: string;
  shaders: string;
  gates: (ready: number, total: number) => string;
  fields: (n: number) => string;
}

const COPY: Record<LoadingScreenLocale, LoadingCopy> = {
  en: {
    finishing: 'Finishing…',
    ready: 'Ready',
    terrainInit: 'Preparing terrain…',
    terrainDecode: 'Decoding heightmap…',
    terrainCollision: 'Building terrain collision…',
    terrain: 'Building terrain…',
    spawn: (done, total) =>
      total > 0
        ? `Placing world objects (${done}/${total})…`
        : 'Placing world objects…',
    assets: (done, total, remaining) =>
      `Loading models ${done}/${total} · ${remaining} remaining`,
    assetsKick: (n) =>
      n === 1 ? 'Starting 1 asset download…' : `Starting ${n} asset downloads…`,
    assetsGeneric: 'Loading models…',
    shaders: 'Warming shaders…',
    gates: (ready, total) => `Steps ${ready}/${total}`,
    fields: (n) => (n === 1 ? '1 terrain field' : `${n} terrain fields`),
  },
  pt: {
    finishing: 'A terminar…',
    ready: 'Pronto',
    terrainInit: 'A preparar o terreno…',
    terrainDecode: 'A descodificar o mapa de alturas…',
    terrainCollision: 'A construir colisão do terreno…',
    terrain: 'A construir o terreno…',
    spawn: (done, total) =>
      total > 0
        ? `A colocar objetos (${done}/${total})…`
        : 'A colocar objetos…',
    assets: (done, total, remaining) =>
      `A carregar modelos ${done}/${total} · ${remaining} restantes`,
    assetsKick: (n) =>
      n === 1
        ? 'A iniciar 1 descarga de asset…'
        : `A iniciar ${n} descargas de assets…`,
    assetsGeneric: 'A carregar modelos…',
    shaders: 'A aquecer shaders…',
    gates: (ready, total) => `Etapas ${ready}/${total}`,
    fields: (n) => (n === 1 ? '1 campo de terreno' : `${n} campos de terreno`),
  },
};

// Singleton: there is one loading screen per page. Kept at module scope (not
// per-State) so it can be mounted before any runtime/State exists — the whole
// point is to paint it as early as possible.
let text: LoadingScreenText = { title: 'Loading…', subtitle: '' };
let locale: LoadingScreenLocale = detectLocale();
let ui: LoadingUI | null = null;
// Pending fade-out timer (see updateLoadingScreen). Tracked so it can be
// cancelled if the runtime is torn down during the fade window; otherwise the
// callback fires on a detached node. runtime.destroy() should call
// cancelLoadingFade().
let fadeTimer: ReturnType<typeof setTimeout> | null = null;

function detectLocale(): LoadingScreenLocale {
  if (typeof navigator === 'undefined') return 'en';
  try {
    return navigator.language.toLowerCase().startsWith('pt') ? 'pt' : 'en';
  } catch {
    return 'en';
  }
}

function copy(): LoadingCopy {
  return COPY[locale] ?? COPY.en;
}

function applyText(): void {
  if (!ui) return;
  ui.titleEl.textContent = text.title;
  ui.subtitleEl.textContent = text.subtitle;
  ui.subtitleEl.style.display = text.subtitle ? '' : 'none';
}

/** Update the loading screen copy; applies live if already mounted. */
export function setLoadingScreenText(t: Partial<LoadingScreenText>): void {
  text = { ...text, ...t };
  applyText();
}

export function getLoadingScreenText(): LoadingScreenText {
  return text;
}

/** Force EN/PT status strings (defaults to navigator.language). */
export function setLoadingScreenLocale(lang: LoadingScreenLocale): void {
  locale = lang;
}

export function getLoadingScreenLocale(): LoadingScreenLocale {
  return locale;
}

/**
 * Create and show the loading overlay immediately (idempotent). Call this as
 * early as possible — e.g. the first line of your bootstrap, before building
 * the runtime — so it paints before the scene parse and asset loads begin.
 */
export function mountLoadingScreen(opts?: Partial<LoadingScreenText>): void {
  if (opts) text = { ...text, ...opts };
  if (typeof document === 'undefined' || !document.body) return;
  if (ui) {
    applyText();
    return;
  }
  ui = createUI();
  applyText();
}

function createUI(): LoadingUI {
  const root = document.createElement('div');
  root.id = 'vibegame-loading';
  root.style.cssText =
    'position:fixed;inset:0;z-index:2147483647;display:flex;flex-direction:column;' +
    'align-items:center;justify-content:center;gap:18px;' +
    'background:radial-gradient(ellipse at 50% 35%,#16213a 0%,#0a0e1a 70%,#05070d 100%);' +
    'font-family:system-ui,Segoe UI,sans-serif;color:#e8eef8;' +
    `opacity:1;transition:opacity ${FADE_MS}ms ease-out;pointer-events:auto;`;

  const titleEl = document.createElement('div');
  titleEl.style.cssText =
    'font-size:34px;font-weight:800;letter-spacing:1.5px;' +
    'text-shadow:0 2px 18px rgba(0,0,0,0.5);';

  const subtitleEl = document.createElement('div');
  subtitleEl.style.cssText =
    'font-size:14px;color:#9fb2d6;letter-spacing:0.3px;margin-top:-6px;';

  const barOuter = document.createElement('div');
  barOuter.style.cssText =
    'width:min(420px,78vw);height:8px;border-radius:6px;overflow:hidden;' +
    'background:rgba(120,150,210,0.18);border:1px solid rgba(120,150,210,0.18);';

  const bar = document.createElement('div');
  bar.style.cssText =
    'width:0%;height:100%;border-radius:6px;' +
    'background:linear-gradient(90deg,#4a7bd6,#7fd0ff);' +
    'transition:width 0.2s ease-out;';
  barOuter.appendChild(bar);

  const status = document.createElement('div');
  status.style.cssText =
    'font-size:13px;color:#a8b8d4;letter-spacing:0.3px;min-height:18px;' +
    'text-align:center;max-width:min(520px,88vw);';

  const detail = document.createElement('div');
  detail.style.cssText =
    'font-size:11px;color:#6e7f9c;letter-spacing:0.2px;min-height:14px;' +
    'text-align:center;max-width:min(520px,88vw);opacity:0.95;';

  root.appendChild(titleEl);
  root.appendChild(subtitleEl);
  root.appendChild(barOuter);
  root.appendChild(status);
  root.appendChild(detail);
  document.body.appendChild(root);

  return {
    root,
    bar,
    status,
    detail,
    titleEl,
    subtitleEl,
    firstShown: 0,
    done: false,
  };
}

/**
 * Stall warn only when the critical set is frozen. Normal boot drains dozens of
 * GLBs over 10–30s — logging every 5s while counts fall is noise, not a hang.
 */
let assetsStallFingerprint = '';
let assetsStuckSince = 0;
let lastAssetsStallLog = 0;
const ASSETS_STALL_LOG_MS = 5_000;
/** Same frozen hold with zero in-flight loads for this long force-releases
 *  the assets gate (a wedged load path must not keep the game from booting). */
const ASSETS_STALL_FORCE_RELEASE_MS = 60_000;

function basenameUrl(url: string): string {
  const clean = url.split('?')[0] ?? url;
  const slash = Math.max(clean.lastIndexOf('/'), clean.lastIndexOf('\\'));
  return slash >= 0 ? clean.slice(slash + 1) : clean;
}

function formatAssetNames(urls: string[], limit = 3): string {
  if (urls.length === 0) return '';
  const names = urls.slice(0, limit).map(basenameUrl);
  const extra = urls.length - names.length;
  return extra > 0 ? `${names.join(', ')} +${extra}` : names.join(', ');
}

function humanizePending(
  state: State,
  pending: string[]
): { status: string; detail: string } {
  const c = copy();
  if (pending.length === 0) return { status: c.finishing, detail: '' };

  // Prefer the most specific / slow phase first so the player sees useful work.
  const order = ['assets', 'terrain', 'spawn', 'shaders'] as const;
  const primary =
    order.find((name) => pending.includes(name)) ?? pending[0] ?? 'assets';

  if (primary === 'assets') {
    const d = describeGltfAssetsPending(state);
    if (d.total > 0 && (d.remaining > 0 || d.done > 0)) {
      return {
        status: c.assets(d.done, d.total, d.remaining),
        detail: formatAssetNames(d.criticalUrls),
      };
    }
    if (d.pendingEntities > 0) {
      return { status: c.assetsKick(d.pendingEntities), detail: '' };
    }
    return { status: c.assetsGeneric, detail: '' };
  }

  if (primary === 'terrain') {
    const t = describeTerrainPending(state);
    const status =
      t.phase === 'decode'
        ? c.terrainDecode
        : t.phase === 'collision'
          ? c.terrainCollision
          : t.phase === 'init'
            ? c.terrainInit
            : c.terrain;
    return {
      status,
      detail: t.fields > 1 ? c.fields(t.fields) : '',
    };
  }

  if (primary === 'spawn') {
    const s = describeSpawnPending(state);
    return { status: c.spawn(s.done, s.total), detail: '' };
  }

  if (primary === 'shaders') {
    return { status: c.shaders, detail: '' };
  }

  return { status: `${primary}…`, detail: '' };
}

/** Gate bar with asset sub-progress so the fill moves during long GLB boots. */
function computeProgressPercent(
  state: State,
  progress: { ready: number; total: number; pending: string[] }
): number {
  if (progress.total === 0) return 100;
  const pending = new Set(progress.pending);
  // Each gate is 1 unit. Pending assets/spawn contribute a done/total fraction.
  const gateWeight = 1;
  const readyUnits = progress.ready * gateWeight;
  let pendingUnits = 0;
  for (const name of pending) {
    if (name === 'assets') {
      const d = describeGltfAssetsPending(state);
      pendingUnits +=
        d.total > 0 ? gateWeight * Math.min(1, d.done / d.total) : 0;
    } else if (name === 'spawn') {
      const s = describeSpawnPending(state);
      pendingUnits +=
        s.total > 0 ? gateWeight * Math.min(1, s.done / s.total) : 0;
    }
    // terrain/shaders stay at 0 until they pass (no reliable sub-meter).
  }
  const score = readyUnits + pendingUnits;
  return Math.max(0, Math.min(100, Math.round((score / progress.total) * 100)));
}

function assetsStallKey(
  d: ReturnType<typeof describeGltfAssetsPending>
): string {
  // Sort so set membership (not insertion order) defines "same hold".
  const urls = d.criticalUrls.slice().sort().join(',');
  return `${d.critical}|${d.pendingEntities}|${urls}`;
}

function maybeLogAssetsStall(
  state: State,
  pending: string[],
  now: number
): void {
  if (!pending.includes('assets')) {
    assetsStallFingerprint = '';
    assetsStuckSince = 0;
    return;
  }
  const d = describeGltfAssetsPending(state);
  const fingerprint = assetsStallKey(d);
  if (fingerprint !== assetsStallFingerprint) {
    // Gate still draining — keep quiet; restart the stuck clock.
    assetsStallFingerprint = fingerprint;
    assetsStuckSince = now;
    return;
  }
  if (assetsStuckSince === 0) assetsStuckSince = now;

  // Safety valve: the exact same hold (same pending count, nothing critical)
  // with NOTHING in flight for a full minute means a load path wedged —
  // force-release the gate so the game boots; the stuck visuals just don't
  // render. Better a world missing a prop than a world that never opens.
  if (
    now - assetsStuckSince >= ASSETS_STALL_FORCE_RELEASE_MS &&
    d.active === 0 &&
    d.critical === 0 &&
    d.pendingEntities > 0
  ) {
    logger.warn(
      `[loading] assets gate wedged ${Math.round(
        (now - assetsStuckSince) / 1000
      )}s on ${d.pendingEntities} pending with nothing in flight — releasing the gate; affected visuals are skipped`
    );
    releaseStuckPendingEntities(state);
    assetsStallFingerprint = '';
    assetsStuckSince = 0;
    return;
  }

  if (now - assetsStuckSince < ASSETS_STALL_LOG_MS) return;
  if (now - lastAssetsStallLog < ASSETS_STALL_LOG_MS) return;
  lastAssetsStallLog = now;
  const crit =
    d.criticalUrls.length > 0
      ? ` criticalUrls=[${d.criticalUrls.slice(0, 8).join(', ')}]`
      : '';
  const samples =
    d.sampleUrls.length > 0 ? ` pendingKick=[${d.sampleUrls.join(', ')}]` : '';
  logger.warn(
    `[loading] assets gate stuck for ${Math.round(
      (now - assetsStuckSince) / 1000
    )}s — critical=${d.critical} active=${d.active} ` +
    `done=${d.done}/${d.total} pendingKick=${d.pendingEntities}${crit}${samples}`
  );
}

/** Per-frame driver, called by {@link LoadingScreenSystem}. */
export function updateLoadingScreen(state: State): void {
  if (typeof document === 'undefined') return;
  if (!ui) {
    mountLoadingScreen();
    if (!ui) return;
  }
  if (ui.done) return;

  const now =
    typeof performance !== 'undefined' ? performance.now() : Date.now();
  if (ui.firstShown === 0) ui.firstShown = now;

  // The `shaders` gate is driven by ShaderWarmupSystem (rendering plugin), not
  // from here: this driver stops the moment the overlay fades, so anything it
  // owned would be left half-done.
  const progress = getLoadingProgress(state);
  const ready = isWorldReady(state);
  const pct = ready ? 100 : computeProgressPercent(state, progress);
  const c = copy();
  ui.bar.style.width = `${pct}%`;
  if (!ready) maybeLogAssetsStall(state, progress.pending, now);

  if (ready) {
    ui.status.textContent = c.ready;
    ui.detail.textContent = c.gates(progress.ready, progress.total);
  } else {
    const line = humanizePending(state, progress.pending);
    ui.status.textContent = line.status;
    const gateHint =
      progress.total > 1 ? c.gates(progress.ready, progress.total) : '';
    ui.detail.textContent = [line.detail, gateHint].filter(Boolean).join(' · ');
  }

  if (ready && now - ui.firstShown >= MIN_VISIBLE_MS) {
    ui.done = true;
    const root = ui.root;
    root.style.opacity = '0';
    // Drop pointer capture as the fade starts: for the whole FADE_MS the
    // overlay is invisible but still on top, and it used to swallow the first
    // click — which in this engine is also the click that focuses the canvas
    // (keyboard routing) and unlocks the audio context.
    root.style.pointerEvents = 'none';
    fadeTimer = setTimeout(() => {
      fadeTimer = null;
      root.remove();
    }, FADE_MS);
  }
}

/**
 * Cancel any pending loading-screen fade-out and remove the overlay.
 * Call from runtime teardown (e.g. ``runtime.destroy()``) so the deferred
 * ``setTimeout`` does not fire on a detached DOM node.
 */
export function cancelLoadingFade(): void {
  if (fadeTimer !== null) {
    clearTimeout(fadeTimer);
    fadeTimer = null;
  }
  if (ui) {
    ui.root.remove();
    ui = null;
  }
}
