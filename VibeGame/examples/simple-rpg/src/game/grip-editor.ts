// In-game held-item grip tuner (dev tool). Toggle with debug action `grip`
// (debug overlay, [?]) or Shift+G. While active, this module owns the held-item
// channel — AttackContextSystem steps aside — so nudges are not overwritten.
// Arrow keys/QE move, 123 pick a rotation axis, Tab switches pos/rot, N cycles
// the weapon, X exports every tuned grip as held-items.json-ready JSON
// (console + clipboard + a copy button on the panel).
import { getAnimator, isKeyDown, playSound, setPlayerHeldItem } from 'vibegame';
import type { HeldItemGrip, State } from 'vibegame';

/** Weapon ids in held-items.json — the editor cycles through these. */
export const GRIP_WEAPONS = [
  'sword',
  'axe',
  'spear',
  'chop',
  'mine',
  'bomb',
] as const;
type GripWeapon = (typeof GRIP_WEAPONS)[number];

/** Held GLB per weapon id — must stay in sync with HELD_MODEL in main.ts. */
const HELD_MODEL: Record<GripWeapon, string> = {
  sword: '/assets/meshes/props/sword_hero_lod0.glb',
  axe: '/assets/meshes/props/axe_lod0.glb',
  spear: '/assets/meshes/props/spear_lod0.glb',
  chop: '/assets/meshes/props/felling_axe_lod0.glb',
  mine: '/assets/meshes/props/pickaxe_lod0.glb',
  bomb: '/assets/meshes/props/bomb_lod0.glb',
};

interface GripValue {
  pos: [number, number, number];
  rot: [number, number, number];
  scale: number;
}

let active = false;
let mode: 'pos' | 'rot' = 'pos';
let weapon: GripWeapon = 'sword';
let rotAxis = 0; // 0=x 1=y 2=z
/** Edited grips (weapon id → value). Unedited ids are not exported. */
const edited = new Map<string, GripValue>();
/** Working copy of every known grip so cycling weapons keeps tuned values. */
const working = new Map<string, GripValue>();

// Keystate for edge detection.
const prev = new Set<string>();

let panelEl: HTMLDivElement | null = null;
let valuesEl: HTMLPreElement | null = null;
let jsonEl: HTMLPreElement | null = null;

export function isGripEditorActive(): boolean {
  return active;
}

/** Seed the working set from the loaded held-items.json grips. */
export function seedGripEditor(
  grips: Record<string, { pos: number[]; rot: number[]; scale?: number }>
): void {
  for (const id of Object.keys(grips)) {
    const g = grips[id]!;
    working.set(id, {
      pos: [g.pos[0] ?? 0, g.pos[1] ?? 0, g.pos[2] ?? 0],
      rot: [g.rot[0] ?? 0, g.rot[1] ?? 0, g.rot[2] ?? 0],
      scale: g.scale ?? 1,
    });
  }
}

function ensureWorking(id: GripWeapon): GripValue {
  let w = working.get(id);
  if (!w) {
    w = { pos: [0, 0, 0], rot: [0, 0, 0], scale: 1 };
    working.set(id, w);
  }
  return w;
}

function applyLive(): void {
  const w = ensureWorking(weapon);
  setPlayerHeldItem(HELD_MODEL[weapon], {
    x: w.pos[0],
    y: w.pos[1],
    z: w.pos[2],
    rx: w.rot[0],
    ry: w.rot[1],
    rz: w.rot[2],
    scale: w.scale,
  });
}

// ── Panel ────────────────────────────────────────────────────────────────────

function fmt3(v: readonly number[]): string {
  return v.map((n) => n.toFixed(3)).join(', ');
}

function renderPanel(): void {
  if (!panelEl) return;
  const w = ensureWorking(weapon);
  valuesEl!.textContent =
    `"${weapon}": {\n` +
    `  "pos": [${fmt3(w.pos)}],\n` +
    `  "rot": [${fmt3(w.rot)}],\n` +
    `  "scale": ${w.scale}\n` +
    `}`;
  panelEl.dataset.mode = mode;
  panelEl.dataset.axis = String(rotAxis);
  jsonEl!.textContent = exportJson();
}

