import { getDataRegistry } from '../../rpg-core';
import type { ItemDef } from '../../rpg-core/types';
import type { State } from '../../../core';
import { InventoryComponent, getInventory } from '../../rpg-inventory';
import { t } from '../../i18n/utils';
import { injectWidgetCss } from './shared';
import type { TabContent } from './tabbed-modal-shared';

export interface InventoryTabConfig {
  targetEntity: number;
  columns?: number;
}

const SLOT_BASE =
  'aspect-ratio:1;border-radius:10px;position:relative;' +
  'display:flex;align-items:center;justify-content:center;';
const SLOT_EMPTY =
  'background:rgba(255,255,255,0.035);border:1px solid rgba(130,160,230,0.14);';
const SLOT_FILLED =
  'background:linear-gradient(160deg,rgba(40,52,82,0.85),rgba(24,32,52,0.85));' +
  'border:1px solid rgba(150,180,240,0.35);box-shadow:inset 0 1px 2px rgba(255,255,255,0.07);';

const INV_CSS = `
.hud-modal-inv-qty{position:absolute;right:4px;bottom:3px;font:800 12px system-ui,Segoe UI,sans-serif;color:#fff;text-shadow:0 1px 2px rgba(0,0,0,0.9);}
.hud-modal-inv-empty{text-align:center;color:#7c8aa8;font:600 13px system-ui,Segoe UI,sans-serif;padding:24px 0;}
`;

export function createInventoryTab(
  state: State,
  cfg: InventoryTabConfig
): TabContent {
  injectWidgetCss(INV_CSS);

  const root = document.createElement('div');
  root.className = 'hud-modal-inventory';

  const grid = document.createElement('div');
  grid.style.cssText = `display:grid;grid-template-columns:repeat(${cfg.columns ?? 5},1fr);gap:8px;`;
  root.appendChild(grid);

  const emptyMsg = document.createElement('div');
  emptyMsg.className = 'hud-modal-inv-empty';
  emptyMsg.textContent = t(state, 'modal.inventoryEmpty');
  root.appendChild(emptyMsg);

  function refresh(): void {
    grid.textContent = '';
    const stacks = getInventory(state, cfg.targetEntity);
    const capacity =
      InventoryComponent.capacity[cfg.targetEntity] ?? stacks.length;
    const total = Math.max(capacity, stacks.length);

    emptyMsg.style.display = stacks.length === 0 ? 'block' : 'none';

    const registry = getDataRegistry(state);
    for (let i = 0; i < total; i++) {
      const slot = document.createElement('div');
      const stack = stacks[i];
      if (!stack) {
        slot.style.cssText = SLOT_BASE + SLOT_EMPTY;
      } else {
        const def = registry.get<ItemDef>('item', stack.itemId);
        slot.style.cssText = SLOT_BASE + SLOT_FILLED;
        slot.title = `${def?.name ?? stack.itemId} ×${stack.qty}`;
        const icon = document.createElement('div');
        icon.textContent = def?.icon ?? '◆';
        icon.style.cssText = 'font-size:26px;line-height:1;';
        const qty = document.createElement('div');
        qty.className = 'hud-modal-inv-qty';
        qty.textContent = String(stack.qty);
        slot.append(icon, qty);
      }
      grid.appendChild(slot);
    }
  }

  refresh();

  return { root, refresh };
}
