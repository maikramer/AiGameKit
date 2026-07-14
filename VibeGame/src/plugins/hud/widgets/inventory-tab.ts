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
  'display:flex;align-items:center;justify-content:center;cursor:pointer;pointer-events:auto;';
const SLOT_EMPTY =
  'background:rgba(255,255,255,0.035);border:1px solid rgba(130,160,230,0.14);';
const SLOT_FILLED =
  'background:linear-gradient(160deg,rgba(40,52,82,0.85),rgba(24,32,52,0.85));' +
  'border:1px solid rgba(150,180,240,0.35);box-shadow:inset 0 1px 2px rgba(255,255,255,0.07);';
const SLOT_SELECTED =
  'outline:2px solid rgba(201,176,122,0.75);outline-offset:1px;';

const INV_CSS = `
.hud-modal-inventory{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(140px,0.85fr);gap:14px;align-items:start;}
.hud-modal-inv-grid{display:grid;gap:8px;}
.hud-modal-inv-qty{position:absolute;right:4px;bottom:3px;font:800 12px "Segoe UI",system-ui,sans-serif;color:#fff;text-shadow:0 1px 2px rgba(0,0,0,0.9);}
.hud-modal-inv-empty{text-align:center;color:#7c8aa8;font:600 13px "Segoe UI",system-ui,sans-serif;padding:24px 0;grid-column:1/-1;}
.hud-modal-inv-detail{min-height:160px;padding:14px 14px 12px;border-radius:12px;background:linear-gradient(165deg,rgba(28,36,56,0.9),rgba(14,18,30,0.95));border:1px solid rgba(160,180,220,0.2);box-shadow:inset 0 1px 0 rgba(255,255,255,0.05);}
.hud-modal-inv-detail-title{font:800 15px "Segoe UI",system-ui,sans-serif;color:#eef2fb;margin-bottom:6px;}
.hud-modal-inv-detail-qty{font:700 12px "Segoe UI",system-ui,sans-serif;color:#c9b07a;margin-bottom:8px;}
.hud-modal-inv-detail-desc{font:500 12px/1.5 "Segoe UI",system-ui,sans-serif;color:#9aabcd;white-space:pre-wrap;}
.hud-modal-inv-detail-tags{display:flex;flex-wrap:wrap;gap:4px;margin-top:10px;}
.hud-modal-inv-tag{font:700 9px "Segoe UI",system-ui,sans-serif;letter-spacing:0.4px;text-transform:uppercase;padding:3px 7px;border-radius:999px;background:rgba(130,160,230,0.14);color:#b8c6e0;border:1px solid rgba(130,160,230,0.2);}
.hud-modal-inv-detail-empty{color:#7c8aa8;font:600 12px "Segoe UI",system-ui,sans-serif;}
`;

export function createInventoryTab(
  state: State,
  cfg: InventoryTabConfig
): TabContent {
  injectWidgetCss(INV_CSS);

  const root = document.createElement('div');
  root.className = 'hud-modal-inventory';

  const grid = document.createElement('div');
  grid.className = 'hud-modal-inv-grid';
  grid.style.gridTemplateColumns = `repeat(${cfg.columns ?? 5},1fr)`;

  const emptyMsg = document.createElement('div');
  emptyMsg.className = 'hud-modal-inv-empty';
  emptyMsg.textContent = t(state, 'modal.inventoryEmpty');

  const detail = document.createElement('div');
  detail.className = 'hud-modal-inv-detail';

  root.append(grid, detail);

  let lastSig = '';
  let selectedSlot = -1;

  function renderDetail(itemId: string | null, qty: number): void {
    detail.textContent = '';
    if (!itemId) {
      const empty = document.createElement('div');
      empty.className = 'hud-modal-inv-detail-empty';
      empty.textContent = t(state, 'modal.inventorySelect');
      detail.appendChild(empty);
      return;
    }
    const def = getDataRegistry(state).get<ItemDef>('item', itemId);
    const title = document.createElement('div');
    title.className = 'hud-modal-inv-detail-title';
    title.textContent = def?.name ?? itemId;
    const qtyEl = document.createElement('div');
    qtyEl.className = 'hud-modal-inv-detail-qty';
    qtyEl.textContent = `×${qty}`;
    const desc = document.createElement('div');
    desc.className = 'hud-modal-inv-detail-desc';
    desc.textContent = def?.description || t(state, 'modal.inventoryNoDesc');
    detail.append(title, qtyEl, desc);
    if (def?.tags && def.tags.length > 0) {
      const tags = document.createElement('div');
      tags.className = 'hud-modal-inv-detail-tags';
      for (const tag of def.tags) {
        const chip = document.createElement('span');
        chip.className = 'hud-modal-inv-tag';
        chip.textContent = tag;
        tags.appendChild(chip);
      }
      detail.appendChild(tags);
    }
  }

  function refresh(): void {
    const stacks = getInventory(state, cfg.targetEntity);
    const capacity =
      InventoryComponent.capacity[cfg.targetEntity] ?? stacks.length;
    const total = Math.max(capacity, stacks.length);
    let sig = `${total}|${stacks.length}|${selectedSlot}`;
    for (let i = 0; i < stacks.length; i++) {
      const s = stacks[i]!;
      sig += `|${s.itemId}:${s.qty}`;
    }
    if (sig === lastSig) return;
    lastSig = sig;

    grid.textContent = '';
    emptyMsg.style.display = stacks.length === 0 ? 'block' : 'none';
    if (stacks.length === 0) {
      grid.appendChild(emptyMsg);
      selectedSlot = -1;
      renderDetail(null, 0);
      return;
    }

    const registry = getDataRegistry(state);
    for (let i = 0; i < total; i++) {
      const slot = document.createElement('div');
      const stack = stacks[i];
      if (!stack) {
        slot.style.cssText = SLOT_BASE + SLOT_EMPTY;
        slot.addEventListener('click', () => {
          selectedSlot = -1;
          lastSig = '';
          refresh();
        });
      } else {
        const def = registry.get<ItemDef>('item', stack.itemId);
        const selected = i === selectedSlot ? SLOT_SELECTED : '';
        slot.style.cssText = SLOT_BASE + SLOT_FILLED + selected;
        slot.title = `${def?.name ?? stack.itemId} ×${stack.qty}`;
        const icon = document.createElement('div');
        const iconStr = def?.icon ?? '◆';
        if (
          /\.(png|jpe?g|webp|svg|gif)$/i.test(iconStr) &&
          iconStr.includes('/')
        ) {
          const img = document.createElement('img');
          img.src = iconStr;
          img.alt = def?.name ?? '';
          img.style.cssText = 'width:48px;height:48px;object-fit:contain;';
          icon.append(img);
        } else {
          icon.textContent = iconStr;
          icon.style.cssText = 'font-size:26px;line-height:1;';
        }
        const qty = document.createElement('div');
        qty.className = 'hud-modal-inv-qty';
        qty.textContent = String(stack.qty);
        slot.append(icon, qty);
        const slotIndex = i;
        slot.addEventListener('click', () => {
          selectedSlot = slotIndex;
          lastSig = '';
          refresh();
        });
      }
      grid.appendChild(slot);
    }

    if (selectedSlot >= 0 && stacks[selectedSlot]) {
      const s = stacks[selectedSlot]!;
      renderDetail(s.itemId, s.qty);
    } else if (stacks[0]) {
      selectedSlot = 0;
      lastSig = '';
      refresh();
      return;
    } else {
      renderDetail(null, 0);
    }
  }

  refresh();

  return { root, refresh };
}
