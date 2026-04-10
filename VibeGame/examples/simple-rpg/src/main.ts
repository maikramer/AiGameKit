import type { System, State } from 'vibegame';
import {
  AudioEmitter,
  configure,
  playAudioEmitter,
  registerEntityScripts,
  resumeAudioContextIfSuspended,
  run,
  withPlugin,
  withSystem,
  SaveLoadPlugin,
  I18nPlugin,
  saveToLocalStorage,
  loadFromLocalStorage,
  loadDictionary,
  setLocale,
  getLocale,
  t,
  isKeyDown,
} from 'vibegame';

const SAVE_KEY = 'simple-rpg-save';
const PROGRESS_KEY = 'simple-rpg-progress';

/** Posições XZ dos cristais (alinhadas ao `<place at="x z">` no index.html). */
const CRYSTAL_SITES = [
  { x: -4, z: 6 },
  { x: 10, z: -5 },
  { x: -10, z: -14 },
] as const;

const COLLECT_RADIUS = 2.85;
const GOLD_PER_CRYSTAL = 15;

const dictEN: Record<string, string> = {
  'hud.title': 'Crystal Vale',
  'hud.mission': 'Gather all energy crystals.',
  'hud.crystals': 'Crystals: {count} / {total}',
  'hud.gold': 'Gold: {gold}',
  'hud.time': 'Time: {time}',
  'hud.locale': 'Language: {lang}  [L] switch',
  'hud.saved': 'Game saved!',
  'hud.loaded': 'Save restored.',
  'hud.no-save': 'No save found.',
  'hud.collect': 'Crystal absorbed!',
  'hud.victory': 'All crystals found — the vale is safe!',
  'hud.controls':
    '[WASD] move  [Space] jump  [Mouse] look  [Q] save  [E] load  [L] EN/PT',
};

const dictPT: Record<string, string> = {
  'hud.title': 'Vale do Cristal',
  'hud.mission': 'Reúne todos os cristais de energia.',
  'hud.crystals': 'Cristais: {count} / {total}',
  'hud.gold': 'Ouro: {gold}',
  'hud.time': 'Tempo: {time}',
  'hud.locale': 'Idioma: {lang}  [L] trocar',
  'hud.saved': 'Jogo gravado!',
  'hud.loaded': 'Progresso restaurado.',
  'hud.no-save': 'Nenhuma gravação encontrada.',
  'hud.collect': 'Cristal absorvido!',
  'hud.victory': 'Todos os cristais — o vale está em paz!',
  'hud.controls':
    '[WASD] mover  [Espaço] saltar  [Rato] câmara  [Q] gravar  [E] carregar  [L] EN/PT',
};

type Progress = {
  crystals: boolean[];
  victoryAnnounced: boolean;
};

let overlayMissionEl: HTMLDivElement | null = null;
let overlayStatsEl: HTMLDivElement | null = null;
let overlayControlsEl: HTMLDivElement | null = null;

let eidSfxJump = -1;
let eidSfxSave = -1;
let eidSfxLoad = -1;

let saveDebounce = false;
let loadDebounce = false;
let localeDebounce = false;
let spaceWasDown = false;

let playTimeSec = 0;
let statusFlashUntil = 0;
let statusFlashKey = '';

const progress: Progress = {
  crystals: CRYSTAL_SITES.map(() => false),
  victoryAnnounced: false,
};

