// Plaza campfire: [H] opens rest + Nota travel. Key H (hearth) so [F] on the
// nearby notice board never double-fires (the two prompts overlap in the gap)
// and the engine debug overlay keeps its [G] GPU-stats binding.
import {
  Transform,
  isKeyDown,
  Health,
  healHealth,
  cancelAllStatuses,
  registerInteractionTarget,
  unregisterInteractionTarget,
  setInputMovementSuppressed,
  playSound,
} from 'vibegame';
import type { MonoBehaviourContext, State } from 'vibegame';
import { isGamePaused, setGameModal } from '../game/pause.ts';
import { findPlayer } from '../game/player-query.ts';
import { showToast } from '../../../shared/src/ui';
import { CAMPFIRE_COOLDOWN, CAMPFIRE_HEAL } from '../game/city-amenities.ts';
import { biomeLabel } from '../game/nota.ts';
import {
  travelDestinations,
  travelToStop,
  type TravelStop,
} from '../game/travel.ts';

const RANGE_SQ = 4.6 * 4.6;
const CLOSE_RANGE_SQ = 6 * 6;
const MODAL_ID = 'campfire';

const BUTTON_BASE =
  'display:block;width:100%;padding:10px 12px;margin:6px 0;box-sizing:border-box;' +
  'background:rgba(40,30,20,0.9);color:#e8d8b0;border:1px solid #5a4a30;' +
  'border-radius:4px;font:15px Georgia,serif;text-align:left;cursor:pointer;';
const BUTTON_FOCUS =
  'border:2px solid #ffd700;box-shadow:0 0 12px rgba(215,180,80,0.35);';
const BUTTON_DISABLED = 'opacity:0.45;cursor:not-allowed;';

let fireEid = 0;
let promptShown = false;
let panelOpen = false;
let panel: HTMLDivElement | null = null;
let listHost: HTMLDivElement | null = null;
let buttons: HTMLButtonElement[] = [];
let focusedIndex = 0;
let gPressed = false;
let navUpPressed = false;
let navDownPressed = false;
let enterPressed = false;
let closePressed = false;
let activeState: State | null = null;
let readyAt = 0;

function promptLabel(now: number): string {
  const wait = Math.ceil(readyAt - now);
  return wait > 0 ? `Fogueira (${wait}s)` : 'Fogueira';
}

function showPrompt(state: State): void {
  if (promptShown || !fireEid) return;
  registerInteractionTarget(state, fireEid, {
    label: promptLabel(state.time.elapsed),
    key: 'H',
  });
  promptShown = true;
}

function hidePrompt(state: State): void {
  if (!promptShown || !fireEid) return;
  unregisterInteractionTarget(state, fireEid);
  promptShown = false;
}

function restAtFire(state: State, player: number): void {
  const now = state.time.elapsed;
  if (now < readyAt) {
    showToast(`A brasa ainda aquece. Espera ${Math.ceil(readyAt - now)}s.`, {
      color: '#e8c090',
      borderColor: '#a06030',
      background: 'rgba(22,12,8,0.95)',
    });
    playSound('error');
    return;
  }
  const max = Health.max[player] ?? 0;
  const cur = Health.current[player] ?? 0;
  const missing = Math.max(0, max - cur);
  const heal = Math.min(CAMPFIRE_HEAL, missing);
  if (heal > 0) healHealth(player, heal);
  cancelAllStatuses(state, player);
  readyAt = now + CAMPFIRE_COOLDOWN;
  playSound('heal');
  showToast(
    heal > 0
      ? `O lume aquece os ossos. (+${heal} HP)`
      : 'O lume limpa o veneno e o cansaço.',
    {
      color: '#ffc070',
      borderColor: '#c06020',
      background: 'rgba(22,12,8,0.95)',
    }
  );
}

