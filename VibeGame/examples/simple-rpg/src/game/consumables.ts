// Consumable hotbar. Buying a potion/antidote/bomb now drops it in the bag
// (engine InventoryComponent, also shown in the pause-menu InventoryTab). This
// module renders a bottom-centre quick-use bar and applies the use effect:
//   [1] Potion   → heal
//   [2] Antidote → cure + small heal
//   [3] Bomb     → thrown with [B] (BombSystem owns it; slot is display-only)
import {
  Health,
  cancelAllStatuses,
  getItemQty,
  healHealth,
  isKeyDown,
  playSound,
  removeItem,
} from 'vibegame';
import type { State } from 'vibegame';
import { isGamePaused } from './pause';

export const POTION_HEAL = 50;
export const ANTIDOTE_HEAL = 35;

interface Slot {
  id: string;
  key: string;
  keyCode: string;
  icon: string;
  color: string;
  label: string;
}

// Bomb's keyCode is KeyB to match the BombSystem; this bar doesn't act on it
// (throw aiming lives in BombSystem), it only shows the count.
const SLOTS: readonly Slot[] = [
  {
    id: 'potion',
    key: '1',
    keyCode: 'Digit1',
    icon: '🧪',
    color: '#7ad27a',
    label: 'Potion — restore HP',
  },
  {
    id: 'antidote',
    key: '2',
    keyCode: 'Digit2',
    icon: '🟣',
    color: '#c08af0',
    label: 'Antidote — cure + heal',
  },
  {
    id: 'bomb',
    key: 'B',
    keyCode: 'KeyB',
    icon: '💣',
    color: '#ff8a6a',
    label: 'Bomb — hold [B] to throw',
  },
];

const pressed: Record<string, boolean> = {};
let hotbarEl: HTMLDivElement | null = null;
const slotEls: Record<
  string,
  { root: HTMLDivElement; count: HTMLSpanElement }
> = {};

function buildHotbar(): void {
  if (hotbarEl || typeof document === 'undefined') return;
  const layer =
    document.querySelector('.vibe-hud-screen-layer') ?? document.body;

  hotbarEl = document.createElement('div');
  hotbarEl.style.cssText =
    'position:absolute;bottom:18px;left:50%;transform:translateX(-50%);z-index:12;' +
    'display:flex;gap:10px;pointer-events:none;';

  for (const s of SLOTS) {
    const root = document.createElement('div');
    root.style.cssText =
      'position:relative;width:54px;height:54px;border-radius:11px;' +
      'display:flex;align-items:center;justify-content:center;font-size:26px;line-height:1;' +
      `border:1px solid ${s.color}55;` +
      'background:linear-gradient(135deg,rgba(14,18,34,0.78),rgba(10,14,26,0.66));' +
      'backdrop-filter:blur(10px);box-shadow:0 5px 18px rgba(0,0,0,0.3);transition:transform 0.08s,border-color 0.12s;' +
      'pointer-events:auto;';
    root.textContent = s.icon;
    root.title = `[${s.key}] ${s.label}`;

    const keyBadge = document.createElement('span');
    keyBadge.textContent = s.key;
    keyBadge.style.cssText =
      'position:absolute;top:-7px;left:-7px;min-width:17px;height:17px;padding:0 4px;' +
      'border-radius:5px;background:#1b2238;color:#cfe;border:1px solid rgba(255,255,255,0.18);' +
      'font:800 11px system-ui,sans-serif;display:flex;align-items:center;justify-content:center;';

    const count = document.createElement('span');
    count.style.cssText =
      'position:absolute;right:3px;bottom:2px;min-width:16px;height:16px;padding:0 3px;' +
      'border-radius:5px;background:rgba(0,0,0,0.55);' +
      'font:800 12px system-ui,sans-serif;color:#fff;text-shadow:0 1px 2px #000;' +
      'display:flex;align-items:center;justify-content:center;';

    root.append(keyBadge, count);
    hotbarEl.appendChild(root);
    slotEls[s.id] = { root, count };
  }
  layer.appendChild(hotbarEl);
}

function flash(id: string): void {
  const el = slotEls[id];
  if (!el) return;
  el.root.style.transform = 'scale(1.18)';
  el.root.style.borderColor = '#fff';
  setTimeout(() => {
    if (!el) return;
    el.root.style.transform = 'scale(1)';
    el.root.style.borderColor = '';
  }, 120);
}

/** Apply a consumable's effect, consuming one from the bag. Returns true if used. */
export function useConsumable(state: State, hero: number, id: string): boolean {
  if (hero <= 0 || getItemQty(state, hero, id) <= 0) return false;

  if (id === 'potion') {
    const max = Health.max[hero] ?? 0;
    if (max > 0 && (Health.current[hero] ?? 0) >= max) return false; // don't waste at full HP
    removeItem(state, hero, id, 1);
    healHealth(hero, POTION_HEAL);
    playSound('heal');
    flash(id);
    return true;
  }

  if (id === 'antidote') {
    removeItem(state, hero, id, 1);
    cancelAllStatuses(state, hero); // cure poison/debuffs, not just heal
    healHealth(hero, ANTIDOTE_HEAL);
    playSound('heal');
    flash(id);
    return true;
  }

  return false; // bomb: thrown by BombSystem, not used from the bar
}

/** Poll hotbar keys + refresh slot counts. Call once per frame with the hero. */
export function updateConsumables(state: State, hero: number): void {
  buildHotbar();

  if (!isGamePaused() && hero > 0) {
    for (const s of SLOTS) {
      if (s.id === 'bomb') continue; // [B] handled by BombSystem
      const down = isKeyDown(s.keyCode);
      if (down && !pressed[s.keyCode]) useConsumable(state, hero, s.id);
      pressed[s.keyCode] = down;
    }
  }

  for (const s of SLOTS) {
    const el = slotEls[s.id];
    if (!el) continue;
    const q = hero > 0 ? getItemQty(state, hero, s.id) : 0;
    el.count.textContent = String(q);
    el.root.style.opacity = q > 0 ? '1' : '0.42';
  }
}

/** HMR/teardown cleanup. */
export function clearHotbar(): void {
  hotbarEl?.remove();
  hotbarEl = null;
  for (const k of Object.keys(slotEls)) delete slotEls[k];
}