function buildPanel(): void {
  if (panelEl || typeof document === 'undefined') return;
  const layer =
    document.querySelector('.vibe-hud-screen-layer') ?? document.body;
  panelEl = document.createElement('div');
  panelEl.style.cssText = [
    'position:absolute',
    'top:14px',
    'right:14px',
    'z-index:60',
    'width:330px',
    'padding:12px 14px',
    'border-radius:12px',
    'background:rgba(8,11,20,0.88)',
    'border:1px solid rgba(255,255,255,0.18)',
    'color:#e8ecf4',
    'font:12px/1.5 ui-monospace,Menlo,Consolas,monospace',
    'pointer-events:auto',
    'white-space:pre-wrap',
  ].join(';');

  const title = document.createElement('div');
  title.style.cssText =
    'font:700 13px system-ui,sans-serif;margin-bottom:8px;color:#ffd24a;';
  title.textContent = '🛠 Grip Editor (held items)';

  const hint = document.createElement('div');
  hint.style.cssText =
    'color:#9fb0c8;margin-bottom:8px;font:11px/1.5 system-ui,sans-serif;';
  hint.textContent =
    'Tab pos/rot · ←→↑↓ mexem · Q/E = Y (pos) · 1/2/3 eixo (rot) · ' +
    'Shift = fino · N arma · X exportar · Esc sair';

  valuesEl = document.createElement('pre');
  valuesEl.style.cssText = 'margin:0 0 10px;color:#6ef07a;';

  jsonEl = document.createElement('pre');
  jsonEl.style.cssText =
    'margin:0 0 10px;max-height:180px;overflow:auto;color:#8fb7ff;font-size:11px;';

  const copyBtn = document.createElement('button');
  copyBtn.type = 'button';
  copyBtn.textContent = '📋 Copiar JSON';
  copyBtn.style.cssText =
    'margin-right:6px;padding:5px 10px;border-radius:8px;border:1px solid rgba(255,255,255,0.25);' +
    'background:#2a3550;color:#fff;font:600 12px system-ui;cursor:pointer;';
  copyBtn.addEventListener('click', () => void copyExport());

  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.textContent = '✕ Fechar';
  closeBtn.style.cssText =
    'padding:5px 10px;border-radius:8px;border:1px solid rgba(255,255,255,0.25);' +
    'background:#502a2a;color:#fff;font:600 12px system-ui;cursor:pointer;';
  closeBtn.addEventListener('click', () => setGripEditorActive(false));

  panelEl.append(title, hint, valuesEl, jsonEl, copyBtn, closeBtn);
  layer.appendChild(panelEl);
  renderPanel();
}

function disposePanel(): void {
  panelEl?.remove();
  panelEl = null;
  valuesEl = null;
  jsonEl = null;
}

// ── Export ───────────────────────────────────────────────────────────────────

function exportJson(): string {
  const out: Record<string, GripValue> = {};
  // Export the full working set so the file stays complete after a paste.
  for (const id of GRIP_WEAPONS) {
    const w = working.get(id);
    if (w) out[id] = w;
  }
  return JSON.stringify(out, null, 2);
}

async function copyExport(): Promise<void> {
  const json = exportJson();
  // Surface it loud enough to grab from the console too.
  console.info('[grip-editor] held-items.json — copy me:\n' + json);
  try {
    await navigator.clipboard.writeText(json);
    playSound('notification');
  } catch {
    playSound('error');
  }
}

// ── Input ────────────────────────────────────────────────────────────────────

const POS_STEP = 0.05;
const POS_STEP_FINE = 0.01;
const ROT_STEP = 0.1;
const ROT_STEP_FINE = 0.02;

/**
 * Keys the editor owns while active. They are intercepted in the CAPTURE
 * phase and never reach the engine's (bubble-phase) key handler — otherwise
 * the arrows would also walk the hero, Q/E would open the pause menu / heal,
 * and 1/2/3 would drink potions mid-tuning.
 */
const EDITOR_KEYS: ReadonlySet<string> = new Set([
  'ArrowUp',
  'ArrowDown',
  'ArrowLeft',
  'ArrowRight',
  'Tab',
  'KeyQ',
  'KeyE',
  'KeyN',
  'KeyX',
  'KeyG',
  'Digit1',
  'Digit2',
  'Digit3',
  'Escape',
]);

/** Editor-local key state, fed by the capture-phase listener. */
const keys = new Set<string>();
let captureInstalled = false;

function onCaptureKey(down: boolean): (event: KeyboardEvent) => void {
  return (event: KeyboardEvent) => {
    if (!active) return;
    const code = event.code;
    const tracked =
      EDITOR_KEYS.has(code) || code === 'ShiftLeft' || code === 'ShiftRight';
    if (!tracked) return;
    // Swallow: the game must not react to editor keys while tuning.
    event.preventDefault();
    event.stopImmediatePropagation();
    if (down) keys.add(code);
    else keys.delete(code);
  };
}

const onKeyDownCapture = onCaptureKey(true);
const onKeyUpCapture = onCaptureKey(false);

function installCapture(): void {
  if (captureInstalled || typeof window === 'undefined') return;
  captureInstalled = true;
  window.addEventListener('keydown', onKeyDownCapture, true);
  window.addEventListener('keyup', onKeyUpCapture, true);
}