function styleButton(btn: HTMLButtonElement, focused: boolean): void {
  let css = BUTTON_BASE;
  if (btn.disabled) css += BUTTON_DISABLED;
  else if (focused) css += BUTTON_FOCUS;
  btn.style.cssText = css;
}

function applyFocus(): void {
  for (let i = 0; i < buttons.length; i++) {
    styleButton(buttons[i], i === focusedIndex);
  }
}

function addButton(
  label: string,
  disabled: boolean,
  onPick: () => void
): HTMLButtonElement {
  const btn = document.createElement('button');
  btn.disabled = disabled;
  btn.textContent = label;
  btn.style.whiteSpace = 'pre-line';
  btn.addEventListener('click', () => {
    if (btn.disabled) return;
    onPick();
  });
  btn.addEventListener('mouseenter', () => {
    const idx = buttons.indexOf(btn);
    if (idx >= 0 && !btn.disabled) {
      focusedIndex = idx;
      applyFocus();
    }
  });
  buttons.push(btn);
  listHost?.appendChild(btn);
  return btn;
}

function rebuildList(state: State): void {
  if (!listHost) return;
  listHost.replaceChildren();
  buttons = [];
  const player = findPlayer(state);
  const now = state.time.elapsed;
  const onCooldown = now < readyAt;
  const wait = Math.ceil(readyAt - now);
  const restLabel = onCooldown
    ? `Descansar — brasa fria (${wait}s)`
    : 'Descansar — curar feridas';
  addButton(restLabel, onCooldown, () => {
    if (!player) return;
    restAtFire(state, player);
    closePanel();
  });

  const dests: TravelStop[] = travelDestinations(state);
  if (dests.length === 0) {
    const hint = document.createElement('div');
    hint.textContent =
      'Anota marcos na Nota ([F] no mundo) para viajar até eles.';
    hint.style.cssText =
      'font-size:13px;color:#b8a888;margin:10px 0 4px;font-style:italic;';
    listHost.appendChild(hint);
  } else {
    const head = document.createElement('div');
    head.textContent = 'Caminhos da Nota';
    head.style.cssText =
      'margin:12px 0 4px;font-size:12px;color:#c8a04a;letter-spacing:0.6px;';
    listHost.appendChild(head);
    for (const stop of dests) {
      addButton(`${stop.label}\n${biomeLabel(stop.biome)}`, false, () => {
        if (!player) return;
        closePanel();
        travelToStop(state, player, stop);
      });
    }
  }

  focusedIndex = Math.max(
    0,
    buttons.findIndex((b) => !b.disabled)
  );
  if (focusedIndex < 0) focusedIndex = 0;
  applyFocus();
}

function createPanel(): void {
  const root = document.createElement('div');
  root.id = 'campfire-panel';
  root.style.cssText =
    'position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);' +
    'width:420px;max-height:82vh;overflow-y:auto;box-sizing:border-box;' +
    'background:rgba(20,15,10,0.96);border:2px solid #c8a04a;border-radius:8px;' +
    'padding:18px 20px;z-index:1000;font-family:Georgia,serif;color:#e8d8b0;' +
    'box-shadow:0 0 40px rgba(0,0,0,0.85);display:none;';

  const header = document.createElement('div');
  header.style.cssText =
    'display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;';
  const title = document.createElement('div');
  title.textContent = 'Fogueira da praça';
  title.style.cssText =
    'font-size:20px;font-weight:bold;color:#c8a04a;letter-spacing:1px;';
  const closeBtn = document.createElement('button');
  closeBtn.textContent = '\u2715';
  closeBtn.style.cssText =
    'background:none;border:none;color:#c8a04a;font-size:18px;cursor:pointer;padding:0 4px;';
  closeBtn.addEventListener('click', () => closePanel());
  header.appendChild(title);
  header.appendChild(closeBtn);
  root.appendChild(header);

  const blurb = document.createElement('div');
  blurb.textContent =
    'Descansa. Os marcos da Nota são caminhos de volta ao lume — e de volta ao mundo.';
  blurb.style.cssText =
    'font-size:13px;color:#b8a888;margin-bottom:10px;font-style:italic;';
  root.appendChild(blurb);

  listHost = document.createElement('div');
  root.appendChild(listHost);

  const hint = document.createElement('div');
  hint.textContent = 'W/S navegar · Enter escolher · H/ESC fechar';
  hint.style.cssText =
    'margin-top:12px;padding-top:10px;border-top:1px solid rgba(200,160,74,0.3);' +
    'font-size:12px;color:#8a7a5a;text-align:center;';
  root.appendChild(hint);

  document.body.appendChild(root);
  panel = root;
}

