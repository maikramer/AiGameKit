import { getLoadingProgress, isWorldReady, type State } from '../../core';

export interface LoadingScreenText {
  title: string;
  subtitle: string;
}

/** Minimum time the screen stays up so fast loads don't flash. */
const MIN_VISIBLE_MS = 350;
/** Fade-out duration before the overlay is removed from the DOM. */
const FADE_MS = 450;

interface LoadingUI {
  root: HTMLDivElement;
  bar: HTMLDivElement;
  status: HTMLDivElement;
  titleEl: HTMLDivElement;
  subtitleEl: HTMLDivElement;
  firstShown: number;
  done: boolean;
}

// Singleton: there is one loading screen per page. Kept at module scope (not
// per-State) so it can be mounted before any runtime/State exists — the whole
// point is to paint it as early as possible.
let text: LoadingScreenText = { title: 'Loading…', subtitle: '' };
let ui: LoadingUI | null = null;

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
    'align-items:center;justify-content:center;gap:22px;' +
    'background:radial-gradient(ellipse at 50% 35%,#16213a 0%,#0a0e1a 70%,#05070d 100%);' +
    'font-family:system-ui,Segoe UI,sans-serif;color:#e8eef8;' +
    `opacity:1;transition:opacity ${FADE_MS}ms ease-out;pointer-events:auto;`;

  const titleEl = document.createElement('div');
  titleEl.style.cssText =
    'font-size:34px;font-weight:800;letter-spacing:1.5px;' +
    'text-shadow:0 2px 18px rgba(0,0,0,0.5);';

  const subtitleEl = document.createElement('div');
  subtitleEl.style.cssText =
    'font-size:14px;color:#9fb2d6;letter-spacing:0.3px;margin-top:-10px;';

  const barOuter = document.createElement('div');
  barOuter.style.cssText =
    'width:min(360px,72vw);height:8px;border-radius:6px;overflow:hidden;' +
    'background:rgba(120,150,210,0.18);border:1px solid rgba(120,150,210,0.18);';

  const bar = document.createElement('div');
  bar.style.cssText =
    'width:0%;height:100%;border-radius:6px;' +
    'background:linear-gradient(90deg,#4a7bd6,#7fd0ff);' +
    'transition:width 0.25s ease-out;';
  barOuter.appendChild(bar);

  const status = document.createElement('div');
  status.style.cssText =
    'font-size:12px;color:#8a9ab8;letter-spacing:0.4px;min-height:16px;';

  root.appendChild(titleEl);
  root.appendChild(subtitleEl);
  root.appendChild(barOuter);
  root.appendChild(status);
  document.body.appendChild(root);

  return { root, bar, status, titleEl, subtitleEl, firstShown: 0, done: false };
}

function humanizePending(pending: string[]): string {
  if (pending.length === 0) return 'Finishing…';
  const labels: Record<string, string> = {
    terrain: 'Building terrain',
    spawn: 'Placing world objects',
    assets: 'Loading assets',
  };
  return pending.map((p) => labels[p] ?? p).join(' · ') + '…';
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

  const progress = getLoadingProgress(state);
  const ready = isWorldReady(state);
  const pct =
    progress.total === 0
      ? 100
      : Math.round((progress.ready / progress.total) * 100);
  ui.bar.style.width = `${pct}%`;
  ui.status.textContent = ready ? 'Ready' : humanizePending(progress.pending);

  if (ready && now - ui.firstShown >= MIN_VISIBLE_MS) {
    ui.done = true;
    const root = ui.root;
    root.style.opacity = '0';
    setTimeout(() => root.remove(), FADE_MS);
  }
}
