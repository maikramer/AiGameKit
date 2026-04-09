import type { System, State } from 'vibegame';
import {
  applyEquirectSkyEnvironment,
  configure,
  run,
  withPlugin,
  withSystem,
  SaveLoadPlugin,
  I18nPlugin,
  saveToLocalStorage,
  loadFromLocalStorage,
  loadDictionary,
  setLocale,
  t,
  isKeyDown,
} from 'vibegame';

const SAVE_KEY = 'simple-rpg-save';

const dictEN: Record<string, string> = {
  'hud.welcome': 'Welcome, adventurer!',
  'hud.saved': 'Game saved!',
  'hud.loaded': 'Save restored.',
  'hud.no-save': 'No save found.',
  'hud.crystals': 'Crystals: {count}',
  'hud.controls': '[Q] save  [E] load  [WASD] move  [Space] jump',
};

const dictPT: Record<string, string> = {
  'hud.welcome': 'Bem-vindo, aventureiro!',
  'hud.saved': 'Jogo gravado!',
  'hud.loaded': 'Progresso restaurado.',
  'hud.no-save': 'Nenhuma gravação encontrada.',
  'hud.crystals': 'Cristais: {count}',
  'hud.controls': '[Q] gravar  [E] carregar  [WASD] mover  [Espaço] saltar',
};

let overlayStatusEl: HTMLDivElement | null = null;
let overlayControlsEl: HTMLDivElement | null = null;
let crystalCount = 0;
let statusTimer = 0;
let saveDebounce = false;
let loadDebounce = false;

const GameplayHudSystem: System = {
  group: 'simulation',
  update(state: State) {
    const dt = state.time.deltaTime;

    if (statusTimer > 0) {
      statusTimer -= dt;
      if (statusTimer <= 0) {
        setStatus(t(state, 'hud.crystals', { count: String(crystalCount) }));
      }
    }

    if (isKeyDown('KeyQ') && !saveDebounce) {
      saveDebounce = true;
      saveToLocalStorage(state, SAVE_KEY);
      showFlash(state, 'hud.saved');
    }
    if (!isKeyDown('KeyQ')) saveDebounce = false;

    if (isKeyDown('KeyE') && !loadDebounce) {
      loadDebounce = true;
      const ok = loadFromLocalStorage(state, SAVE_KEY);
      showFlash(state, ok ? 'hud.loaded' : 'hud.no-save');
    }
    if (!isKeyDown('KeyE')) loadDebounce = false;
  },
};

function showFlash(state: State, key: string): void {
  setStatus(t(state, key));
  statusTimer = 2.5;
}

function setStatus(text: string): void {
  if (overlayStatusEl) overlayStatusEl.textContent = text;
}

function createOverlayHud(status: string, controls: string): void {
  const wrap = document.createElement('div');
  wrap.style.cssText =
    'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);' +
    'display:flex;flex-direction:column;align-items:center;gap:6px;' +
    'pointer-events:none;z-index:1000;font-family:monospace;';

  overlayStatusEl = document.createElement('div');
  overlayStatusEl.style.cssText =
    'background:rgba(10,10,30,0.8);color:#e0e8ff;padding:8px 20px;' +
    'border-radius:6px;font-size:15px;letter-spacing:0.5px;' +
    'border:1px solid rgba(100,140,255,0.25);backdrop-filter:blur(6px);';
  overlayStatusEl.textContent = status;

  overlayControlsEl = document.createElement('div');
  overlayControlsEl.style.cssText =
    'background:rgba(10,10,30,0.55);color:#8899bb;padding:5px 14px;' +
    'border-radius:4px;font-size:11px;letter-spacing:0.3px;';
  overlayControlsEl.textContent = controls;

  wrap.appendChild(overlayStatusEl);
  wrap.appendChild(overlayControlsEl);
  document.body.appendChild(wrap);
}

async function bootstrap(): Promise<void> {
  withPlugin(SaveLoadPlugin);
  withPlugin(I18nPlugin);
  withSystem(GameplayHudSystem);

  configure({ canvas: '#game-canvas' });
  const runtime = await run();
  const state = runtime.getState();

  loadDictionary(state, 'en', dictEN);
  loadDictionary(state, 'pt', dictPT);

  const userLang = navigator.language.startsWith('pt') ? 'pt' : 'en';
  setLocale(state, userLang);

  const welcomeMsg = t(state, 'hud.welcome');
  const controlsMsg = t(state, 'hud.controls');
  createOverlayHud(welcomeMsg, controlsMsg);
  statusTimer = 4;

  try {
    await applyEquirectSkyEnvironment(state, '/assets/sky/sky.png');
  } catch {
    console.warn('[simple-rpg] Sky env map not loaded (optional).');
  }
}

void bootstrap();