function openPanel(state: State): void {
  if (panelOpen) return;
  panelOpen = true;
  setGameModal(MODAL_ID, true);
  setInputMovementSuppressed(true);
  hidePrompt(state);
  if (!panel) createPanel();
  rebuildList(state);
  if (panel) panel.style.display = 'block';
  playSound('shop-open');
  closePressed = true;
  gPressed = true;
}

function closePanel(): void {
  if (!panelOpen) return;
  panelOpen = false;
  setGameModal(MODAL_ID, false);
  setInputMovementSuppressed(false);
  if (activeState) showPrompt(activeState);
  if (panel) panel.style.display = 'none';
}

function navigate(direction: number): void {
  const n = buttons.length;
  if (n === 0) return;
  let idx = focusedIndex;
  for (let step = 0; step < n; step++) {
    idx = (idx + direction + n) % n;
    if (!buttons[idx].disabled) {
      focusedIndex = idx;
      applyFocus();
      return;
    }
  }
}

function handleKeys(): void {
  const up = isKeyDown('KeyW') || isKeyDown('ArrowUp');
  if (up && !navUpPressed) navigate(-1);
  navUpPressed = up;

  const down = isKeyDown('KeyS') || isKeyDown('ArrowDown');
  if (down && !navDownPressed) navigate(1);
  navDownPressed = down;

  const confirm = isKeyDown('KeyJ') || isKeyDown('Enter');
  if (confirm && !enterPressed) {
    const btn = buttons[focusedIndex];
    if (btn && !btn.disabled) btn.click();
  }
  enterPressed = confirm;

  const close = isKeyDown('Escape') || isKeyDown('KeyL') || isKeyDown('KeyH');
  if (close && !closePressed) closePanel();
  closePressed = close;
}

export function start(ctx: MonoBehaviourContext): void {
  findPlayer(ctx.state);
  fireEid = ctx.entity;
  activeState = ctx.state;
  showPrompt(ctx.state);
}

export function onDestroy(ctx: MonoBehaviourContext): void {
  closePanel();
  hidePrompt(ctx.state);
  fireEid = 0;
  activeState = null;
}

export function update(ctx: MonoBehaviourContext): void {
  activeState = ctx.state;
  if (isGamePaused() && !panelOpen) return;

  const player = findPlayer(ctx.state);
  const eid = ctx.entity;
  const dx = player ? Transform.posX[player] - Transform.posX[eid] : 0;
  const dz = player ? Transform.posZ[player] - Transform.posZ[eid] : 0;
  const distSq = dx * dx + dz * dz;

  if (panelOpen) {
    handleKeys();
    if (distSq > CLOSE_RANGE_SQ) closePanel();
    return;
  }

  if (player && distSq < RANGE_SQ) {
    showPrompt(ctx.state);
    if (promptShown) {
      registerInteractionTarget(ctx.state, eid, {
        label: promptLabel(ctx.state.time.elapsed),
        key: 'H',
      });
    }
  } else if (promptShown) {
    hidePrompt(ctx.state);
  }

  const g = isKeyDown('KeyH');
  if (g && !gPressed && player && distSq < RANGE_SQ) {
    openPanel(ctx.state);
  }
  gPressed = g;
}
