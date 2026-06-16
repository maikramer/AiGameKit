import {
  getInventory,
  INVENTORY_CAPACITY,
  type InventoryEntry,
} from '../game/inventory';

export interface InventoryPanel {
  root: HTMLElement;
  refresh: (locale: 'en' | 'pt') => void;
}

const SLOT_BASE =
  'aspect-ratio:1;border-radius:10px;position:relative;' +
  'display:flex;align-items:center;justify-content:center;';
const SLOT_EMPTY =
  'background:rgba(255,255,255,0.035);border:1px solid rgba(130,160,230,0.14);';
const SLOT_FILLED =
  'background:linear-gradient(160deg,rgba(40,52,82,0.85),rgba(24,32,52,0.85));' +
  'border:1px solid rgba(150,180,240,0.35);box-shadow:inset 0 1px 2px rgba(255,255,255,0.07);';

/** Grid-of-slots inventory view. Reads the live bag each refresh and fills the
 * remaining slots empty up to INVENTORY_CAPACITY. */
export function createInventoryPanel(): InventoryPanel {
  const root = document.createElement('div');
  const grid = document.createElement('div');
  grid.style.cssText =
    'display:grid;grid-template-columns:repeat(5,1fr);gap:8px;';
  root.appendChild(grid);

  function fillSlot(
    slot: HTMLDivElement,
    e: InventoryEntry,
    locale: 'en' | 'pt'
  ): void {
    slot.style.cssText = SLOT_BASE + SLOT_FILLED;
    slot.title = `${e.name[locale]} ×${e.qty}`;
    const icon = document.createElement('div');
    icon.textContent = e.icon;
    icon.style.cssText = 'font-size:26px;line-height:1;';
    const qty = document.createElement('div');
    qty.textContent = String(e.qty);
    qty.style.cssText =
      'position:absolute;right:4px;bottom:3px;font:800 12px system-ui;' +
      'color:#fff;text-shadow:0 1px 2px rgba(0,0,0,0.9);';
    slot.append(icon, qty);
  }

  const refresh = (locale: 'en' | 'pt'): void => {
    grid.textContent = '';
    const entries = getInventory();
    const total = Math.max(INVENTORY_CAPACITY, entries.length);
    for (let i = 0; i < total; i++) {
      const slot = document.createElement('div');
      const e = entries[i];
      if (e) fillSlot(slot, e, locale);
      else slot.style.cssText = SLOT_BASE + SLOT_EMPTY;
      grid.appendChild(slot);
    }
  };

  return { root, refresh };
}