function removeCapture(): void {
  if (!captureInstalled) return;
  captureInstalled = false;
  window.removeEventListener('keydown', onKeyDownCapture, true);
  window.removeEventListener('keyup', onKeyUpCapture, true);
  keys.clear();
}

function held(code: string): boolean {
  return keys.has(code);
}

function edge(code: string, down: boolean): boolean {
  const was = prev.has(code);
  if (down && !was) {
    prev.add(code);
    return true;
  }
  if (!down) prev.delete(code);
  return false;
}

/** Tick the editor (call once/frame while active). */
function tickGripEditor(dt: number): void {
  const shift = held('ShiftLeft') || held('ShiftRight');
  const w = ensureWorking(weapon);

  // Shift+G / Esc while active: close (keys are captured, so read locally).
  if (
    edge('__toggleLocal', held('KeyG') && shift) ||
    edge('Escape', held('Escape'))
  ) {
    if (held('Escape')) prev.delete('Escape');
    setGripEditorActive(false);
    return;
  }

  if (edge('Tab', held('Tab'))) {
    mode = mode === 'pos' ? 'rot' : 'pos';
    renderPanel();
  }
  if (edge('KeyN', held('KeyN'))) {
    const i = GRIP_WEAPONS.indexOf(weapon);
    weapon = GRIP_WEAPONS[(i + 1) % GRIP_WEAPONS.length]!;
    applyLive();
    renderPanel();
  }
  if (edge('KeyX', held('KeyX'))) {
    void copyExport();
  }

  if (mode === 'pos') {
    const s = shift ? POS_STEP_FINE : POS_STEP;
    if (edge('ArrowLeft', held('ArrowLeft'))) w.pos[0] -= s;
    if (edge('ArrowRight', held('ArrowRight'))) w.pos[0] += s;
    if (edge('ArrowUp', held('ArrowUp'))) w.pos[2] -= s;
    if (edge('ArrowDown', held('ArrowDown'))) w.pos[2] += s;
    if (edge('KeyQ', held('KeyQ'))) w.pos[1] -= s;
    if (edge('KeyE', held('KeyE'))) w.pos[1] += s;
  } else {
    if (edge('Digit1', held('Digit1'))) rotAxis = 0;
    if (edge('Digit2', held('Digit2'))) rotAxis = 1;
    if (edge('Digit3', held('Digit3'))) rotAxis = 2;
    const s = shift ? ROT_STEP_FINE : ROT_STEP;
    if (edge('ArrowLeft', held('ArrowLeft'))) w.rot[rotAxis] -= s;
    if (edge('ArrowRight', held('ArrowRight'))) w.rot[rotAxis] += s;
    if (edge('ArrowUp', held('ArrowUp'))) w.rot[rotAxis] += s;
    if (edge('ArrowDown', held('ArrowDown'))) w.rot[rotAxis] -= s;
  }

  edited.set(weapon, w);
  applyLive();
  renderPanel();
  void dt;
}

// ── Public toggle ────────────────────────────────────────────────────────────

export function setGripEditorActive(on: boolean): void {
  if (on === active) return;
  active = on;
  if (on) {
    buildPanel();
    applyLive();
    // Own the keyboard: editor keys are captured before the engine sees them.
    installCapture();
    prev.clear();
    console.info(
      '[grip-editor] ON — o jogo ignora as teclas do editor. Tab pos/rot; setas ajustam; N arma; X exporta; Esc sai.'
    );
  } else {
    disposePanel();
    removeCapture();
    // Hand the held-item channel back (AttackContextSystem resumes next frame).
    setPlayerHeldItem(null);
  }
}

/**
 * Poll Shift+G as a quick toggle while INACTIVE (while active the editor's
 * capture handler owns KeyG and tickGripEditor closes it locally). Call from
 * a simulation system every frame.
 */
export function updateGripEditor(state: State): void {
  if (!active) {
    const g =
      isKeyDown('KeyG') && (isKeyDown('ShiftLeft') || isKeyDown('ShiftRight'));
    if (g && !prev.has('__toggle')) {
      prev.add('__toggle');
      setGripEditorActive(true);
    } else if (!g) {
      prev.delete('__toggle');
    }
    return;
  }
  tickGripEditor(state.time.deltaTime);
  // Release the local toggle edge when Shift+G is let go.
  if (!keys.has('KeyG')) prev.delete('__toggleLocal');
  void getAnimator; // reserved for live-pose helpers
}

/** Teardown (HMR). */
export function clearGripEditor(): void {
  active = false;
  disposePanel();
  removeCapture();
  edited.clear();
  prev.clear();
  keys.clear();
}