function formatTime(sec: number): string {
  const s = Math.floor(sec % 60);
  const m = Math.floor(sec / 60) % 60;
  const h = Math.floor(sec / 3600);
  if (h > 0)
    return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function crystalCount(): number {
  return progress.crystals.filter(Boolean).length;
}

function goldAmount(): number {
  return crystalCount() * GOLD_PER_CRYSTAL;
}

function loadProgress(): void {
  if (typeof localStorage === 'undefined') return;
  const raw = localStorage.getItem(PROGRESS_KEY);
  if (!raw) return;
  try {
    const data = JSON.parse(raw) as Progress;
    if (
      !Array.isArray(data.crystals) ||
      data.crystals.length !== CRYSTAL_SITES.length
    ) {
      return;
    }
    progress.crystals = data.crystals.map((v) => Boolean(v));
    progress.victoryAnnounced = Boolean(data.victoryAnnounced);
  } catch {
    /* ignore */
  }
}

function saveProgress(): void {
  if (typeof localStorage === 'undefined') return;
  localStorage.setItem(
    PROGRESS_KEY,
    JSON.stringify({
      crystals: progress.crystals,
      victoryAnnounced: progress.victoryAnnounced,
    })
  );
}

function pushFlash(state: State, key: string, seconds = 2.2): void {
  statusFlashKey = key;
  statusFlashUntil = state.time.elapsed + seconds;
}

function refreshHud(state: State): void {
  const total = CRYSTAL_SITES.length;
  const count = crystalCount();
  if (overlayMissionEl) {
    overlayMissionEl.innerHTML =
      `<strong style="font-size:15px;letter-spacing:0.4px">${t(state, 'hud.title')}</strong><br/>` +
      `<span style="opacity:0.88;font-size:13px">${t(state, 'hud.mission')}</span>`;
  }
  if (overlayStatsEl) {
    overlayStatsEl.innerHTML =
      `${t(state, 'hud.crystals', { count: String(count), total: String(total) })}<br/>` +
      `${t(state, 'hud.gold', { gold: String(goldAmount()) })} · ${t(
        state,
        'hud.time',
        {
          time: formatTime(playTimeSec),
        }
      )}<br/>` +
      `${t(state, 'hud.locale', { lang: getLocale(state) === 'pt' ? 'PT' : 'EN' })}`;
  }
}

const GameplayHudSystem: System = {
  group: 'simulation',
  update(state: State) {
    const dt = state.time.deltaTime;
    playTimeSec += dt;

    const TransformCmp = state.getComponent('transform');
    const heroEid = state.getEntityByName('hero');
    if (
      TransformCmp &&
      heroEid !== null &&
      state.hasComponent(heroEid, TransformCmp)
    ) {
      const px = TransformCmp.posX[heroEid];
      const pz = TransformCmp.posZ[heroEid];
      for (let i = 0; i < CRYSTAL_SITES.length; i++) {
        if (progress.crystals[i]) continue;
        const { x, z } = CRYSTAL_SITES[i];
        const dx = px - x;
        const dz = pz - z;
        if (dx * dx + dz * dz < COLLECT_RADIUS * COLLECT_RADIUS) {
          progress.crystals[i] = true;
          saveProgress();
          if (eidSfxSave >= 0) playAudioEmitter(state, eidSfxSave);
          pushFlash(state, 'hud.collect', 2);
          if (
            crystalCount() === CRYSTAL_SITES.length &&
            !progress.victoryAnnounced
          ) {
            progress.victoryAnnounced = true;
            saveProgress();
            pushFlash(state, 'hud.victory', 4);
          }
        }
      }
    }

    if (statusFlashKey && state.time.elapsed >= statusFlashUntil) {
      statusFlashKey = '';
    }
    if (overlayMissionEl && statusFlashKey) {
      const extra = t(state, statusFlashKey);
      overlayMissionEl.innerHTML =
        `<strong style="font-size:15px;letter-spacing:0.4px">${t(state, 'hud.title')}</strong><br/>` +
        `<span style="opacity:0.95;font-size:13px;color:#c8e0ff">${extra}</span>`;
    } else if (overlayMissionEl) {
      overlayMissionEl.innerHTML =
        `<strong style="font-size:15px;letter-spacing:0.4px">${t(state, 'hud.title')}</strong><br/>` +
        `<span style="opacity:0.88;font-size:13px">${t(state, 'hud.mission')}</span>`;
    }
    refreshHud(state);

    const spaceDown = isKeyDown('Space');
    if (spaceDown && !spaceWasDown && eidSfxJump >= 0) {
      playAudioEmitter(state, eidSfxJump);
    }
    spaceWasDown = spaceDown;

    if (isKeyDown('KeyL') && !localeDebounce) {
      localeDebounce = true;
      const next = getLocale(state) === 'pt' ? 'en' : 'pt';
      setLocale(state, next);
      if (overlayControlsEl)
        overlayControlsEl.textContent = t(state, 'hud.controls');
      refreshHud(state);
    }
    if (!isKeyDown('KeyL')) localeDebounce = false;

    if (isKeyDown('KeyQ') && !saveDebounce) {
      saveDebounce = true;
      saveToLocalStorage(state, SAVE_KEY);
      saveProgress();
      if (eidSfxSave >= 0) playAudioEmitter(state, eidSfxSave);
      pushFlash(state, 'hud.saved', 2.5);
    }
    if (!isKeyDown('KeyQ')) saveDebounce = false;

    if (isKeyDown('KeyE') && !loadDebounce) {
      loadDebounce = true;
      const ok = loadFromLocalStorage(state, SAVE_KEY);
      loadProgress();
      if (ok && eidSfxLoad >= 0) playAudioEmitter(state, eidSfxLoad);
      pushFlash(state, ok ? 'hud.loaded' : 'hud.no-save', 2.5);
    }
    if (!isKeyDown('KeyE')) loadDebounce = false;
  },
};

function createOverlayHud(state: State): void {
  const wrap = document.createElement('div');
  wrap.style.cssText =
    'position:fixed;inset:0;pointer-events:none;z-index:1000;font-family:system-ui,Segoe UI,sans-serif;';

  const topLeft = document.createElement('div');
  topLeft.style.cssText =
    'position:absolute;top:20px;left:20px;max-width:min(420px,92vw);' +
    'display:flex;flex-direction:column;gap:10px;';

  overlayMissionEl = document.createElement('div');
  overlayMissionEl.style.cssText =
    'background:rgba(8,12,28,0.72);color:#e8eef8;padding:12px 18px;' +
    'border-radius:10px;font-size:13px;line-height:1.45;' +
    'border:1px solid rgba(90,120,200,0.22);backdrop-filter:blur(10px);' +
    'box-shadow:0 8px 32px rgba(0,0,0,0.25);';

  overlayStatsEl = document.createElement('div');
  overlayStatsEl.style.cssText =
    'background:rgba(8,12,28,0.55);color:#b8c8e8;padding:10px 16px;' +
    'border-radius:8px;font-size:12px;line-height:1.5;' +
    'border:1px solid rgba(90,120,200,0.15);backdrop-filter:blur(8px);';

  topLeft.appendChild(overlayMissionEl);
  topLeft.appendChild(overlayStatsEl);

  const bottom = document.createElement('div');
  bottom.style.cssText =
    'position:absolute;bottom:22px;left:50%;transform:translateX(-50%);';

  overlayControlsEl = document.createElement('div');
  overlayControlsEl.style.cssText =
    'background:rgba(8,12,28,0.5);color:#8a9ab8;padding:8px 16px;' +
    'border-radius:8px;font-size:11px;letter-spacing:0.2px;max-width:92vw;text-align:center;' +
    'border:1px solid rgba(90,120,200,0.12);';
  overlayControlsEl.textContent = t(state, 'hud.controls');

  bottom.appendChild(overlayControlsEl);
  wrap.appendChild(topLeft);
  wrap.appendChild(bottom);
  document.body.appendChild(wrap);

  refreshHud(state);
}

function resolveAudioEids(state: State): void {
  eidSfxJump = state.getEntityByName('sfx-jump') ?? -1;
  eidSfxSave = state.getEntityByName('sfx-save') ?? -1;
  eidSfxLoad = state.getEntityByName('sfx-load') ?? -1;
}

async function bootstrap(): Promise<void> {
  withPlugin(SaveLoadPlugin);
  withPlugin(I18nPlugin);
  withSystem(GameplayHudSystem);

  configure({ canvas: '#game-canvas' });
  const runtime = await run();
  const state = runtime.getState();

  registerEntityScripts(state, import.meta.glob('./scripts/*.ts'));

  resolveAudioEids(state);

  loadDictionary(state, 'en', dictEN);
  loadDictionary(state, 'pt', dictPT);

  const userLang = navigator.language.startsWith('pt') ? 'pt' : 'en';
  setLocale(state, userLang);

  loadProgress();
  createOverlayHud(state);

  const bgmEid = state.getEntityByName('bgm');
  if (bgmEid !== null && typeof document !== 'undefined') {
    const startBgm = () => {
      resumeAudioContextIfSuspended();
      if (state.exists(bgmEid) && state.hasComponent(bgmEid, AudioEmitter)) {
        AudioEmitter.playing[bgmEid] = 1;
      }
      document.removeEventListener('pointerdown', startBgm);
    };
    document.addEventListener('pointerdown', startBgm, { once: true });
  }
}

void bootstrap();
